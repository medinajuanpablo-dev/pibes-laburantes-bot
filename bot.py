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
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
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


# --------------------------------------------------------------------------------
# The Telegram layer: a thin shell over the pure helpers above.
# --------------------------------------------------------------------------------


async def on_start(update: telegram.Update, _context: object) -> None:
    message = update.effective_message
    if message:
        await message.reply_text("mandá un link de YouTube, Instagram o Facebook y te lo bajo")


async def on_message(update: telegram.Update, _context: object) -> None:
    message = update.effective_message
    if message is None:
        return
    for url in find_urls(message.text or message.caption):
        if is_supported(url):
            await _deliver(message, url)


async def _deliver(message: telegram.Message, url: str) -> None:
    try:
        with temp_workspace() as workspace:
            # yt-dlp is blocking; keep it off the event loop so the bot stays responsive.
            media = await asyncio.to_thread(download_into, url, workspace)
            kind = media_kind(media.path, media.has_audio)
            size = media.path.stat().st_size
            if delivery_decision(size, kind) == "link":
                log.info("%s is %d bytes (%s), over the ceiling -- replying with a link", url, size, kind)
                await _reply_text(message, oversize_reply(size, media.direct_url or url))
                return
            log.info("sending %s as %s (%d bytes)", url, kind, size)
            await _send(message, kind, media)
    except Exception:
        # Never a stack trace in the group. The real error goes to the log.
        log.exception("failed to deliver %s", url)
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not ffmpeg_path():
        log.warning("ffmpeg is not on PATH; merged-quality downloads will fail")
    app = Application.builder().token(read_token()).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(MessageHandler(MESSAGE_FILTER, on_message))
    log.info("polling; privacy mode must be OFF for the bot to see plain links (see README.md)")
    app.run_polling()


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
SELF_CHECK_URLS = {
    "youtube": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "instagram": "https://www.instagram.com/reel/DbGNFqVKnB-/?igsh=OHFxM3dxdmIzdTQ5",
    "facebook": "https://www.facebook.com/share/v/1L8yZSLkWq/",
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


def _probe_container_and_codec(path: Path) -> tuple[str, str]:
    """Ask ffprobe what the file on disk really is: (container, video codec name).

    ffprobe, not the info dict. The point of this check is to catch a format string
    that yt-dlp is perfectly happy with and Telegram is not.
    """
    ffprobe = shutil.which("ffprobe")
    assert ffprobe, "ffprobe not found on PATH (it ships with ffmpeg)"
    result = subprocess.run(
        [ffprobe, "-v", "error", "-of", "json",
         "-show_entries", "format=format_name:stream=codec_type,codec_name", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    probed = json.loads(result.stdout)
    container = probed["format"]["format_name"]
    video = [s for s in probed["streams"] if s.get("codec_type") == "video"]
    assert video, f"{path.name}: ffprobe found no video stream"
    return container, video[0].get("codec_name", "")


def _check_extraction() -> None:
    assert ffmpeg_path(), "ffmpeg not found on PATH -- merged 720p cannot work without it"
    print(f"ok  ffmpeg resolved from PATH at {ffmpeg_path()}")

    for site, url in SELF_CHECK_URLS.items():
        print(f"..  {site}: downloading {url}")
        with downloaded_media(url) as media:
            assert media.path.is_file(), f"{site}: expected a file at {media.path}"
            size = media.path.stat().st_size
            assert size > 0, f"{site}: downloaded file is empty"
            kind = media_kind(media.path, media.has_audio)
            assert delivery_decision(size, kind) == "file", (
                f"{site}: {size} bytes is over the {upload_ceiling(kind)}-byte ceiling for {kind}"
            )

            # The property the whole design rests on. Without this the check passes
            # for a file Telegram delivers as a grey file row instead of a video.
            container, codec = _probe_container_and_codec(media.path)
            assert telegram_renders_inline(container, codec), (
                f"{site}: got {container} / {codec}, which Telegram delivers as a DOCUMENT, "
                f"not a video. Check MEDIA_FORMAT."
            )

            extra = video_kwargs(media)
            print(
                f"ok  {site}: {media.path.name} {size} bytes, {container.split(',')[0]}/{codec}"
                f" -> {reply_method_name(kind)} {extra}"
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


def _check_failure_path() -> None:
    """_deliver must survive a download AND a reply that both blow up.

    Doubles built inline: a message whose reply_text always raises, and a stand-in
    for download_into that raises before any network call. No mocking framework.
    """

    class ExplodingMessage:
        def __init__(self) -> None:
            self.attempts = 0

        async def reply_text(self, _text: str, **_kwargs: object) -> None:
            self.attempts += 1
            raise telegram.error.TimedOut

    class Captured(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.errors: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.ERROR:
                self.errors.append(record.getMessage())

    def _exploding_download(_url: str, _target_dir: Path) -> Media:
        raise ExtractionError("simulated extractor failure")

    captured = Captured()
    log.addHandler(captured)
    real_download = globals()["download_into"]
    globals()["download_into"] = _exploding_download
    # Both failures are deliberate here, so keep their tracebacks out of the
    # self-check's output -- they are asserted on below, not ignored.
    log.propagate = False
    try:
        message = ExplodingMessage()
        # If this raises, _deliver's except block is not a net and the group gets
        # nothing -- the exact live failure this guards against.
        asyncio.run(_deliver(message, "https://youtu.be/never-fetched"))
    finally:
        log.propagate = True
        globals()["download_into"] = real_download
        log.removeHandler(captured)

    assert message.attempts == 1, f"the apology must be attempted exactly once, got {message.attempts}"
    assert len(captured.errors) == 2, f"both failures must be logged, got {captured.errors}"
    assert any("failed to deliver" in m for m in captured.errors), captured.errors
    assert any("got nothing" in m for m in captured.errors), captured.errors
    print("ok  _deliver swallows a failing download AND a failing apology")


def _self_check() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    _check_pure_helpers()
    _check_send_timeouts()
    _check_failure_path()
    _check_extraction()
    print("\nself-check passed")


if __name__ == "__main__":
    if "--self-check" in sys.argv[1:]:
        _self_check()
    else:
        main()
