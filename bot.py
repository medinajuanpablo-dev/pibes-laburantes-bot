#!/usr/bin/env python3
"""Telegram meme bot.

Someone pastes a YouTube / Instagram / Facebook link in the group; the bot
replies with the media so nobody has to leave the chat.

Scale: one group, ~20 links a week. Every decision here follows from that.
Run it with `python bot.py`; run its self-check with `python bot.py --self-check`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import telegram
import yt_dlp
from telegram.ext import Application, CommandHandler, MessageHandler, filters

log = logging.getLogger("the-bot")

TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
COOKIES_ENV_VAR = "YTDLP_COOKIES"

# Telegram's bot-upload ceiling, verified against
# https://core.telegram.org/bots/api#sending-files on 2026-08-07:
# "10 MB max size for photos, 50 MB for other files."
TELEGRAM_MAX_UPLOAD = 50 * 1024 * 1024
TELEGRAM_MAX_PHOTO_UPLOAD = 10 * 1024 * 1024

# Format preference, ordered for *inline playability first, bytes second*. Telegram
# renders H.264/AAC in an mp4 as a real video; anything else risks arriving as a grey
# file row, which defeats the purpose of the bot.
#
#   1. A ≤720p H.264 video stream merged with an AAC audio stream. YouTube's case.
#   2. A ready-made single-file mp4 the site serves itself. Instagram and Facebook
#      both expose one, with every field ("height", "vcodec") reported as unknown --
#      hence `height<=?720`, where the `?` lets an unknown height through. No ffmpeg
#      merge is needed for these.
#   3. Any ≤720p merge, 4. anything at all. Safety nets, never hit by the three sites.
#
# Measured on 2026-08-07, all three anonymous, no cookies, downloaded and ffprobed:
#   YouTube  dQw4w9WgXcQ  -> 136+140  1280x720 h264/aac  29,969,207 B  (ffmpeg merge)
#   Instagram reel        -> 3        772x720  h264/aac   1,272,833 B  (no merge)
#   Facebook reel         -> hd       720x900  h264/aac   1,881,291 B  (no merge)
#
# Note branch 1 is what costs bytes on YouTube: the codec-agnostic `bv*[height<=720]+ba`
# picks AV1+Opus at ~21 MB instead of H.264's ~29 MB. The extra 9 MB buys a clip that
# plays; the ceiling is 50 MB, so there is room to pay it.
#
# ponytail: 720p cap. Raising it is NOT free at this codec preference: the same
# YouTube video at `height<=1080` measures 1920x1080 H.264 at ~84 MB, over the 50 MB
# ceiling, where the AV1 equivalent is ~34 MB. So 1080p would mean either accepting
# AV1's playability risk or having long clips fall through to the link fallback.
# Leave it at 720 unless the group actually complains.
# ponytail: the height cap is NOT a size guarantee. Branch 2 accepts unknown heights
# by design, and portrait video (the Facebook clip is 720x900) can exceed 720 in the
# long dimension while passing a `height<=720` filter or skipping it entirely. The
# only real size guard is the byte count of the finished file -- see delivery_decision.
MEDIA_FORMAT = (
    "bv*[height<=720][vcodec^=avc1]+ba[acodec^=mp4a]/"
    "b[ext=mp4][height<=?720]/"
    "bv*[height<=720]+ba/"
    "b/bv*+ba"
)

SOCKET_TIMEOUT = 20  # seconds. Note: timeout(1) does not exist on macOS; this is the real knob.

# python-telegram-bot's default media write timeout is 20 s, which no real video
# meets. This value has to cover a whole upload, not one socket write: PTB reads the
# file into `bytes` (telegram/_files/inputfile.py), httpx yields a bytes body as a
# single chunk (httpx/_multipart.py FileField.render_data), and httpcore applies the
# write timeout per chunk -- so for this code path one chunk is the entire file and
# the write timeout is an effective whole-upload deadline. Verified by reading those
# three sources on 2026-08-07, because the per-chunk reading of the same code would
# have made this constant harmless and the fix unnecessary.
#
# Measured: two real uploads of the same 29,969,207-byte file took 216 s and 128 s,
# i.e. a slow-day rate of ~139 kB/s. The 50 MiB ceiling needs ~378 s at that rate, so
# the previous 300 s promised a size the code could not deliver and failed as a bare
# "no pude bajar ese link". 600 s covers the full ceiling down to ~87 kB/s.
#
# ponytail: 600 s of a blocked handler is the cost, since Application processes
# updates sequentially by default. Fine at 20 links a week. If it ever bites, the
# upgrade path is handing PTB an open file object instead of a Path -- httpx then
# streams at 64 KiB and this stops being a whole-upload deadline (it also stops
# holding the whole file in RAM).
UPLOAD_TIMEOUT = 600  # seconds

# Establishing the connection is a separate timeout from transferring over it, and
# python-telegram-bot defaults it to 5 s. Passing write_timeout and read_timeout does
# not touch it, so a generous upload timeout is worth nothing when the TCP+TLS
# handshake is the thing that gives up.
#
# ponytail: 30 s, sized for a bad mobile connection rather than measured -- the only
# evidence is failures. Measured on a live group: a 1,272,833-byte Instagram reel
# failed twice, three minutes apart, each time ~5 s after the upload began, with
# telegram.error.TimedOut -- exactly what an httpx.ConnectTimeout surfaces as, and
# exactly PTB's 5 s default. Other reels of the same size and a 17.6 MB video
# succeeded in the same session, so this is a flaky handshake, not a slow transfer.
# Raise it if TimedOut recurs at 30 s.
CONNECT_TIMEOUT = 30  # seconds

# Short text replies carry no upload, so a minute is already generous.
TEXT_REPLY_TIMEOUT = 60  # seconds

# Telegram allows exactly one getUpdates poller per token; a second one makes the
# loser of the race see HTTP 409 "Conflict: terminated by other getUpdates request".
# That is a normal event here rather than a bug: run-bot.command hands the bot from
# one friend to the next, and its "is anybody running?" probe is itself a competing
# getUpdates call, so a short conflict happens every time somebody merely checks.
#
# Two numbers, and the gap between them is the whole design:
#
# GRACE is how long a conflict must last before this instance accepts it has lost
#   the baton. It must outlast a probe. Measured before this change: one competing
#   call produced conflicts for about ten seconds, so 60 s is six times the blip.
# EPISODE_GAP is the silence that separates one conflict from the next. It has to be
#   larger than python-telegram-bot's own retry backoff, which grows by 1.5x and is
#   capped at 30 s (telegram/ext/_utils/networkloop.py) -- otherwise a long conflict
#   would look like a series of unrelated new ones and never reach GRACE.
CONFLICT_GRACE = 60.0  # seconds
CONFLICT_EPISODE_GAP = 45.0  # seconds

# The three sites the group actually pastes. Anything else is left alone rather than
# attempted and apologised for -- a bot that answers "no pude bajar ese link" to every
# news article in the chat is worse than one that stays quiet.
# ponytail: hardcoded host list; add a host here when the group starts pasting a
# fourth site. Not worth a config file at 20 links a week.
SUPPORTED_HOSTS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
        "instagram.com",
        "instagr.am",
        "facebook.com",
        "fb.watch",
        "fb.com",
    }
)

# Only scheme-carrying URLs count. A bare "instagram.com" mentioned mid-sentence is
# someone talking about a site, not a link to fetch.
# ponytail: this misses schemeless pastes like "youtu.be/xyz" that Telegram itself
# renders as links. Upgrade path if that turns out to annoy the group: read
# message.entities instead of the raw text and let Telegram decide what a link is.
URL_PATTERN = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.IGNORECASE)

# Trailing punctuation a human types after a pasted link: "mira esto https://x.com/y."
URL_TRAILING_JUNK = ".,;:!?'\"»)]}"

PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"})
ANIMATION_SUFFIXES = frozenset({".gif"})

# Media kind -> the python-telegram-bot Message method that renders it inline.
# reply_document is deliberately absent: a document shows up as a grey file row.
REPLY_METHODS = {
    "video": "reply_video",
    "photo": "reply_photo",
    "animation": "reply_animation",
}

FAILURE_REPLY = "no pude bajar ese link"

# Every supported link that does not end in delivered media is written here, one
# JSON object per line, so the owner can ask "analizá todos los rebotados" later
# instead of asking whichever friend was hosting to dig through a lost terminal.
# Next to bot.py rather than in a config dir: this file IS the bot's directory on
# every host, and .gitignore keeps the group's content out of git.
#
# ponytail: with a rotating host the ledger FRAGMENTS -- each friend's machine
# records only the bounces it saw, and nothing merges them. That is the accepted
# ceiling: at ~20 links a week, the owner reading his own file and asking a friend
# to send theirs costs less than any sync would. Upgrade path, cheapest first:
# ask each friend to send their rejected.jsonl and concatenate them (the format is
# append-only lines, so `cat` is the merge); and only if that stops working, move
# the bot to one always-on host. Not a server, not a database, not a sync loop.
REJECTED_LEDGER = Path(__file__).resolve().parent / "rejected.jsonl"

# A yt-dlp DownloadError message can be several hundred characters of URL and
# advice. The first line is the diagnosis; the rest is noise in a ledger.
REJECTED_DETAIL_LIMIT = 400

# The `error` field of a record is an exception class name wherever there is an
# exception. The oversize path has none -- nothing failed, the file simply does not
# fit -- so it gets this token instead, which groups the same way.
OVERSIZE_ERROR = "OversizeForTelegram"

# `filters.TEXT | filters.CAPTION` on its own is not "new messages": MessageFilter
# tests Update.effective_message, which resolves to `edited_message` when that is
# what arrived, and to `channel_post` for a channel. So editing a typo in a message
# containing a link would make the bot download and upload the whole video again.
# filters.UpdateType.MESSAGE narrows it to updates carrying Update.message.
MESSAGE_FILTER = filters.UpdateType.MESSAGE & (filters.TEXT | filters.CAPTION)


class ExtractionError(Exception):
    """The media could not be downloaded. The message is for the log, not the group."""


class Media(NamedTuple):
    path: Path
    has_audio: bool
    direct_url: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None


def read_token() -> str:
    """Return the bot token from the environment, or exit with a clear message.

    The token is never stored in this repository: not in a default, not in an
    example, not in a comment. The environment variable is the whole contract.
    """
    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if not token:
        sys.exit(
            f"{TOKEN_ENV_VAR} is not set.\n"
            f"Get a token from @BotFather and start the bot with:\n"
            f"    {TOKEN_ENV_VAR}=<your-token> python bot.py\n"
            f"See README.md for the full setup, including the BotFather "
            f"privacy-mode step the bot silently needs."
        )
    return token


def cookie_file() -> str | None:
    """Path to a Netscape-format cookie file, if the operator supplied one.

    ponytail: with no cookie file, Instagram is expected to fail -- it wants an
    authenticated session for essentially everything. Upgrade path: export cookies
    from a throwaway account's browser session to a file and point YTDLP_COOKIES
    at it; no code changes needed. The file must stay out of git (.gitignore
    already covers cookies.txt and *.cookies.txt).
    """
    path = os.environ.get(COOKIES_ENV_VAR, "").strip()
    if path and os.path.isfile(path):
        return path
    if path:
        log.warning("%s is set to %r but no such file exists; ignoring it", COOKIES_ENV_VAR, path)
    return None


def ffmpeg_path() -> str | None:
    """Resolve ffmpeg from PATH. Never hardcoded -- this gets copied to Linux."""
    return shutil.which("ffmpeg")


def _ydl_options(target_dir: Path) -> dict:
    options = {
        "format": MEDIA_FORMAT,
        "outtmpl": str(target_dir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "playlist_items": "1",
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 3,
        "quiet": True,
        "noprogress": True,
        # Warnings stay on: the "no JavaScript runtime" one is an early signal that
        # YouTube extraction is about to rot. See the Operations section of README.md.
        "no_warnings": False,
        "logger": log,
    }
    cookies = cookie_file()
    if cookies:
        options["cookiefile"] = cookies
    return options


def _no_formats_ok(target_dir: Path) -> dict:
    """`_ydl_options` plus tolerance for a post that has no video in it.

    `ignore_no_formats_error` only works while *not* downloading. On the download
    path yt-dlp calls `raise_no_formats(info, forced=True)`, and the `forced` arm
    raises whatever the flag says -- so this cannot be folded into the normal
    options to save a round trip, and the image fallback has to probe separately.
    """
    return _ydl_options(target_dir) | {"ignore_no_formats_error": True, "simulate": True}


@contextmanager
def temp_workspace() -> Iterator[Path]:
    """A scratch directory that is removed on the way out, success or failure."""
    target_dir = Path(tempfile.mkdtemp(prefix="the-bot-"))
    try:
        yield target_dir
    finally:
        shutil.rmtree(target_dir, ignore_errors=True)


def download_into(url: str, target_dir: Path) -> Media:
    """Download `url` into `target_dir`. Blocking. Nothing here knows about Telegram.

    Raises ExtractionError if the download produced no usable file. Split out from
    the context manager below so the Telegram layer can run it off the event loop
    while still holding the file open for the upload.
    """
    try:
        with yt_dlp.YoutubeDL(_ydl_options(target_dir)) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        # The video path above is untouched: this runs only after it has already
        # failed. "No video in this post" is not necessarily a failure -- an
        # Instagram image post lands here and can still be delivered as a photo.
        image = _image_fallback(url, target_dir)
        if image is not None:
            return image
        raise ExtractionError(f"yt-dlp could not download {url}: {exc}") from exc

    path = _downloaded_path(info, target_dir)
    if path is None:
        raise ExtractionError(f"yt-dlp reported success but left no file for {url}")
    if path.stat().st_size == 0:
        raise ExtractionError(f"yt-dlp left an empty file for {url}")
    width, height, duration = _dimensions(info)
    return Media(
        path=path,
        has_audio=_has_audio(info),
        direct_url=_direct_url(info),
        width=width,
        height=height,
        duration=duration,
    )


@contextmanager
def downloaded_media(url: str) -> Iterator[Media]:
    """Download `url` and yield it; the temp directory dies with the `with` block.

    A context manager rather than a plain function returning a path, because the
    caller needs the file to still exist while it uses it.
    """
    with temp_workspace() as target_dir:
        yield download_into(url, target_dir)


def is_image_post(info: dict | None) -> bool:
    """Whether this is a post with no video in it at all, only still images.

    The whole safety of the image fallback lives in this one function, so it is pure
    and asserted with plain dicts.

    `formats` is the discriminator, and the empty check comes FIRST on purpose: a post
    that offers video formats is a video, full stop. If its formats then fail to
    download, that is an error and the group gets the apology -- it must never
    degrade into silently sending the poster frame of a video somebody asked for.

    The other half of that guarantee is upstream, in yt-dlp: `ignore_no_formats_error`
    suppresses only the "No video formats" condition. Measured on 2026-08-07, an
    auth-walled reel and a bogus shortcode both still raised DownloadError with the
    flag set, so a failed extraction never reaches this function at all.

    Note what is NOT used here: `duration` is None for the image post AND for a
    working reel, and `title` is "Video by <author>" even for an image, because that
    is Instagram's generic caption. Neither discriminates anything.
    """
    if not info:
        return False
    if info.get("formats"):
        return False
    # ponytail: multi-item carousels are refused rather than guessed at. yt-dlp
    # models a carousel as a playlist of entries and its handling of mixed
    # photo/video ones is an open upstream problem (yt-dlp issues #7569, #11792).
    # No public carousel was available to measure on 2026-08-07, so the honest
    # behaviour is the apology, not an arbitrary slide. The reference image post
    # reports `entries: 0`, so this costs the measured case nothing. Upgrade path:
    # find a public carousel, measure what the entries actually look like, and only
    # then decide whether to send the first slide or all of them.
    if info.get("entries"):
        return False
    return bool(info.get("thumbnails"))


def _image_fallback(url: str, target_dir: Path) -> Media | None:
    """A Media photo if `url` is a post with no video in it, else None.

    Returning None means "not an image post" and the caller re-raises the original
    download failure, so a broken extraction stays an error and never degrades into
    a still image.
    """
    try:
        with yt_dlp.YoutubeDL(_no_formats_ok(target_dir)) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError:
        return None  # extraction is genuinely broken, not merely video-less
    if not is_image_post(info):
        return None
    log.info("%s has no video; sending its image instead", url)
    return Media(path=_download_best_thumbnail(url, target_dir), has_audio=False)


def _download_best_thumbnail(url: str, target_dir: Path) -> Path:
    """Fetch every thumbnail of an image post and keep the biggest file.

    Deliberately downloads all of them and picks by byte count on disk, because
    neither cheaper option is sound. Measured on the reference post, 2026-08-07:

      - The thumbnail entries carry NO width/height at all -- only `id` and `url` --
        so there is nothing to sort by in the metadata. (A reel's thumbnails DO carry
        dimensions, which is what makes this easy to assume and wrong.)
      - The list is not ordered worst-to-best. Its 13 entries run 1149k pixels at
        index 0, then 22k at index 1, climbing to 1283k at index 12: two interleaved
        ladders, square crops then aspect-correct. Taking the last entry happens to
        win on this post and is not a property you can rely on.

    Byte size stands in for resolution here: these are the same image re-encoded at
    one quality, so bytes track pixels (191,815 B = 1072x1197 beats 142,680 B =
    1072x1072). 13 small requests, once, for a post type that arrives a few times a
    week -- cheap enough not to out-think.
    """
    options = _no_formats_ok(target_dir) | {
        "simulate": False,
        "skip_download": True,
        "writethumbnail": True,
        "write_all_thumbnails": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise ExtractionError(f"yt-dlp could not fetch thumbnails for {url}: {exc}") from exc

    images = [
        path
        for path in target_dir.iterdir()
        if path.is_file() and path.suffix.lower() in PHOTO_SUFFIXES and path.stat().st_size > 0
    ]
    if not images:
        raise ExtractionError(f"{url} has no video and no downloadable image either")
    return max(images, key=lambda path: path.stat().st_size)


def _has_audio(info: dict | None) -> bool:
    """Whether the downloaded stream carries sound. Assume yes when unsure.

    A silent clip is what Telegram calls an *animation* (a GIF, essentially), and it
    renders differently from a video. Guessing wrong in that direction only costs a
    play button, so an unknown means "video".
    """
    if not info:
        return True
    entries = info.get("requested_downloads") or []
    acodec = entries[0].get("acodec") if entries else info.get("acodec")
    return acodec != "none"


def _dimensions(info: dict | None) -> tuple[int | None, int | None, float | None]:
    """Width, height and duration of the file that was actually written.

    Width and height come from `requested_downloads[0]`, the entry that describes the
    file on disk, rather than from the top of the info dict, which describes whatever
    yt-dlp last selected. Measured on 2026-08-07 the two agree everywhere they are
    populated (YouTube 1280x720 in both; Instagram and Facebook null in both under
    this format string), so this is a correct-by-construction preference, not a fix
    for an observed mismatch -- do not go looking for the bug it prevents.

    Duration is a property of the video rather than of the format, so falling back to
    the top level is both safe and necessary: YouTube and Facebook report it only
    there.

    Any of the three may be None. Instagram reports all three as unknown and Facebook
    reports only duration, so the caller must omit rather than substitute.
    """
    if not info:
        return None, None, None
    entry = (info.get("requested_downloads") or [{}])[0]
    width, height = entry.get("width"), entry.get("height")
    duration = entry.get("duration") or info.get("duration")
    return width, height, duration


def _direct_url(info: dict | None) -> str | None:
    """A single URL that points at the media itself, when one exists.

    Only offered for downloads that were one ready-made file. A merged download has
    two source URLs, one of them silent video and the other audio, and handing either
    to the group would be worse than handing them nothing.

    ponytail: these URLs are signed and expire in hours. Good enough for "watch it
    now"; the caller falls back to the page URL when this returns None.
    """
    if not info:
        return None
    entries = info.get("requested_downloads") or []
    if len(entries) != 1 or entries[0].get("requested_formats"):
        return None
    url = entries[0].get("url")
    return url if isinstance(url, str) and url.startswith("http") else None


def _downloaded_path(info: dict | None, target_dir: Path) -> Path | None:
    """Find the file yt-dlp actually wrote.

    `requested_downloads[0]["filepath"]` is the authoritative answer and accounts for
    the extension changing during an ffmpeg merge. Scanning the temp dir is the
    fallback for extractors that do not populate it.
    """
    if info:
        for entry in info.get("requested_downloads") or ():
            filepath = entry.get("filepath")
            if filepath and Path(filepath).is_file():
                return Path(filepath)
    files = [p for p in target_dir.iterdir() if p.is_file()]
    return max(files, key=lambda p: p.stat().st_size) if files else None


# --------------------------------------------------------------------------------
# Pure helpers. No network, no Telegram objects -- which is what makes the
# self-check below possible without a token.
# --------------------------------------------------------------------------------


def find_urls(text: str | None) -> list[str]:
    """Every http(s) URL in a message body, in order, de-duplicated.

    Requires a scheme on purpose: "eso lo vi en instagram.com" is someone naming a
    site, not asking for a download.
    """
    if not text:
        return []
    found: list[str] = []
    for match in URL_PATTERN.findall(text):
        url = match.rstrip(URL_TRAILING_JUNK)
        if url and url not in found:
            found.append(url)
    return found


def is_supported(url: str) -> bool:
    """Whether the URL points at one of the three sites the group actually pastes."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host in SUPPORTED_HOSTS or any(host.endswith("." + h) for h in SUPPORTED_HOSTS)


def media_kind(filename: str | Path, has_audio: bool = True) -> str:
    """Classify a downloaded file as "photo", "animation" or "video".

    Telegram's own taxonomy: an animation is a silent short clip or a GIF, and it
    plays looping without a play button. A video has sound and player controls.
    """
    suffix = Path(filename).suffix.lower()
    if suffix in PHOTO_SUFFIXES:
        return "photo"
    if suffix in ANIMATION_SUFFIXES:
        return "animation"
    return "video" if has_audio else "animation"


def reply_method_name(kind: str) -> str:
    """The telegram.Message method that renders `kind` inline."""
    try:
        return REPLY_METHODS[kind]
    except KeyError:
        raise ValueError(f"unknown media kind {kind!r}") from None


def upload_ceiling(kind: str) -> int:
    """Telegram's bot-upload ceiling for this media kind, in bytes."""
    return TELEGRAM_MAX_PHOTO_UPLOAD if kind == "photo" else TELEGRAM_MAX_UPLOAD


def delivery_decision(size_bytes: int, kind: str) -> str:
    """"file" if Telegram will accept the upload, "link" if it is too big.

    ponytail: this takes the real byte count of the finished file on disk, and it has
    to stay that way. yt-dlp's pre-download `filesize_approx` came back NA for both
    the Instagram and the Facebook clip on 2026-08-07, so any size logic built on the
    estimate would be dead code on two of the three sites. The cost is that an
    oversized video is downloaded before being rejected -- cheap at 20 links a week.
    """
    return "file" if size_bytes <= upload_ceiling(kind) else "link"


def video_kwargs(media: Media) -> dict:
    """Extra arguments for reply_video, omitting anything the extractor did not report.

    Without width/height/duration Telegram guesses, and it guesses badly: the same
    28.58 MB file came back as 320x320 / duration 0 without them and as 1280x720 /
    213 s with them (measured live, 2026-08-07). Never send zeros -- Instagram and
    Facebook report these fields as unknown, and a zero is a lie where an absent
    field is an honest "you work it out".
    """
    kwargs: dict = {"supports_streaming": True}
    if media.width and media.height:
        kwargs["width"] = int(media.width)
        kwargs["height"] = int(media.height)
    if media.duration:
        kwargs["duration"] = round(media.duration)
    return kwargs


def telegram_renders_inline(container: str, video_codec: str) -> bool:
    """Whether Telegram shows this file as a playable video or as a grey file row.

    Both arguments are ffprobe's own strings: `format.format_name` (a comma-separated
    list like "mov,mp4,m4a,3gp,3g2,mj2" or "matroska,webm") and a video stream's
    `codec_name` ("h264", "vp9", "av1").

    Measured against a live group on 2026-08-07, four real uploads:

        h264 + aac,  mp4   -> video
        vp9  + aac,  mp4   -> video
        h264 + aac,  mp4   -> video   (28.58 MB, the merged YouTube case)
        av01 + opus, webm  -> DOCUMENT

    So the rule is NOT "h264 only" -- vp9 in an mp4 played inline. What fails is
    webm/AV1. This is the property the whole format string exists to guarantee, and
    the self-check asserts it on every real download.
    """
    containers = {name.strip().lower() for name in container.split(",")}
    return "mp4" in containers and "av1" not in video_codec.lower()


def oversize_reply(size_bytes: int, link: str) -> str:
    """The Spanish message sent instead of a file that will not fit."""
    megabytes = size_bytes / 1024 / 1024
    return f"pesa {megabytes:.0f} MB y Telegram no me deja subirlo. Te lo dejo acá: {link}"


def conflict_action(
    now: float, started: float | None, last: float | None
) -> tuple[str, float | None, float | None]:
    """Decide what a poll conflict at `now` means, given the episode so far.

    Returns the action and the new (started, last) pair, so the whole rule is one
    pure function that can be asserted without a network or a second bot:

    * `announce` -- the first conflict of an episode. Say it once.
    * `quiet`    -- a repeat inside the same episode. python-telegram-bot retries on
      a growing backoff, so a single competing poller produces a stream of these;
      logging each one is how the old behaviour became a wall of text.
    * `give-up`  -- the episode has lasted CONFLICT_GRACE. Somebody really has taken
      the baton and this instance is no longer receiving anything.

    The episode ends after CONFLICT_EPISODE_GAP of silence. Without that reset, two
    unrelated probes an hour apart would look like one hour-long conflict and the
    second one would kill a perfectly healthy bot.
    """
    if started is None or last is None or now - last > CONFLICT_EPISODE_GAP:
        return "announce", now, now
    if now - started >= CONFLICT_GRACE:
        return "give-up", None, None
    return "quiet", started, now


# --------------------------------------------------------------------------------
# The rejected-links ledger. Diagnostics, never a dependency of delivery.
# --------------------------------------------------------------------------------


def rejection_record(
    url: str,
    error: str,
    detail: str,
    chat_id: int | None,
    message_id: int | None,
    when: str,
) -> dict:
    """One ledger line as a plain dict. Pure, so the shape is asserted without a file.

    What is deliberately NOT in here: the message body. This is a private group and
    the diagnosis is the URL, exactly like the ignore-logging in on_message. The
    detail is the error text, truncated -- a yt-dlp error carries the whole login
    advice and a signed URL, and none of that is worth 800 characters a line.
    """
    first_line = (detail or "").strip().splitlines()[0] if (detail or "").strip() else ""
    if len(first_line) > REJECTED_DETAIL_LIMIT:
        first_line = first_line[:REJECTED_DETAIL_LIMIT] + "..."
    return {
        "when": when,
        "chat_id": chat_id,
        "message_id": message_id,
        "url": url,
        "error": error,
        "detail": first_line,
    }


def record_rejection(
    message: telegram.Message, url: str, error: str, detail: str, path: Path | None = None
) -> None:
    """Append one record to the ledger. This function may not raise, ever.

    It is diagnostics bolted onto the failure path, so a full disk, a read-only
    checkout or a permission problem must cost the group nothing: the apology still
    goes out and delivery is unaffected. That makes this the *second* place in the
    file that swallows an exception, after _apologise, and for the same reason --
    there is nothing above it that could do anything with the failure.

    Opened, written and closed per record. Closing is what makes it survive the way
    this bot actually dies: a friend closing the Terminal window kills the process,
    and the kernel still owns the buffer of a closed file. No fsync -- that defends
    against a machine crash, which is not the failure mode here.
    """
    try:
        record = rejection_record(
            url=url,
            error=error,
            detail=detail,
            chat_id=getattr(message, "chat_id", None),
            message_id=getattr(message, "message_id", None),
            when=dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        )
        with (path or REJECTED_LEDGER).open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        log.exception("could not write the rejected-links ledger; delivery is unaffected")


def read_rejections(path: Path) -> list[dict]:
    """Every readable record in the ledger, oldest first.

    Unparseable lines are skipped rather than fatal: the process is killed by a
    window closing, so a half-written last line is a normal thing to find, not a
    corrupt file.
    """
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def rejection_host(url: str) -> str:
    """The host a bounced URL points at, for grouping. Never raises on junk."""
    try:
        return (urlparse(url).hostname or "?").lower().removeprefix("www.")
    except ValueError:
        return "?"


def format_rejections(records: list[dict]) -> str:
    """The --rejected report: grouped by error class, then by host.

    That order on purpose. The error class answers "what kind of thing is going
    wrong" -- one rotted extractor looks completely different from a run of files
    over the ceiling -- and the host answers "where", which is the next question and
    usually the fix. Every record is listed under its group because at ~20 links a
    week the whole file fits on a screen, and a count with no URLs is not something
    anybody can act on.
    """
    if not records:
        return "nothing has bounced yet"

    by_error: dict[str, dict[str, list[dict]]] = {}
    for record in records:
        error = str(record.get("error") or "?")
        host = rejection_host(str(record.get("url") or ""))
        by_error.setdefault(error, {}).setdefault(host, []).append(record)

    stamps = sorted(_readable_stamp(record) for record in records)
    lines = [f"{len(records)} links bounced, {stamps[0]} to {stamps[-1]}"]
    for error, hosts in sorted(by_error.items(), key=lambda kv: (-_group_size(kv[1]), kv[0])):
        lines.append("")
        lines.append(f"{error} -- {_group_size(hosts)}")
        for host, entries in sorted(hosts.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            lines.append(f"  {host} -- {len(entries)}")
            for record in sorted(entries, key=lambda r: str(r.get("when") or "")):
                lines.append(f"    {_readable_stamp(record)}  {record.get('url')}")
                detail = str(record.get("detail") or "").strip()
                if detail:
                    lines.append(f"        {detail[:160]}")
    return "\n".join(lines)


def _group_size(hosts: dict[str, list[dict]]) -> int:
    return sum(len(entries) for entries in hosts.values())


def _readable_stamp(record: dict) -> str:
    """"2026-08-09T16:31:00-03:00" -> "2026-08-09 16:31". The offset stays in the file."""
    return str(record.get("when") or "?")[:16].replace("T", " ")


def print_rejections() -> None:
    """`python bot.py --rejected`. The command the owner runs before asking for an
    analysis, so it explains its own scope: this file is one machine's memory."""
    records = read_rejections(REJECTED_LEDGER)
    print(format_rejections(records))
    print()
    print(f"({REJECTED_LEDGER} -- only what this machine saw; the host rotates)")


# --------------------------------------------------------------------------------
# The Telegram layer: a thin shell over the pure helpers above.
# --------------------------------------------------------------------------------


async def on_start(update: telegram.Update, _context: object) -> None:
    message = update.effective_message
    if message:
        await message.reply_text("mandá un link de YouTube, Instagram o Facebook y te lo bajo")


async def on_message(update: telegram.Update, _context: object) -> None:
    """Deliver every supported link in a message, and say why when there are none.

    Doing nothing is a legitimate outcome here -- most messages in the group are not
    links -- but an untraceable one is not. When a link is pasted and no video comes
    back, the log has to be able to tell the owner whether no URL was recognised at
    all or the URLs were recognised and rejected as unsupported hosts. Those two have
    different fixes and there is no way to tell them apart from the chat.

    The rejected URLs go in the log because they are the entire diagnosis. The
    message body does not: this is a private group.
    """
    message = update.effective_message
    if message is None:
        return
    urls = find_urls(message.text or message.caption)
    if not urls:
        log.info("message %s: no URL recognised, nothing to do", message.message_id)
        return
    supported = [url for url in urls if is_supported(url)]
    if not supported:
        log.info(
            "message %s: %d URL(s) found, none on a supported host -- rejected: %s",
            message.message_id,
            len(urls),
            ", ".join(urls),
        )
        return
    for url in supported:
        await _deliver(message, url)


# The current conflict episode: when it began and when it was last seen. Module
# state because python-telegram-bot hands the error handler nothing to keep it in,
# and the rule that reads it -- conflict_action -- is pure and tested on its own.
_conflict_started: float | None = None
_conflict_last: float | None = None


async def on_error(_update: object, context: object) -> None:
    """Turn a poll-level failure into something the person hosting the bot can act on.

    Registered with Application.add_error_handler. This is not the retry loop the
    delivery path deliberately does not have: a Conflict has no message to reply to
    and nothing local to catch it, so the global handler is the only place it can be
    handled at all. Registering one also stops python-telegram-bot from logging "No
    error handlers are registered" with a full traceback for every retry -- which,
    with the launcher's probe, is six lines and three tracebacks for an event that is
    completely normal.

    Two audiences, one event, so two lines: the log line is English and operational,
    like every other line in this file, and belongs to whoever reads the log later;
    the printed line is Spanish, because the person watching this window is a friend
    hosting the bot for the afternoon, not a developer.
    """
    global _conflict_started, _conflict_last

    error = getattr(context, "error", None)
    if not isinstance(error, telegram.error.Conflict):
        # Everything else keeps its traceback: it is unexpected, and this is the
        # only place it will ever be reported.
        log.exception("unhandled error while polling", exc_info=error)
        return

    action, _conflict_started, _conflict_last = conflict_action(
        time.monotonic(), _conflict_started, _conflict_last
    )
    if action == "quiet":
        return
    if action == "announce":
        log.warning("another instance has taken the poll; this one is receiving nothing meanwhile")
        print("Otra persona prendió el bot, así que este dejó de recibir mensajes.")
        return

    log.warning("the conflict lasted %.0f s; stopping so the window says so", CONFLICT_GRACE)
    print("El bot ahora lo tiene otra persona. Podés cerrar esta ventana.")
    application = getattr(context, "application", None)
    if application is not None:
        application.stop_running()


async def _deliver(message: telegram.Message, url: str) -> None:
    try:
        with temp_workspace() as workspace:
            # yt-dlp is blocking; keep it off the event loop so the bot stays responsive.
            media = await asyncio.to_thread(download_into, url, workspace)
            kind = media_kind(media.path, media.has_audio)
            size = media.path.stat().st_size
            if delivery_decision(size, kind) == "link":
                log.info("%s is %d bytes (%s), over the ceiling -- replying with a link", url, size, kind)
                # A link is not the media, so it counts as a bounce: this is the one
                # delivery path that has never run against Telegram (README.md §6),
                # and the ledger is how it stops being invisible.
                record_rejection(
                    message,
                    url,
                    OVERSIZE_ERROR,
                    f"{size} bytes as {kind}, over the {upload_ceiling(kind)}-byte ceiling",
                )
                await _reply_text(message, oversize_reply(size, media.direct_url or url))
                return
            log.info("sending %s as %s (%d bytes)", url, kind, size)
            await _send(message, kind, media)
    except Exception as exc:
        # Never a stack trace in the group. The real error goes to the log.
        log.exception("failed to deliver %s", url)
        # Written before the apology so a network bad enough to kill both still
        # leaves the diagnosis behind. It cannot raise, so it cannot cost the apology.
        record_rejection(message, url, type(exc).__name__, str(exc))
        await _apologise(message)


async def _reply_text(message: telegram.Message, text: str) -> None:
    """Send a short text reply with timeouts of its own. May raise."""
    await message.reply_text(
        text,
        connect_timeout=CONNECT_TIMEOUT,
        write_timeout=TEXT_REPLY_TIMEOUT,
        read_timeout=TEXT_REPLY_TIMEOUT,
    )


async def _apologise(message: telegram.Message) -> None:
    """Tell the group the link failed. This call may not raise, ever.

    It is the last line of defence, so it is the one call that cannot itself be
    unprotected: a network bad enough to fail an upload is frequently bad enough to
    fail the apology, and an exception escaping here escapes _deliver too. Observed
    live on 2026-08-07: the upload timed out, reply_text timed out five seconds
    later, the exception left _deliver, and python-telegram-bot logged "No error
    handlers are registered". The group got no video and no apology -- the silent
    drop PLAN.md forbids.

    Swallowing is correct here and nowhere else. There is nothing above this to
    handle the failure and no third message worth attempting; the log is the only
    honest destination left.
    """
    try:
        await _reply_text(message, FAILURE_REPLY)
    except Exception:
        log.exception("could not deliver the failure reply either; the group got nothing")


async def _send(message: telegram.Message, kind: str, media: Media) -> None:
    reply = getattr(message, reply_method_name(kind))
    extra = video_kwargs(media) if kind == "video" else {}
    await reply(
        media.path,
        connect_timeout=CONNECT_TIMEOUT,
        write_timeout=UPLOAD_TIMEOUT,
        read_timeout=UPLOAD_TIMEOUT,
        **extra,
    )


def build_application(token: str) -> Application:
    """Wire the handlers onto an Application. Separate from main() so the self-check
    can assert the wiring: a handler that exists but was never registered is the one
    failure this file cannot see from the outside, and forgetting add_error_handler
    would silently restore the wall of tracebacks. Builds nothing over the network."""
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(MessageHandler(MESSAGE_FILTER, on_message))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not ffmpeg_path():
        log.warning("ffmpeg is not on PATH; merged-quality downloads will fail")
    app = build_application(read_token())
    log.info("polling; privacy mode must be OFF for the bot to see plain links (see README.md)")
    log.info("only one instance can poll this token at a time (see README.md: the baton pass)")
    # ponytail: whatever was posted while nobody was hosting is lost. Telegram holds
    # updates for about 24 hours and run_polling() replays them all by default, so
    # the first person to open the launcher would dump everything posted in the gap
    # into the group in one burst -- with a rotating host that is the normal case,
    # not an edge case. Measured with nobody running: 7 updates queued, 2 of them
    # reels. At ~20 links a week the flood is clearly worse than the loss, and
    # anybody can paste the link again. The ceiling is exactly that: a link posted
    # while the bot was off never arrives. Upgrade path if it ever bites, and it
    # needs no new dependency: keep the backlog but deliver only the updates whose
    # message date is within a few minutes of startup, dropping the rest.
    app.run_polling(drop_pending_updates=True)


# --------------------------------------------------------------------------------
# Self-check: `python bot.py --self-check`. Plain asserts, no test framework.
# --------------------------------------------------------------------------------

# One known-good public URL per site. These hit the network on purpose: extraction
# rotting is this project's real failure mode, and only a real download detects it.
#
# Deliberately short clips. The YouTube entry is a 19-second video, not the
# 3.5-minute one the format string was measured against, because the check does not
# need 30 MB to prove anything: verified on 2026-08-07 that the codec-agnostic
# mutation of MEDIA_FORMAT still selects AV1 on this clip, so the assertion that
# matters stays armed while the run drops from minutes to seconds. The large-file
# path is covered by plain numbers through delivery_decision instead.
#
# Each entry is (url, expected media kind). The kind is asserted, not just recorded:
# an Instagram image post coming back as a video -- or a reel coming back as its
# poster frame -- is the failure mode the image fallback can produce.
SELF_CHECK_URLS = {
    "youtube": ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "video"),
    "instagram reel": ("https://www.instagram.com/reel/DbGNFqVKnB-/?igsh=OHFxM3dxdmIzdTQ5", "video"),
    "instagram image": ("https://www.instagram.com/p/DbvWPFQxPkI/?igsh=Mnd6dGdxajVzeGV5", "photo"),
    "facebook": ("https://www.facebook.com/share/v/1L8yZSLkWq/", "video"),
}


def _check_pure_helpers() -> None:
    # find_urls: no URL, one mid-sentence, several, a bare domain, trailing punctuation.
    assert find_urls("") == []
    assert find_urls(None) == []
    assert find_urls("no hay nada aca") == []
    assert find_urls("miren instagram.com que buen post") == [], "a bare domain is not a link"
    assert find_urls("che miren https://youtu.be/abc esto") == ["https://youtu.be/abc"]
    assert find_urls("https://youtu.be/abc y https://youtu.be/def") == [
        "https://youtu.be/abc",
        "https://youtu.be/def",
    ]
    assert find_urls("mira https://youtu.be/abc.") == ["https://youtu.be/abc"], "strip trailing dot"
    assert find_urls("(https://youtu.be/abc)") == ["https://youtu.be/abc"]
    assert find_urls("https://youtu.be/abc https://youtu.be/abc") == ["https://youtu.be/abc"]
    assert find_urls("HTTPS://YOUTU.BE/abc") == ["HTTPS://YOUTU.BE/abc"]
    print("ok  find_urls")

    # is_supported: the three sites, their subdomains and short forms, and nothing else.
    for url in (
        "https://www.youtube.com/watch?v=x",
        "https://youtu.be/x",
        "https://m.youtube.com/watch?v=x",
        "https://www.instagram.com/reel/x/",
        "https://instagram.com/p/x/",
        "https://www.facebook.com/share/v/x/",
        "https://fb.watch/x/",
    ):
        assert is_supported(url), f"{url} should be supported"
    for url in (
        "https://example.com/article",
        "https://www.tiktok.com/@a/video/1",
        "https://notyoutube.com/watch?v=x",
        "https://youtube.com.evil.example/watch?v=x",
    ):
        assert not is_supported(url), f"{url} should not be supported"
    print("ok  is_supported")

    # media_kind and the reply-method mapping.
    assert media_kind("clip.mp4", has_audio=True) == "video"
    assert media_kind("clip.mp4", has_audio=False) == "animation", "silent clip is an animation"
    assert media_kind("clip.webm", has_audio=True) == "video"
    assert media_kind("meme.gif", has_audio=False) == "animation"
    assert media_kind("meme.GIF", has_audio=True) == "animation", "suffix match is case-insensitive"
    assert media_kind("photo.jpg") == "photo"
    assert media_kind(Path("/tmp/x/photo.PNG")) == "photo"
    assert media_kind("photo.webp", has_audio=False) == "photo"
    print("ok  media_kind")

    assert reply_method_name("video") == "reply_video"
    assert reply_method_name("photo") == "reply_photo"
    assert reply_method_name("animation") == "reply_animation"
    try:
        reply_method_name("document")
    except ValueError:
        pass
    else:
        raise AssertionError("reply_method_name should reject unknown kinds")
    # Catch a python-telegram-bot rename without touching the network.
    for kind, name in REPLY_METHODS.items():
        assert hasattr(telegram.Message, name), f"telegram.Message has no {name} (for {kind})"
    print("ok  reply_method_name maps onto real telegram.Message methods")

    # The exact filter object main() installs, against updates built by hand.
    sample = telegram.Message(
        message_id=1,
        date=dt.datetime.fromtimestamp(0, dt.timezone.utc),
        chat=telegram.Chat(id=-100, type=telegram.Chat.GROUP),
        from_user=telegram.User(id=1, first_name="u", is_bot=False),
        text="mira https://youtu.be/abc",
    )
    assert MESSAGE_FILTER.check_update(telegram.Update(update_id=1, message=sample)), (
        "a plain new message must be handled"
    )
    assert not MESSAGE_FILTER.check_update(telegram.Update(update_id=2, edited_message=sample)), (
        "an edited message must NOT re-trigger a download"
    )
    assert not MESSAGE_FILTER.check_update(telegram.Update(update_id=3, channel_post=sample)), (
        "a channel post must not be handled"
    )
    print("ok  MESSAGE_FILTER ignores edits and channel posts")

    # Size fallback, driven by plain numbers -- no huge download involved.
    assert delivery_decision(0, "video") == "file"
    assert delivery_decision(1, "video") == "file"
    assert delivery_decision(TELEGRAM_MAX_UPLOAD - 1, "video") == "file"
    assert delivery_decision(TELEGRAM_MAX_UPLOAD, "video") == "file", "the ceiling itself fits"
    assert delivery_decision(TELEGRAM_MAX_UPLOAD + 1, "video") == "link"
    assert delivery_decision(700 * 1024 * 1024, "animation") == "link"
    assert delivery_decision(TELEGRAM_MAX_PHOTO_UPLOAD, "photo") == "file"
    assert delivery_decision(TELEGRAM_MAX_PHOTO_UPLOAD + 1, "photo") == "link", "photos cap lower"
    assert delivery_decision(TELEGRAM_MAX_PHOTO_UPLOAD + 1, "video") == "file", "videos do not"
    # The large-file path, by number rather than by downloading 30 MB every run.
    # 29,969,207 B is the real measured size of a 3.5-minute 720p YouTube video.
    assert delivery_decision(29_969_207, "video") == "file", "a 3.5-min 720p video must fit"
    assert delivery_decision(29_969_207, "photo") == "link", "the same bytes as a photo must not"
    print("ok  delivery_decision")

    # telegram_renders_inline, against the four real uploads it encodes.
    assert telegram_renders_inline("mov,mp4,m4a,3gp,3g2,mj2", "h264")
    assert telegram_renders_inline("mov,mp4,m4a,3gp,3g2,mj2", "vp9"), "vp9 in mp4 played inline"
    assert not telegram_renders_inline("matroska,webm", "av1"), "webm/av1 arrived as a document"
    assert not telegram_renders_inline("mov,mp4,m4a,3gp,3g2,mj2", "av1"), "av1 is out either way"
    assert not telegram_renders_inline("matroska,webm", "h264"), "webm is out either way"
    print("ok  telegram_renders_inline")

    # video_kwargs: real numbers pass through, unknowns are omitted, never zeros.
    stub = Path("x.mp4")
    full = video_kwargs(Media(stub, True, None, 1280, 720, 213.0))
    assert full == {"supports_streaming": True, "width": 1280, "height": 720, "duration": 213}, full
    bare = video_kwargs(Media(stub, True))
    assert bare == {"supports_streaming": True}, f"unknown dimensions must be omitted: {bare}"
    assert "width" not in video_kwargs(Media(stub, True, None, 0, 0, 0)), "never send zeros"
    assert "duration" not in video_kwargs(Media(stub, True, None, 0, 0, 0)), "never send zeros"
    assert "width" not in video_kwargs(Media(stub, True, None, 1280, None, None)), "half is nothing"
    assert video_kwargs(Media(stub, True, None, None, None, 29.7))["duration"] == 30, "rounded int"
    print("ok  video_kwargs")

    message = oversize_reply(120 * 1024 * 1024, "https://youtu.be/abc")
    assert "https://youtu.be/abc" in message, "the fallback must carry the link"
    assert "120 MB" in message, message
    assert "Traceback" not in message
    print("ok  oversize_reply")

    # is_image_post: the guard that keeps a broken video from becoming a still.
    thumbs = [{"id": "0", "url": "https://example.com/a.jpg"}]
    assert is_image_post({"formats": [], "thumbnails": thumbs}), "no formats + thumbnails = image"
    assert is_image_post({"thumbnails": thumbs}), "a missing formats key is still no formats"
    # The one that matters: a post that HAS video formats is a video, whatever else
    # is true of it. If those formats then fail, that is an error, not a poster frame.
    assert not is_image_post({"formats": [{"format_id": "hd"}], "thumbnails": thumbs}), (
        "a video whose formats exist must never be treated as an image"
    )
    assert not is_image_post({"formats": [], "thumbnails": []}), "no formats and no image = error"
    # A carousel is unmeasured, so it must fail loudly rather than send a guess.
    # `entries: 0` is what the measured single-image post reports -- still an image.
    assert not is_image_post({"formats": [], "thumbnails": thumbs, "entries": [{}, {}]}), (
        "a multi-item carousel must not be guessed at"
    )
    assert is_image_post({"formats": [], "thumbnails": thumbs, "entries": []}), "entries:0 is fine"
    assert not is_image_post({}), "an empty info dict is not an image post"
    assert not is_image_post(None), "no info at all is not an image post"
    # Neither of these discriminates anything -- measured, both are the same for an
    # image post and for a working reel. Asserted so nobody reaches for them later.
    assert is_image_post({"formats": [], "thumbnails": thumbs, "duration": None, "title": "Video by x"})
    assert not is_image_post({"formats": [{"format_id": "1"}], "duration": None, "thumbnails": thumbs})
    print("ok  is_image_post")


def _check_rejected_ledger() -> None:
    """The ledger records the bounce, survives junk, and can never break delivery."""
    # Record building: the shape, the truncation, and what must NOT be in it.
    record = rejection_record(
        url="https://www.instagram.com/p/x/",
        error="DownloadError",
        detail="ERROR: [Instagram] x: No video formats found!\nsecond line is noise",
        chat_id=-100123,
        message_id=7,
        when="2026-08-09T16:31:00-03:00",
    )
    assert set(record) == {"when", "chat_id", "message_id", "url", "error", "detail"}, record
    assert record["detail"] == "ERROR: [Instagram] x: No video formats found!", record
    assert record["chat_id"] == -100123 and record["message_id"] == 7, record
    huge = rejection_record("u", "E", "x" * 5000, 1, 1, "w")
    assert len(huge["detail"]) == REJECTED_DETAIL_LIMIT + 3, len(huge["detail"])
    assert rejection_record("u", "E", "", 1, 1, "w")["detail"] == ""
    print("ok  rejection_record")

    class StubMessage:
        chat_id = -100123
        message_id = 7
        text = "miren esto SECRETO-DEL-GRUPO https://youtu.be/abc"

    with temp_workspace() as workspace:
        ledger = workspace / "rejected.jsonl"
        record_rejection(StubMessage(), "https://youtu.be/abc", "DownloadError", "boom", ledger)
        record_rejection(StubMessage(), "https://www.instagram.com/p/x/", "TimedOut", "slow", ledger)
        # Read with a fresh handle: if the writer kept the file open and unflushed,
        # this is what a killed process would have lost.
        raw = ledger.read_text(encoding="utf-8")
        assert raw.count("\n") == 2, f"one flushed line per record: {raw!r}"
        assert "SECRETO-DEL-GRUPO" not in raw, "the message body must never reach the ledger"
        records = read_rejections(ledger)
        assert len(records) == 2, records
        assert records[0]["error"] == "DownloadError" and records[1]["error"] == "TimedOut"

        # A process killed mid-write leaves a half line. That is normal, not corrupt.
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write('{"when": "2026-08-09T1')
        assert len(read_rejections(ledger)) == 2, "a truncated last line must be skipped"
        assert read_rejections(workspace / "nope.jsonl") == [], "a missing ledger is empty, not fatal"

        # Diagnostics may never break delivery: a ledger path that cannot be written
        # has to be swallowed. A directory is the cheapest unwritable path there is.
        blocked = workspace / "a-directory"
        blocked.mkdir()
        with _capture_log(logging.ERROR) as errors:
            record_rejection(StubMessage(), "https://youtu.be/abc", "DownloadError", "boom", blocked)
        assert len(errors) == 1 and "ledger" in errors[0], errors
    print("ok  record_rejection appends, flushes, hides the body and never raises")

    # A file too big to upload is a bounce too: the group got a link, not the media.
    # That path has never run against Telegram (README.md §6), so the ledger is the
    # only thing that will ever tell the owner it happened. The oversized file is
    # sparse -- truncate() sets the byte count the ceiling is compared against
    # without writing 10 MiB.
    class LinkRecordingMessage:
        chat_id = -100123
        message_id = 9

        def __init__(self) -> None:
            self.said = ""

        async def reply_text(self, text: str, **_kwargs: object) -> None:
            self.said = text

    with temp_workspace() as workspace:
        huge = workspace / "huge.jpg"
        with huge.open("wb") as handle:
            handle.truncate(TELEGRAM_MAX_PHOTO_UPLOAD + 1)
        ledger = workspace / "rejected.jsonl"
        real_download, real_ledger = globals()["download_into"], globals()["REJECTED_LEDGER"]
        globals()["download_into"] = lambda _url, _dir: Media(huge, has_audio=False)
        globals()["REJECTED_LEDGER"] = ledger
        try:
            message = LinkRecordingMessage()
            asyncio.run(_deliver(message, "https://www.instagram.com/p/big/"))
        finally:
            globals()["download_into"] = real_download
            globals()["REJECTED_LEDGER"] = real_ledger
        assert "no me deja subirlo" in message.said, message.said
        assert FAILURE_REPLY not in message.said, "an oversize file is not a failure to the group"
        oversized = read_rejections(ledger)
        assert len(oversized) == 1, oversized
        assert oversized[0]["error"] == OVERSIZE_ERROR, oversized
        assert str(TELEGRAM_MAX_PHOTO_UPLOAD + 1) in oversized[0]["detail"], oversized
    print("ok  an oversize link reply is recorded as a bounce too")

    # The report: grouped by error class, then by host, every URL visible.
    assert format_rejections([]) == "nothing has bounced yet"
    report = format_rejections([
        rejection_record("https://www.instagram.com/p/a/", "DownloadError", "no formats", 1, 1,
                         "2026-08-07T10:00:00-03:00"),
        rejection_record("https://www.instagram.com/p/b/", "DownloadError", "no formats", 1, 2,
                         "2026-08-08T11:00:00-03:00"),
        rejection_record("https://youtu.be/c", "TimedOut", "upload died", 1, 3,
                         "2026-08-09T12:00:00-03:00"),
    ])
    assert report.startswith("3 links bounced, 2026-08-07 10:00 to 2026-08-09 12:00"), report
    assert "DownloadError -- 2" in report and "TimedOut -- 1" in report, report
    assert "  instagram.com -- 2" in report and "  youtu.be -- 1" in report, report
    # The biggest group first: a pattern has to be visible without counting.
    assert report.index("DownloadError") < report.index("TimedOut"), report
    for url in ("https://www.instagram.com/p/a/", "https://www.instagram.com/p/b/", "https://youtu.be/c"):
        assert url in report, f"{url} missing from the report"
    assert "no formats" in report, "the error text is the diagnosis"
    # Junk in the file must not crash the report the owner runs.
    assert format_rejections([{}, {"url": "not a url", "error": None}]), "junk records must render"
    print("ok  format_rejections groups by error class, then host")


def _probe_container_and_codec(path: Path) -> tuple[str, str, int, int]:
    """What the file on disk really is: (container, codec, width, height).

    ffprobe, not the info dict. The point of this check is to catch a file that
    yt-dlp is perfectly happy with and Telegram is not -- and, for a still image, to
    prove the bytes decode as a picture at all rather than being an error page.
    """
    ffprobe = shutil.which("ffprobe")
    assert ffprobe, "ffprobe not found on PATH (it ships with ffmpeg)"
    result = subprocess.run(
        [ffprobe, "-v", "error", "-of", "json",
         "-show_entries", "format=format_name:stream=codec_type,codec_name,width,height", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    probed = json.loads(result.stdout)
    container = probed["format"]["format_name"]
    video = [s for s in probed["streams"] if s.get("codec_type") == "video"]
    assert video, f"{path.name}: ffprobe found no video stream"
    stream = video[0]
    return container, stream.get("codec_name", ""), stream.get("width", 0), stream.get("height", 0)


def _check_extraction() -> None:
    assert ffmpeg_path(), "ffmpeg not found on PATH -- merged 720p cannot work without it"
    print(f"ok  ffmpeg resolved from PATH at {ffmpeg_path()}")

    for site, (url, expected_kind) in SELF_CHECK_URLS.items():
        print(f"..  {site}: downloading {url}")
        with downloaded_media(url) as media:
            assert media.path.is_file(), f"{site}: expected a file at {media.path}"
            size = media.path.stat().st_size
            assert size > 0, f"{site}: downloaded file is empty"
            kind = media_kind(media.path, media.has_audio)
            assert kind == expected_kind, (
                f"{site}: expected a {expected_kind}, got a {kind} ({media.path.name}). "
                f"A reel arriving as its poster frame is exactly what this catches."
            )
            assert delivery_decision(size, kind) == "file", (
                f"{site}: {size} bytes is over the {upload_ceiling(kind)}-byte ceiling for {kind}"
            )

            container, codec, width, height = _probe_container_and_codec(media.path)
            if kind == "video":
                # The property the whole design rests on. Without this the check
                # passes for a file Telegram shows as a grey file row, not a video.
                assert telegram_renders_inline(container, codec), (
                    f"{site}: got {container} / {codec}, which Telegram delivers as a DOCUMENT, "
                    f"not a video. Check MEDIA_FORMAT."
                )
            else:
                # For a still, "it decodes and has real dimensions" is the property:
                # an HTML error page saved as .jpg would fail here.
                assert width > 0 and height > 0, f"{site}: {container}/{codec} has no dimensions"
                # And it must be the BEST still, not merely a valid one. Every
                # thumbnail is still sitting in the temp dir, so compare against
                # them rather than against a hardcoded resolution Instagram is free
                # to change. Sorting by filename would pick ".9.jpg" over ".12.jpg".
                others = [
                    p for p in media.path.parent.iterdir()
                    if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES
                ]
                assert len(others) > 1, f"{site}: only {len(others)} thumbnail, nothing was chosen"
                biggest = max(other.stat().st_size for other in others)
                assert size == biggest, (
                    f"{site}: delivered {media.path.name} at {size} B but a larger "
                    f"thumbnail ({biggest} B) was available -- selection is wrong"
                )

            extra = video_kwargs(media) if kind == "video" else {}
            print(
                f"ok  {site}: {media.path.name} {size} bytes, {container.split(',')[0]}/{codec}"
                f" {width}x{height} -> {reply_method_name(kind)} {extra}"
            )
            workspace = media.path.parent
        assert not workspace.exists(), f"{site}: temp dir {workspace} survived the context manager"
    print("ok  temp directories cleaned up")


def _check_send_timeouts() -> None:
    """Every outbound call must carry a connect_timeout of its own.

    Without it PTB silently uses its 5 s default and a generous upload timeout buys
    nothing, which is exactly how the live uploads died.
    """

    class CapturingMessage:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        async def reply_video(self, _video: object, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def reply_text(self, _text: str, **kwargs: object) -> None:
            self.kwargs = kwargs

    video = CapturingMessage()
    asyncio.run(_send(video, "video", Media(Path("x.mp4"), True, None, 1280, 720, 10.0)))
    assert video.kwargs.get("connect_timeout") == CONNECT_TIMEOUT, video.kwargs
    assert video.kwargs.get("write_timeout") == UPLOAD_TIMEOUT, video.kwargs
    assert video.kwargs.get("read_timeout") == UPLOAD_TIMEOUT, video.kwargs

    text = CapturingMessage()
    asyncio.run(_reply_text(text, "hola"))
    assert text.kwargs.get("connect_timeout") == CONNECT_TIMEOUT, text.kwargs
    assert text.kwargs.get("write_timeout") == TEXT_REPLY_TIMEOUT, text.kwargs
    print("ok  outbound calls carry an explicit connect_timeout")


@contextmanager
def _capture_log(level: int) -> Iterator[list[str]]:
    """Collect this module's log messages at `level` or above, and keep them off stderr.

    Raises the logger's own level for the duration: the self-check configures logging
    at WARNING, so an INFO record would be dropped before any handler saw it.
    """
    messages: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Collect(level)
    previous_level, previous_propagate = log.level, log.propagate
    log.addHandler(handler)
    log.setLevel(level)
    log.propagate = False
    try:
        yield messages
    finally:
        log.removeHandler(handler)
        log.setLevel(previous_level)
        log.propagate = previous_propagate


def _check_message_logging() -> None:
    """A message that produces no video must say why in the log."""

    def deliver_nothing(text: str) -> list[str]:
        message = telegram.Message(
            message_id=7,
            date=dt.datetime.fromtimestamp(0, dt.timezone.utc),
            chat=telegram.Chat(id=-100, type=telegram.Chat.GROUP),
            from_user=telegram.User(id=1, first_name="u", is_bot=False),
            text=text,
        )
        with _capture_log(logging.INFO) as messages:
            asyncio.run(on_message(telegram.Update(update_id=1, message=message), None))
        return messages

    no_url = deliver_nothing("che alguien vio el partido ayer")
    assert len(no_url) == 1, f"exactly one line, got {no_url}"
    assert "no URL recognised" in no_url[0], no_url
    assert "partido" not in no_url[0], f"do not log the message body: {no_url}"

    rejected = deliver_nothing("miren https://example.com/nota y https://www.tiktok.com/@a/video/1")
    assert len(rejected) == 1, f"exactly one line, got {rejected}"
    # The rejected URLs are the whole diagnosis -- without them the line is useless.
    assert "https://example.com/nota" in rejected[0], rejected
    assert "https://www.tiktok.com/@a/video/1" in rejected[0], rejected
    assert "2 URL(s)" in rejected[0], rejected
    # The message body is not the diagnosis, and this is a private group.
    assert "miren" not in rejected[0], f"do not log the message body: {rejected}"
    print("ok  on_message logs why it delivered nothing")


def _check_failure_path() -> None:
    """_deliver must survive a download AND a reply that both blow up.

    Doubles built inline: a message whose reply_text always raises, and a stand-in
    for download_into that raises before any network call. No mocking framework.
    """

    class ExplodingMessage:
        chat_id = -100123
        message_id = 42
        text = "miren esto SECRETO-DEL-GRUPO https://youtu.be/never-fetched"

        def __init__(self) -> None:
            self.attempts = 0

        async def reply_text(self, _text: str, **_kwargs: object) -> None:
            self.attempts += 1
            raise telegram.error.TimedOut

    def _exploding_download(_url: str, _target_dir: Path) -> Media:
        raise ExtractionError("simulated extractor failure")

    real_download = globals()["download_into"]
    real_ledger = globals()["REJECTED_LEDGER"]
    globals()["download_into"] = _exploding_download
    with temp_workspace() as workspace:
        globals()["REJECTED_LEDGER"] = workspace / "rejected.jsonl"
        try:
            message = ExplodingMessage()
            # Both failures here are deliberate, so _capture_log also keeps their
            # tracebacks out of the self-check's output -- asserted on below, not ignored.
            with _capture_log(logging.ERROR) as errors:
                # If this raises, _deliver's except block is not a net and the group gets
                # nothing -- the exact live failure this guards against.
                asyncio.run(_deliver(message, "https://youtu.be/never-fetched"))
            written = read_rejections(globals()["REJECTED_LEDGER"])
            raw = globals()["REJECTED_LEDGER"].read_text(encoding="utf-8")
        finally:
            globals()["download_into"] = real_download
            globals()["REJECTED_LEDGER"] = real_ledger

    assert message.attempts == 1, f"the apology must be attempted exactly once, got {message.attempts}"
    assert len(errors) == 2, f"both failures must be logged, got {errors}"
    assert any("failed to deliver" in m for m in errors), errors
    assert any("got nothing" in m for m in errors), errors
    # The bounce is written down even when the apology itself dies -- that pair is
    # exactly the case nobody was around to see, and the whole point of the ledger.
    assert len(written) == 1, f"the failure must land in the ledger: {written}"
    assert written[0]["url"] == "https://youtu.be/never-fetched", written
    assert written[0]["error"] == "ExtractionError", written
    assert written[0]["message_id"] == 42 and written[0]["chat_id"] == -100123, written
    assert "SECRETO-DEL-GRUPO" not in raw, "the message body must never reach the ledger"
    print("ok  _deliver swallows a failing download AND a failing apology, and records it")


def _check_conflict_handling() -> None:
    """One line per conflict episode, a clean stop only when it is sustained.

    The whole path except the network: conflict_action is pure, and on_error takes a
    stand-in context, so both halves are reachable without a second live bot. What is
    NOT reachable here is python-telegram-bot actually delivering a Conflict to the
    handler -- that needs two instances and a real token.
    """
    # A single probe: the launcher asks Telegram whether anybody is polling, which
    # costs the running instance a burst of conflicts for about ten seconds. It gets
    # announced once and must NEVER stop the bot -- otherwise merely asking kills it.
    started = last = None
    actions = []
    for moment in (0.0, 1.0, 2.5, 4.7, 8.1, 10.0):
        action, started, last = conflict_action(moment, started, last)
        actions.append(action)
    assert actions == ["announce", "quiet", "quiet", "quiet", "quiet", "quiet"], actions

    # Quiet again, then somebody probes an hour later: a new episode, not a stop.
    action, started, last = conflict_action(3600.0, started, last)
    assert action == "announce", action

    # Somebody has really taken over: conflicts keep arriving on PTB's backoff, which
    # tops out at 30 s, and after CONFLICT_GRACE this instance gives up.
    started = last = None
    moment = 0.0
    actions = []
    while moment <= CONFLICT_GRACE + 30.0:
        action, started, last = conflict_action(moment, started, last)
        actions.append(action)
        moment += 25.0  # inside EPISODE_GAP, so it stays one episode
    assert actions.count("announce") == 1, actions
    assert "give-up" in actions, actions
    assert actions.index("give-up") * 25.0 >= CONFLICT_GRACE, actions

    # The handler has to be registered, not merely written. The token below is a
    # syntactically shaped fake and never leaves this process: building an
    # Application makes no request.
    wired = build_application("123456:AAHnot-a-real-token-nothing-is-sent")
    assert on_error in wired.error_handlers, "main() must register the conflict handler"

    class StopRecordingApplication:
        def __init__(self) -> None:
            self.stopped = 0

        def stop_running(self) -> None:
            self.stopped += 1

    class Context:
        def __init__(self, error: Exception, application: object) -> None:
            self.error = error
            self.application = application

    global _conflict_started, _conflict_last
    saved = (_conflict_started, _conflict_last)
    application = StopRecordingApplication()
    try:
        _conflict_started = _conflict_last = None
        with _capture_log(logging.WARNING) as warnings:
            spoken = io.StringIO()
            with redirect_stdout(spoken):
                # Two conflicts back to back are one event, and the bot keeps running.
                asyncio.run(on_error(None, Context(telegram.error.Conflict("c"), application)))
                asyncio.run(on_error(None, Context(telegram.error.Conflict("c"), application)))
        assert len(warnings) == 1, f"one line per episode, got {warnings}"
        assert "taken the poll" in warnings[0], warnings
        # The window is read by a friend, not by a developer.
        assert spoken.getvalue().count("\n") == 1, spoken.getvalue()
        assert "Otra persona" in spoken.getvalue(), spoken.getvalue()
        assert application.stopped == 0, "a burst of conflicts must not stop the bot"

        # A sustained one does stop it, and says so before it goes.
        _conflict_last = time.monotonic()
        _conflict_started = _conflict_last - CONFLICT_GRACE - 1.0
        with _capture_log(logging.WARNING) as warnings:
            spoken = io.StringIO()
            with redirect_stdout(spoken):
                asyncio.run(on_error(None, Context(telegram.error.Conflict("c"), application)))
        assert application.stopped == 1, "a sustained conflict must stop the bot"
        assert len(warnings) == 1, warnings
        assert "cerrar esta ventana" in spoken.getvalue(), spoken.getvalue()

        # Anything that is not a Conflict keeps its traceback and stops nothing.
        _conflict_started = _conflict_last = None
        with _capture_log(logging.ERROR) as errors:
            asyncio.run(on_error(None, Context(telegram.error.TimedOut(), application)))
        assert len(errors) == 1, errors
        assert "unhandled error" in errors[0], errors
        assert application.stopped == 1, "only a conflict stops the bot"
    finally:
        _conflict_started, _conflict_last = saved
    print("ok  a conflict is one line, and only a sustained one stops the bot")


def _check_startup_drops_the_backlog() -> None:
    """Starting must not replay everything posted while nobody was hosting.

    main() is reached with a stand-in Application, so the kwarg is asserted where it
    is actually passed rather than as a constant that agrees with itself. The token
    is a shaped fake and no request is made: build_application is replaced first.
    """

    class PollRecordingApplication:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        def run_polling(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    application = PollRecordingApplication()
    real_build = globals()["build_application"]
    globals()["build_application"] = lambda _token: application
    previous = os.environ.get(TOKEN_ENV_VAR)
    os.environ[TOKEN_ENV_VAR] = "123456:AAHnot-a-real-token-nothing-is-sent"
    try:
        main()
    finally:
        globals()["build_application"] = real_build
        if previous is None:
            del os.environ[TOKEN_ENV_VAR]
        else:
            os.environ[TOKEN_ENV_VAR] = previous

    assert application.kwargs.get("drop_pending_updates") is True, application.kwargs
    print("ok  startup drops the backlog instead of flooding the group")


def _self_check() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    _check_pure_helpers()
    _check_send_timeouts()
    _check_message_logging()
    _check_rejected_ledger()
    _check_failure_path()
    _check_conflict_handling()
    _check_startup_drops_the_backlog()
    _check_extraction()
    print("\nself-check passed")


if __name__ == "__main__":
    if "--self-check" in sys.argv[1:]:
        _self_check()
    elif "--rejected" in sys.argv[1:]:
        print_rejections()
    else:
        main()
