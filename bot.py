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
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager, redirect_stdout
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

# Telegram provides no asymmetry: two pollers on one token both get 409, so two
# instances running the same rule reach the same conclusion and both give up --
# measured on 2026-08-09, and it left the group with no bot at all (README.md §4.9).
# The asymmetry has to be injected from outside, and the product already collects it:
# the launcher asks "somebody else has it, shall I take it?" and passes this flag when
# the person says yes. Nothing else in the repository sets it, so a normal start is
# byte-for-byte the start it was before this existed.
TAKE_OVER_FLAG = "--take-over"

# How long that declared intent lasts, counted from this process's start. It exists
# so the baton can be passed twice: without an expiry, the friend who took the bot at
# 18:00 would refuse to yield it at 20:00 and only the first hand-over of the day
# would work. It has to comfortably outlast the incumbent's CONFLICT_GRACE, because
# the intent must still be live when the hand-over conflict starts -- which is within
# a second or two of startup, so twice the grace is already generous.
TAKE_OVER_WINDOW = 120.0  # seconds

# How long a taking-over instance keeps quiet before it says the hand-over is not
# going through. Past this, the other side is not an incumbent of this build -- an
# incumbent yields at CONFLICT_GRACE -- so it is most likely a second person who also
# answered yes. Three times the grace: long enough that a yielding incumbent is
# always gone first, short enough that somebody watching the window still learns why.
CONFLICT_STANDOFF = 180.0  # seconds

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

# Only scheme-carrying URLs count HERE. A bare "instagram.com" mentioned mid-sentence
# is someone talking about a site, not a link to fetch -- and this pattern has no way
# to tell those apart, which is why it keeps insisting on a scheme.
#
# Telegram can tell them apart, because its client already decided which words to
# underline, so entity_urls() reads that decision and message_urls() unions the two.
# This pattern is the first half of that union and its contract has not changed: do
# not loosen it to catch bare domains, that job now belongs to the entities.
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
    "album": "reply_media_group",
}

# Telegram's album size, quoted verbatim from the sendMediaGroup docs on 2026-08-09:
# "A JSON-serialized Array describing messages to be sent, must include 2-10 items."
#
# Taken from python-telegram-bot's own enum rather than typed as 2 and 10, so there
# is one source of truth and a library bump cannot leave a stale literal here. The
# self-check asserts the values ARE 2 and 10, so a change is loud instead of silent.
#
# python-telegram-bot does NOT enforce either bound: send_media_group only mentions
# them in its docstring (read at 22.8), so an 11-item call is rejected by Telegram's
# server, not locally. The cap below is the bot's own, and it has to be.
ALBUM_MIN_ITEMS = int(telegram.constants.MediaGroupLimit.MIN_MEDIA_LENGTH)
ALBUM_MAX_ITEMS = int(telegram.constants.MediaGroupLimit.MAX_MEDIA_LENGTH)

FAILURE_REPLY = "no pude bajar ese link"

# The failures the bot can NAME, and the Spanish line each one earns. Data: the
# matcher is failure_reply() and never needs touching to add a row.
#
# Every row is a signature measured on 2026-08-09 by running download_into against
# the live site -- NOT by reading `yt-dlp --simulate`, because the image fallback
# rewrites some of those before they ever reach the group (see the note on the last
# row). A failure nobody has actually seen does not get a row: guessing at the cause
# is the thing this table exists to stop.
#
# A row is (markers, reply). ALL markers must appear, casefolded, in the failure
# text; first row that matches wins, and the rows are disjoint today. Two markers
# rather than one wherever the message alone is common prose -- the extractor tag
# ("[facebook]", "[instagram:user]") is what pins the row to a host, and brackets
# and colons are also what keep a marker from matching the pasted URL.
#
# HONESTY IS THE HARD PART. Three of these signatures do not carry the certainty a
# reader would want, and the reply must not invent it:
#   * Facebook's "Cannot parse data" is dead post OR Facebook throttling us. An
#     earlier agent hit it on a perfectly good URL after five self-checks in 25
#     minutes, so "ese post no existe" would be a confident lie ~half the time.
#   * Instagram's empty media response is private, deleted, or auth-walled.
#   * "no video and no downloadable image" is whatever the site refused to give.
# Those three offer both possibilities and suggest what the friend can do.
#
# ponytail: these strings are upstream prose and they WILL drift -- yt-dlp rewords
# its own errors between releases and Instagram rewords the sentence yt-dlp quotes.
# The failure mode is deliberately the safe one: a reworded signature matches no row
# and the group gets FAILURE_REPLY, which is exactly today's behaviour and never a
# wrong specific answer. Drift shows up as rows that stop firing, and the ledger is
# where the new wording is found, because it keeps the raw detail whatever was said.
# Upgrade path when a row goes quiet: read `bot.py --rejected`, copy the new
# sentence, add a row. Do not soften the markers to "catch more" -- a loose marker
# is how a row starts firing on the wrong failure, which is the one outcome worse
# than the generic apology.
FAILURE_SIGNATURES: tuple[tuple[tuple[str, ...], str], ...] = (
    # instagram.com/reel/DbpG4CuSKoG/ -- the real bounce that prompted all of this.
    (("this content isn't available to everyone",),
     "ese post de instagram no es público, no me deja verlo"),
    # instagram.com/reel/AAAAAAAAAAA/ -- also what an auth-walled post gives.
    (("instagram sent an empty media response",),
     "no puedo ver ese post de instagram: puede que sea privado o que ya no exista"),
    # instagram.com/nasa/ -- a profile link instead of a post. The one row where the
    # friend can fix it themselves, so the reply says how.
    (("[instagram:user]", "unable to extract data"),
     "ese link es de un perfil de instagram, no de un post: pasame el del reel o la foto"),
    # facebook.com/watch/?v=999999999999999 -- dead post or throttling, no way to tell.
    (("[facebook]", "cannot parse data"),
     "no pude abrir ese link de facebook: puede que ya no exista o que facebook me esté "
     "frenando. probá de nuevo en un rato"),
    # youtube.com/watch?v=AAAAAAAAAAA and ?v=ZZZZZZZZZZZ, byte-identical for both.
    # This row used to be keyed on the bot's OWN sentence ("has no video and no
    # downloadable image either"), because the image fallback's error replaced
    # yt-dlp's before anything downstream saw it. That defect is fixed and the
    # extractor's own words now survive, so the row is keyed on them -- re-measured
    # against the live site on 2026-08-09 after the fix. The old key is deliberately
    # gone rather than kept "just in case": no measured failure reaches it any more,
    # and an unreachable row is the thing this table's checks exist to catch.
    (("[youtube]", "video unavailable"),
     "no encontré nada para bajar en ese link: puede que lo hayan borrado o que sea privado"),
)

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

# Terminal colour, on its way into a file that exists to be read and grepped. A live
# record from 2026-08-09T17:17 carries yt-dlp's red "ERROR:" as the literal bytes
# \x1b[0;31mERROR:\x1b[0m, and a second record of the SAME failure nine minutes later
# carries none -- so the ledger is already inconsistent with itself.
#
# CSI only: colour is SGR, and that is the whole observed defect. A stray escape of
# some other family would survive, which is a cosmetic miss in a diagnostic file.
#
# ponytail: WHY one run colours and the next does not is NOT established. The obvious
# theory -- "yt-dlp colours a TTY and not a pipe" -- was tested both ways, pipe and
# pseudo-TTY, and produced no escapes at all either time, so it is refuted and the
# real trigger is unknown. That is exactly why this strips unconditionally instead of
# asking whether colour is on: a guard on a TTY check, an env var or a yt-dlp flag
# would be a guard on the wrong thing and would silently do nothing. Stripping text
# that has no escapes is a no-op, so there is nothing to gain by being cleverer.
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]")

# The `error` field of a record is an exception class name wherever there is an
# exception. The oversize path has none -- nothing failed, the file simply does not
# fit -- so it gets this token instead, which groups the same way.
OVERSIZE_ERROR = "OversizeForTelegram"

# And this one is for a link the bot never even tried: the host is not in
# SUPPORTED_HOSTS. It has to be its own class rather than share one with a real
# failure, because the two lead to opposite actions -- a bounce means something
# rotted and needs fixing, an unattempted link means the group keeps pasting a site
# the bot does not know, and the fix is deciding whether to support it. Grouped
# separately by format_rejections, which is the whole point: "the group pasted
# TikTok eight times this week" is the one signal that decides what to build next,
# and until now it lived only in a runtime log that dies with the terminal window.
UNSUPPORTED_ERROR = "UnsupportedHost"

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
    # An album: every slide of an all-image carousel, in the order they were posted.
    # Empty for everything else, and `path` stays the first slide so nothing that
    # reads a single file has to learn about carousels.
    slides: tuple[Path, ...] = ()
    # How many slides the post actually had, which is not len(slides) when the post
    # is longer than an album. The difference is what the group gets told about.
    slide_total: int = 0


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


def _carousel_options(target_dir: Path) -> dict:
    """`_no_formats_ok` without the one-slide cap. The slides ARE the post.

    `_ydl_options` sets `playlist_items: "1"`, which is right for the video path --
    a link to a post is a request for that post, not for a playlist. It is wrong
    while inspecting a carousel: measured on 2026-08-09 against the reference post,
    the probe returns `entries: 1` with the cap and `entries: 10` without it, while
    reporting `playlist_count: 10` either way. Detection written against the capped
    dict would be reading a truth the bot never sees.

    `noplaylist: True` is left in place deliberately: Instagram's extractor returns
    the carousel as a playlist regardless (measured, same run), so removing it would
    change nothing here and would loosen the video path's contract for no reason.
    """
    return _no_formats_ok(target_dir) | {"playlist_items": None}


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
    flag set, so on Instagram a failed extraction never reaches this function at all.

    That last sentence was written as "a failed extraction never reaches this function
    at all", full stop, and it does NOT generalise off Instagram. Measured 2026-08-09
    and again 2026-08-09 on this branch: an unavailable YouTube video
    (`watch?v=AAAAAAAAAAA`) does not raise under the flag -- the probe returns no
    formats and 38 thumbnails, so THIS FUNCTION SAYS TRUE FOR A FAILED EXTRACTION.

    So this function's answer is provisional off Instagram, and nothing keyed on it
    may treat it as final. The discrimination it cannot make -- "video-less post" vs
    "extraction that produced nothing" -- is made one step later, in _image_fallback,
    by the only signal that is not a guess: whether any of those thumbnails yields an
    image. None of the 38 does, because YouTube's are synthesised from a URL template
    rather than extracted, so the fallback declines and the caller re-raises yt-dlp's
    own "Video unavailable". Do not try to move that decision up here: the metadata
    an image post and a dead video hand back is the same shape, and every up-front
    signal available (a `preference` key, a placeholder title, a thumbnail count)
    would put the WORKING image path at risk to save requests on a failing one.

    Note what is NOT used here: `duration` is None for the image post AND for a
    working reel, and `title` is "Video by <author>" even for an image, because that
    is Instagram's generic caption. Neither discriminates anything.
    """
    if not info:
        return False
    if info.get("formats"):
        return False
    # A multi-item carousel is not this: it has no thumbnails of its own, and its
    # slides live in `entries`. It is handled by carousel_slides() below, and this
    # function must keep refusing it -- one post, one photo, is the contract the
    # single-image path is asserted against.
    if info.get("entries"):
        return False
    return bool(info.get("thumbnails"))


def carousel_slides(info: dict | None) -> list[dict]:
    """The entries of an all-image carousel, in order. `[]` for anything else.

    Pure, and the whole safety of the album path lives here, so it is asserted with
    plain dicts exactly like `is_image_post`. Measured against the reference
    carousel on 2026-08-09: `_type: playlist`, no top-level formats, no top-level
    thumbnails, `entries: 10`, and every entry `formats: 0` with `thumbnails: 13`.

    The guards, and why each one is not optional:

    * **Top-level formats mean a video**, same first question as `is_image_post`. A
      post that offers video formats and then fails to download them is an error;
      an album of poster frames would be the silent degradation this repo forbids.
    * **Fewer than two entries is not an album.** Telegram's own floor is 2, and a
      one-entry playlist is the single-image path, which already works.
    * **Any entry with formats means a video slide.** Measured on 2026-08-09,
      `instagram.com/p/BQ0eAlwhDrw/` is a live all-video carousel: 3 entries, each
      with 4 formats and 12 thumbnails. Sending that as photos would turn three
      videos into three poster frames. Note this guard only ever runs after the
      video path has already FAILED on such a post -- while it works, that carousel
      never reaches the fallback -- which is why the live entry in SELF_CHECK_URLS
      cannot exercise it and the dict asserts are the only cover it has.
      ponytail: this refuses the MIXED photo/video carousel as well, which is the
      case yt-dlp still handles badly upstream (#7569, #11792). The one public
      example those issues name, `instagram.com/p/CtXtwOop1W5/`, is auth-walled
      today -- "Instagram sent an empty media response", measured 2026-08-09 -- so a
      mixed carousel remains unmeasured and the apology is the honest answer.
      Upgrade path: find a live one, check whether the video entries download under
      MEDIA_FORMAT, and only then build the mixed album. Telegram itself allows the
      mix: its sendMediaGroup docs restrict only documents and audio to same-type
      albums.
    * **An entry with no thumbnails has nothing to send**, and a hole in the middle
      of an album is worse than an apology.
    """
    if not info:
        return []
    if info.get("formats"):
        return []
    entries = [entry for entry in (info.get("entries") or ()) if isinstance(entry, dict)]
    if len(entries) < ALBUM_MIN_ITEMS:
        return []
    if any(entry.get("formats") for entry in entries):
        return []
    if not all(entry.get("thumbnails") for entry in entries):
        return []
    return entries


def _image_fallback(url: str, target_dir: Path) -> Media | None:
    """A Media photo or album if `url` is a post with no video in it, else None.

    Returning None means "not an image post" and the caller re-raises the original
    download failure, so a broken extraction stays an error and never degrades into
    a still image. THREE things return None, and the third is the whole point of the
    branch below: the probe raising, the post being neither single image nor
    all-image carousel, and thumbnails that produce no image. Nothing in here may
    raise an error of its own on the single-image path -- that error would replace
    the extractor's and the ledger would record the fallback's opinion instead of
    the cause.

    One probe answers both questions -- a single post and a carousel differ only in
    the same info dict -- so the carousel costs the single-image path no extra round
    trip. `is_image_post` is asked first and the two are disjoint by construction:
    it refuses anything with entries, and a carousel needs at least two of them.
    """
    try:
        with yt_dlp.YoutubeDL(_carousel_options(target_dir)) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError:
        return None  # extraction is genuinely broken, not merely video-less
    if is_image_post(info):
        photo = _download_best_thumbnail(url, target_dir)
        if photo is None:
            # THE discrimination is_image_post() cannot make from metadata alone.
            # It answered "no video formats + thumbnails" and that is as far as a
            # dict can go: an unavailable YouTube video comes back from this same
            # probe with no formats and 38 SYNTHESISED thumbnail URLs -- built from
            # a template, never checked, all of them 404 (measured 2026-08-09).
            # The signal that separates the two is the only one that is not a guess:
            # thumbnails that yield no image at all. That post never was an image
            # post, so this is not a new failure to report -- it is the fallback
            # declining, exactly like the carousel branch below, and returning None
            # is what lets download_into re-raise the extractor's own words.
            log.info("%s offered thumbnails but no image; keeping the original failure", url)
            return None
        log.info("%s has no video; sending its image instead", url)
        return Media(path=photo, has_audio=False)
    slides = carousel_slides(info)
    if not slides:
        return None
    log.info("%s is a carousel of %d images; sending them as an album", url, len(slides))
    # ponytail: `?img_index=N` in the pasted URL is ignored on purpose. Instagram
    # writes it from whichever slide the sharer happened to be looking at, so
    # reading it as "send this one" is a guess about intent; the album is the safe
    # superset and the slide they meant is inside it. The owner's original link
    # carried img_index=9 and the self-check still uses it, so this stays proven
    # rather than merely claimed. Upgrade path if the group ever complains that ten
    # photos are noise: parse img_index and send that single slide, keeping the
    # album for links that carry no index.
    paths = _download_carousel_slides(url, target_dir)
    return Media(
        path=paths[0],
        has_audio=False,
        slides=tuple(paths),
        slide_total=len(slides),
    )


def _download_carousel_slides(url: str, target_dir: Path) -> list[Path]:
    """The best image of each carousel slide, in the order they were posted.

    The selection is `_download_best_thumbnail`'s, per slide, and for the same
    measured reason (§4.8): a carousel entry's thumbnails carry `id` and `url` and
    nothing else -- no width, no height, no reliable ordering -- so every thumbnail
    is fetched and the largest file wins. Verified on the reference carousel,
    2026-08-09: 10 slides x 13 thumbnails = 130 files, 6.3 MB, 17.4 s, and the ten
    winners are 97 KB to 260 KB.

    ponytail: 130 requests to send 10 images, and ~17 s of a blocked handler
    (PTB processes updates sequentially). Accepted at ~20 links a week, and the
    cheap-looking alternative is the one §4.8 already rejected: yt-dlp's own
    `writethumbnail` picks the last entry of an unordered list. The real upgrade
    path, if this ever bites, is fetching the slides concurrently -- not trusting
    the ordering.

    The download is capped at ALBUM_MAX_ITEMS because nothing beyond it can be sent;
    the caller already knows the true slide count from the probe and tells the group.
    """
    options = _carousel_options(target_dir) | {
        "simulate": False,
        "skip_download": True,
        "writethumbnail": True,
        "write_all_thumbnails": True,
        "playlist_items": f"1:{ALBUM_MAX_ITEMS}",
        # "slide-007.12.jpg": the zero-padded playlist index groups and sorts the
        # slides, and the thumbnail id after it is what yt-dlp appends per image.
        "outtmpl": str(target_dir / "slide-%(playlist_index)03d.%(ext)s"),
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise ExtractionError(f"yt-dlp could not fetch the carousel of {url}: {exc}") from exc

    groups: dict[str, list[Path]] = {}
    for path in target_dir.iterdir():
        if path.is_file() and path.suffix.lower() in PHOTO_SUFFIXES and path.stat().st_size > 0:
            groups.setdefault(path.name.split(".")[0], []).append(path)

    chosen = []
    for key in sorted(groups):
        group = groups[key]
        best = max(group, key=lambda path: path.stat().st_size)
        chosen.append(best)
    if len(chosen) < ALBUM_MIN_ITEMS:
        # The probe said this was a carousel and the download disagreed. That is an
        # error, not an album of one -- degrading here would deliver a random slide.
        raise ExtractionError(
            f"{url} looked like a carousel but only {len(chosen)} slide(s) came down"
        )
    return chosen


def _download_best_thumbnail(url: str, target_dir: Path) -> Path | None:
    """Fetch every thumbnail of an image post and keep the biggest file.

    Returns None when not one of them came down. That is not a failure to report:
    it means the post never was an image post and the caller must fall back to the
    error that actually happened. Raising here instead is what threw away yt-dlp's
    diagnosis for every failing YouTube link -- see _image_fallback.

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
        return None
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


def entity_urls(message: telegram.Message) -> list[str]:
    """Every link Telegram itself marked in the message, in order, carrying a scheme.

    The second source of URLs, and the reason `youtu.be/xyz` is no longer invisible:
    Telegram linkifies a bare domain in its own client, and `URL_PATTERN` requires a
    scheme, so until now the bot never saw a link the group could plainly see and tap.

    ONLY `url` entities are taken. Telegram has two entity types that carry a link
    and they are not the same thing:

      * `url` -- text Telegram itself recognised as a link. What the entity says IS
        what the group sees on screen, which is the whole reason it is safe to act on.
      * `text_link` -- a URL hidden behind display text, the thing Telegram's "create
        link" formatting produces. Refused. This bot's contract is "paste a link, get
        the media", and honouring a text_link means downloading something nobody in
        the chat can read -- the same instinct that keeps raw error text out of the
        group. Refusing is also the self-correcting mistake: nothing happens and the
        friend pastes the plain link. Nothing else is a link at all -- `mention`,
        `email`, `phone_number` and the formatting types are excluded by asking for
        one type rather than by listing what to skip, so a new entity type in a
        future Bot API cannot quietly start feeding yt-dlp.
        ponytail: no measured case has needed text_link, and this repo does not add
        behaviour on a guess. It is also invisible while refused -- a message that
        carries nothing else logs "no URL recognised" and writes no ledger record,
        so nobody will see demand for it building up. Upgrade path if a friend ever
        reports "le mandé un link y no hizo nada" and the message turns out to be
        formatted: read `entity.url` for this type instead of the text slice, and
        decide then whether the group should be told which URL was taken.

    A caption is the same story with different attribute names -- MESSAGE_FILTER
    accepts captions, so a video posted with a link in its caption has to work too.

    The offsets are the trap. Telegram counts them in UTF-16 code units, not Python
    characters, so one emoji before the link shifts every naive slice by one and the
    URL comes back with its first character eaten -- "outu.be/abc", which is_supported
    then rejects, silently. python-telegram-bot's own parse_entity does the UTF-16
    round trip, so this asks the library rather than re-deriving it here.
    """
    text = getattr(message, "text", None)
    if text:
        entities = getattr(message, "entities", None) or ()
        parse = getattr(message, "parse_entity", None)
    else:
        text = getattr(message, "caption", None)
        entities = getattr(message, "caption_entities", None) or ()
        parse = getattr(message, "parse_caption_entity", None)
    if not text or not entities or parse is None:
        return []

    found: list[str] = []
    for entity in entities:
        if getattr(entity, "type", None) != telegram.MessageEntity.URL:
            continue
        url = parse(entity).strip().rstrip(URL_TRAILING_JUNK)
        if not url:
            continue
        # Telegram linkifies a bare domain, and tapping it opens https. Without a
        # scheme urlparse reads the whole thing as a path and hands is_supported no
        # hostname at all, so the link would be dropped one line later.
        if "://" not in url:
            url = "https://" + url
        if url not in found:
            found.append(url)
    return found


def message_urls(message: telegram.Message) -> list[str]:
    """Every URL in a message, from both sources, de-duplicated. Regex first.

    A union, never a replacement. The regex is what works today and it stays first,
    so any message that already produced a list produces exactly the same list in
    exactly the same order; the entities can only ever ADD the links Telegram saw
    and the pattern could not. The same link arriving from both sources is one link:
    both sides strip the same trailing punctuation and both carry a scheme by the
    time they are compared, so `youtu.be/abc` and `https://youtu.be/abc` in one
    message are recognised as the same URL rather than downloaded twice.
    """
    urls = find_urls(getattr(message, "text", None) or getattr(message, "caption", None))
    for url in entity_urls(message):
        if url not in urls:
            urls.append(url)
    return urls


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
    """Telegram's bot-upload ceiling for this media kind, in bytes.

    An album's items are photos and are held to the photo ceiling, not the 50 MiB
    one -- the album is not one 50 MiB upload, it is N photos in one call.
    """
    return TELEGRAM_MAX_PHOTO_UPLOAD if kind in ("photo", "album") else TELEGRAM_MAX_UPLOAD


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


def delivered_files(media: Media) -> tuple[Path, ...]:
    """Every file this Media will put in the chat: the album's slides, or the one file."""
    return media.slides or (media.path,)


def delivery_kind(media: Media) -> str:
    """What this Media is delivered as: "album", or whatever media_kind says.

    Separate from `media_kind` on purpose. `media_kind` answers "what is this file",
    which for an album slide is honestly "photo"; this answers "what call sends it",
    and only the Media knows there is more than one file.
    """
    if media.slides:
        return "album"
    return media_kind(media.path, media.has_audio)


def oversize_reply(size_bytes: int, link: str) -> str:
    """The Spanish message sent instead of a file that will not fit."""
    megabytes = size_bytes / 1024 / 1024
    return f"pesa {megabytes:.0f} MB y Telegram no me deja subirlo. Te lo dejo acá: {link}"


def failure_reply(detail: str) -> str:
    """The Spanish line the group gets for a failure whose text is `detail`.

    The logic half of FAILURE_SIGNATURES, and all of it: find the first row whose
    markers are all present, else fall back to FAILURE_REPLY. Adding a failure the
    bot can name is adding a row up there; nothing here changes.

    Casefolded so a wording that only differs in capitals still matches, and the
    escapes come off first so a coloured message classifies exactly like a clean one
    -- the ledger already learned that lesson the expensive way.

    Unrecognised is not a guess. Anything this does not recognise is still
    "no pude bajar ese link", the same sentence it has always been.
    """
    haystack = strip_ansi(detail).casefold()
    for markers, reply in FAILURE_SIGNATURES:
        if all(marker in haystack for marker in markers):
            return reply
    return FAILURE_REPLY


def album_truncation_note(total: int, sent: int) -> str:
    """The Spanish warning for a carousel longer than one album.

    A truncated post must never arrive silently: the group has to know a slide is
    missing, otherwise the bot is quietly lying about what was posted. Said before
    the album rather than after it, so the warning cannot be the message that got
    lost.
    """
    return (
        f"el post tiene {total} fotos y Telegram me deja mandar {sent} juntas, "
        f"así que te mando las primeras {sent}"
    )


def take_over_requested(argv: Sequence[str]) -> bool:
    """True when this instance was started to take the bot away from somebody else.

    An argument rather than an environment variable on purpose. `bot.py` already owns
    a small command line (`--self-check`, `--rejected`), so the intent joins the
    channel that exists instead of inventing a second one; it cannot be inherited by
    accident from a shell, or from the `.env` the macOS launcher exports wholesale
    with `set -a`; and it is per-invocation by construction, which is exactly what an
    intent declared once, at one double-click, is.

    Anything that is not the flag -- nothing, a typo, a value glued onto it -- is a
    normal start. The unclear reading has to be the yielding one: an instance that
    wrongly believes it was told to take over never gives the baton back.
    """
    return TAKE_OVER_FLAG in argv


def conflict_action(
    now: float,
    started: float | None,
    last: float | None,
    take_over_until: float | None = None,
) -> tuple[str, float | None, float | None]:
    """Decide what a poll conflict at `now` means, given the episode so far.

    Returns the action and the new (started, last) pair, so the whole rule is one
    pure function that can be asserted without a network or a second bot:

    * `announce`     -- the first conflict of an episode on an instance nobody told
      to take the bot over. Say it once.
    * `take-over`    -- the first conflict of an episode on an instance that was told
      to. The same event, read the other way round: this one is the one arriving.
    * `quiet`        -- a repeat inside the same episode. python-telegram-bot retries
      on a growing backoff, so a single competing poller produces a stream of these;
      logging each one is how the old behaviour became a wall of text.
    * `give-up`      -- the episode has lasted CONFLICT_GRACE on an instance that was
      not taking over. Somebody has taken the baton and this one is receiving nothing.
    * `stand-ground` -- the episode has lasted CONFLICT_STANDOFF on an instance that
      WAS taking over: the other side is not yielding either. Said once, and this
      instance keeps polling.

    `take_over_until` is the monotonic time until which the intent the person declared
    at the launcher applies, or None on a normal start. It is the only asymmetry in
    the whole feature: Telegram hands both pollers the same 409 and designates no
    winner, so with a symmetric rule both sides conclude they lost and the group is
    left with no bot -- measured 2026-08-09 (README.md §4.9).

    Two properties of that argument are load-bearing:

    * the role is decided from the *episode's* start, not from `now`, so an intent
      that expires in the middle of a conflict can never turn a standing-ground
      instance into a yielding one. That would put both sides back on the give-up
      path at once, which is the bug this exists to fix;
    * the intent expires, so the friend who took the bot at 18:00 is an ordinary
      incumbent by 18:30 and yields to the next person like anybody else. Without an
      expiry only the first hand-over would ever work.

    The episode ends after CONFLICT_EPISODE_GAP of silence. Without that reset, two
    unrelated probes an hour apart would look like one hour-long conflict and the
    second one would kill a perfectly healthy bot.
    """
    new_episode = started is None or last is None or now - last > CONFLICT_EPISODE_GAP
    episode_started = now if new_episode else started
    taking_over = take_over_until is not None and episode_started <= take_over_until
    if new_episode:
        return ("take-over" if taking_over else "announce"), now, now
    if taking_over:
        # Never `give-up` from here. This instance was told to take the bot, and the
        # other side cannot be observed: if it stops because it also thinks it lost,
        # a give-up here leaves nobody polling at all. Say once, on the conflict that
        # crosses CONFLICT_STANDOFF, that the hand-over is not going through -- then
        # stay quiet and keep polling.
        if last - episode_started < CONFLICT_STANDOFF <= now - episode_started:
            return "stand-ground", episode_started, now
        return "quiet", episode_started, now
    if now - episode_started >= CONFLICT_GRACE:
        return "give-up", None, None
    return "quiet", episode_started, now


# --------------------------------------------------------------------------------
# The rejected-links ledger. Diagnostics, never a dependency of delivery.
# --------------------------------------------------------------------------------


def strip_ansi(text: str) -> str:
    """`text` with terminal escape sequences removed. Pure, and safe on anything.

    Only the escapes go: "\x1b[0;31mERROR:\x1b[0m" becomes "ERROR:", and every
    bracket that a human typed -- "[Instagram]" -- stays, because those are the
    diagnosis. A pattern loose enough to eat the extractor's name would quietly
    destroy the most useful word in the record.
    """
    return ANSI_ESCAPE.sub("", text or "")


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

    Colour codes are stripped before the truncation, not after, so the 400 characters
    are 400 characters of diagnosis rather than of escape sequence.

    An empty `detail` is legitimate and stays empty: an unsupported host has no error
    text because nothing was attempted, and the class plus the URL are the whole
    record. format_rejections prints no detail line for those rather than a constant
    sentence repeated under every URL.
    """
    detail = strip_ansi(detail)
    first_line = detail.strip().splitlines()[0] if detail.strip() else ""
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

    "No URL recognised" now means neither the regex NOR Telegram found one, which is
    a much smaller set than it used to be -- see message_urls.

    The rejected URLs go in the log because they are the entire diagnosis. The
    message body does not: this is a private group.

    They also go in the ledger, under their own error class. The log answers "why
    did nothing happen just now" for whoever is watching the window; the ledger
    answers "what has this group been pasting that I do not support", which is a
    question nobody can ask a terminal that has been closed. Same privacy rule in
    both places: the URLs, never the body.
    """
    message = update.effective_message
    if message is None:
        return
    urls = message_urls(message)
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
        # Every one of them, not just the first: which sites recur IS the diagnosis,
        # and a message pasting three TikToks is three data points. The log line
        # above stays exactly as it was -- it serves the person watching the window
        # right now, the ledger serves the owner reading a month later.
        #
        # A message with no URL at all never gets here, deliberately: that is
        # ordinary chat, not a bounced link, and recording it would bury the signal
        # under every "jajaja" in the group. Neither does a message that mixed an
        # unsupported link with a supported one -- something WAS attempted there,
        # and calling the whole message unattempted would be false.
        for url in urls:
            record_rejection(message, url, UNSUPPORTED_ERROR, "")
        return
    for url in supported:
        await _deliver(message, url)


# The current conflict episode: when it began and when it was last seen. Module
# state because python-telegram-bot hands the error handler nothing to keep it in,
# and the rule that reads it -- conflict_action -- is pure and tested on its own.
_conflict_started: float | None = None
_conflict_last: float | None = None

# The monotonic time until which this instance was told to take the bot over, or None
# on a normal start. Set once in main() from the command line and never changed after,
# so the rule that reads it -- conflict_action -- still takes it as an argument and
# stays pure.
_take_over_until: float | None = None


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
        time.monotonic(), _conflict_started, _conflict_last, _take_over_until
    )
    if action == "quiet":
        return
    if action == "announce":
        log.warning("another instance has taken the poll; this one is receiving nothing meanwhile")
        print("Otra persona prendió el bot, así que este dejó de recibir mensajes.")
        return
    if action == "take-over":
        # The same 409 the other side is reading as a defeat. This window belongs to
        # the person who just answered "yes, take it", and telling them somebody else
        # has the bot would be the opposite of what they asked for.
        log.warning(
            "taking the poll over as instructed; the other instance should yield within %.0f s",
            CONFLICT_GRACE,
        )
        # "un minuto o dos", not "un minuto": CONFLICT_GRACE is a floor and PTB's
        # backoff decides when the conflict that crosses it is actually judged, which
        # measured 65 s and 74 s on the two instances of 2026-08-09.
        print("Se lo estoy sacando a quien lo tenía prendido. Puede tardar un minuto o dos.")
        return
    if action == "stand-ground":
        # Nothing here can arbitrate: the hosts are different laptops and Telegram
        # refuses to pick a winner. Stopping would risk leaving nobody polling, so
        # this says the one true thing it knows and hands the decision to the two
        # people, who -- unlike their bots -- can talk to each other.
        # ponytail: the ceiling is that two people who both answered yes keep two
        # instances knocking each other off until one of them closes the window; the
        # group's bot works erratically meanwhile instead of not at all. The upgrade
        # path is a real tie-break, and it needs a channel between the hosts that
        # this project deliberately does not have (README.md §6).
        log.warning(
            "still conflicting after %.0f s; the other instance is not yielding either",
            CONFLICT_STANDOFF,
        )
        print(
            "Parece que otra persona también lo está prendiendo y ninguno de los dos afloja. "
            "Pónganse de acuerdo: que uno cierre la ventana. Este sigue prendido mientras tanto."
        )
        return

    # `give-up`. What this instance actually observed is that it stopped receiving,
    # and the rest is inference about a process on somebody else's laptop that it
    # cannot see -- so the wording claims the observation, hedges the cause, and says
    # what to do if the guess was wrong. On 2026-08-09 it was wrong on both windows at
    # once and nobody investigated, because both had been told the other one had it.
    log.warning("the conflict lasted %.0f s; stopping so the window says so", CONFLICT_GRACE)
    print(
        "Este dejó de recibir mensajes, así que se apaga: parece que otra persona prendió el bot. "
        "Podés cerrar esta ventana. Si el grupo se queda sin bot, volvé a abrir este archivo."
    )
    application = getattr(context, "application", None)
    if application is not None:
        application.stop_running()


async def _deliver(message: telegram.Message, url: str) -> None:
    try:
        with temp_workspace() as workspace:
            # yt-dlp is blocking; keep it off the event loop so the bot stays responsive.
            media = await asyncio.to_thread(download_into, url, workspace)
            kind = delivery_kind(media)
            files = delivered_files(media)
            # The largest slide decides: Telegram rejects the whole album if one
            # item is over the ceiling, so measuring only the first would pass a
            # call that fails.
            size = max(path.stat().st_size for path in files)
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
            if kind == "album":
                dropped = media.slide_total - len(files)
                log.info(
                    "sending %s as an album of %d/%d slides (largest %d bytes)",
                    url, len(files), media.slide_total, size,
                )
                if dropped > 0:
                    # Before the album, not after: if this reply is the one that
                    # fails, the group gets the apology instead of a truncated post
                    # it was never told about.
                    await _reply_text(message, album_truncation_note(media.slide_total, len(files)))
                await _send_album(message, files)
                return
            log.info("sending %s as %s (%d bytes)", url, kind, size)
            await _send(message, kind, media)
    except Exception as exc:
        # Never a stack trace in the group. The real error goes to the log.
        log.exception("failed to deliver %s", url)
        detail = str(exc)
        # Written before the apology so a network bad enough to kill both still
        # leaves the diagnosis behind. It cannot raise, so it cannot cost the apology.
        # The ledger gets the RAW text whatever the group is told: the friendly line
        # is for the chat, the detail is the owner's diagnosis and must not be lost.
        record_rejection(message, url, type(exc).__name__, detail)
        await _apologise(message, detail)


async def _reply_text(message: telegram.Message, text: str) -> None:
    """Send a short text reply with timeouts of its own. May raise."""
    await message.reply_text(
        text,
        connect_timeout=CONNECT_TIMEOUT,
        write_timeout=TEXT_REPLY_TIMEOUT,
        read_timeout=TEXT_REPLY_TIMEOUT,
    )


async def _apologise(message: telegram.Message, detail: str = "") -> None:
    """Tell the group the link failed, naming the cause when `detail` allows it.

    `detail` is the failure's own text and is used only to pick the sentence --
    nothing from it is ever quoted to the group, which would put a URL, an extractor
    name or a stack fragment in front of people who cannot use any of them. With no
    detail, or a detail nothing recognises, this is the generic apology it has always
    been. See FAILURE_SIGNATURES for why some of those sentences hedge.

    This call may not raise, ever.

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
        await _reply_text(message, failure_reply(detail))
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


def album_media(paths: Sequence[Path], stack: ExitStack) -> list[telegram.InputMediaPhoto]:
    """The album, as InputMediaPhoto over OPEN FILES. The open file is the point.

    `InputMediaPhoto(Path(...))` looks correct and is not. Read at 22.8, its
    __init__ calls `parse_file_input(..., local_mode=True)` unconditionally --
    "we don't have access to the actual setting and want things to work in local
    mode" -- so a Path or a str becomes the literal string `file:///...`. Nothing
    downstream repairs it: RequestParameter only rewrites an InputMedia whose
    `.media` is an InputFile, so the request would carry
    `media: "file:///Users/..."` and upload zero bytes. api.telegram.org would
    reject that; only a local Bot API server would not.

    A file object goes down the other branch of `parse_file_input` and becomes an
    InputFile with an `attach://` URI, which is what actually uploads the picture.
    The self-check asserts the resulting `.media` is an InputFile, because this is
    invisible until it reaches the real group and this bot cannot test that.

    The caller owns the ExitStack so the handles close with the send, not before it.
    """
    return [
        telegram.InputMediaPhoto(stack.enter_context(path.open("rb"))) for path in paths
    ]


async def _send_album(message: telegram.Message, paths: Sequence[Path]) -> None:
    """Send every slide as one Telegram album -- one sendMediaGroup call."""
    reply = getattr(message, reply_method_name("album"))
    with ExitStack() as stack:
        await reply(
            album_media(paths, stack),
            connect_timeout=CONNECT_TIMEOUT,
            write_timeout=UPLOAD_TIMEOUT,
            read_timeout=UPLOAD_TIMEOUT,
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
    global _take_over_until

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not ffmpeg_path():
        log.warning("ffmpeg is not on PATH; merged-quality downloads will fail")
    if take_over_requested(sys.argv[1:]):
        _take_over_until = time.monotonic() + TAKE_OVER_WINDOW
        log.info(
            "told to take the bot over: a conflict starting in the next %.0f s will not stop this "
            "instance (see README.md: the baton pass)",
            TAKE_OVER_WINDOW,
        )
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
# Each entry is (url, expected media kind, expected file count). The kind is
# asserted, not just recorded: an Instagram image post coming back as a video -- or
# a reel coming back as its poster frame -- is the failure mode the image fallback
# can produce. The count is one for everything that is not an album.
#
# The last two are both carousels and they are here for opposite reasons. The image
# one must come back as a ten-slide album. The video one must keep coming back as a
# single video: its slides have formats, so the ordinary video path downloads the
# first one and the image fallback is never reached at all.
#
# Be precise about what that second entry proves, because it is easy to overclaim
# and it was overclaimed here first: it pins that video carousels still belong to
# the video path, which is the thing a future carousel change is most likely to
# break. It does NOT exercise carousel_slides' per-entry formats guard -- verified
# 2026-08-09 by deleting that guard and watching this entry stay green. That guard
# only ever runs when a video carousel's download has ALREADY failed, which is not
# a state any public URL can be held in, so it is asserted on dicts only.
SELF_CHECK_URLS = {
    "youtube": ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "video", 1),
    "instagram reel": ("https://www.instagram.com/reel/DbGNFqVKnB-/?igsh=OHFxM3dxdmIzdTQ5", "video", 1),
    "instagram image": ("https://www.instagram.com/p/DbvWPFQxPkI/?igsh=Mnd6dGdxajVzeGV5", "photo", 1),
    "facebook": ("https://www.facebook.com/share/v/1L8yZSLkWq/", "video", 1),
    "instagram carousel": ("https://www.instagram.com/p/DbcsX-BlkZX/?img_index=9", "album", 10),
    "instagram video carousel": ("https://www.instagram.com/p/BQ0eAlwhDrw/", "video", 1),
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

    # entity_urls and message_urls: the links only Telegram can see.
    #
    # NOTHING HERE IS END-TO-END. Nobody has posted a schemeless link into the real
    # group and watched what Telegram actually sends, so every entity below is one
    # this code constructed. The offsets are computed the way the Bot API documents
    # them -- UTF-16 code units -- and that is an assumption until a real paste
    # confirms it. See "Open, known" in AGENTS.md.
    def _entity(kind: str, offset: int, length: int, url: str | None = None) -> telegram.MessageEntity:
        return telegram.MessageEntity(type=kind, offset=offset, length=length, url=url)

    def _message(text: str, *entities: telegram.MessageEntity, caption: bool = False) -> telegram.Message:
        body = {"caption": text, "caption_entities": entities} if caption else \
               {"text": text, "entities": entities}
        return telegram.Message(
            message_id=1,
            date=dt.datetime.fromtimestamp(0, dt.timezone.utc),
            chat=telegram.Chat(id=-100, type=telegram.Chat.GROUP),
            from_user=telegram.User(id=1, first_name="u", is_bot=False),
            **body,
        )

    # A message with no entities at all is exactly what it was before.
    assert message_urls(_message("mira https://youtu.be/abc")) == ["https://youtu.be/abc"]
    assert message_urls(_message("che alguien vio el partido")) == []
    assert entity_urls(_message("mira https://youtu.be/abc")) == [], "no entities, no second source"

    # Scheme-carrying, from both sources: ONE link, not two.
    both = _message("mira https://youtu.be/abc", _entity("url", 5, 20))
    assert entity_urls(both) == ["https://youtu.be/abc"], entity_urls(both)
    assert message_urls(both) == ["https://youtu.be/abc"], "the same link from both sources is one"

    # Schemeless: the whole point. The regex sees nothing; Telegram saw a link.
    bare = _message("mira youtu.be/abc", _entity("url", 5, 12))
    assert find_urls("mira youtu.be/abc") == [], "the regex still requires a scheme"
    assert message_urls(bare) == ["https://youtu.be/abc"], message_urls(bare)
    assert is_supported(message_urls(bare)[0]), "a schemeless link must survive is_supported"

    # Both kinds in one message, and the union keeps the regex's order first so that
    # nothing which worked before changes shape.
    mixed = _message(
        "https://youtu.be/abc y tiktok.com/@a/video/1",
        _entity("url", 0, 20), _entity("url", 23, 21),
    )
    assert message_urls(mixed) == ["https://youtu.be/abc", "https://tiktok.com/@a/video/1"], \
        message_urls(mixed)

    # The same link written both ways in one message is still one link.
    twice = _message("youtu.be/abc y https://youtu.be/abc",
                     _entity("url", 0, 12), _entity("url", 15, 20))
    assert message_urls(twice) == ["https://youtu.be/abc"], message_urls(twice)

    # THE TRAP. Telegram counts offsets in UTF-16 code units and an emoji outside the
    # BMP is TWO of them while being one Python character, so text[offset:] eats the
    # first letter of the link -- "outu.be/abc", which is_supported then rejects
    # without a word. This assert is the difference between the feature working and
    # failing silently for every message that starts with an emoji, which in this
    # group is most of them.
    emoji = _message("\U0001f602 youtu.be/abc", _entity("url", 3, 12))
    assert emoji.text[3:15] == "outu.be/abc", "the naive slice really is wrong here"
    assert message_urls(emoji) == ["https://youtu.be/abc"], message_urls(emoji)
    # Two of them, and one inside the sentence, so an off-by-one in either direction
    # is caught rather than cancelling out.
    emojis = _message("\U0001f602\U0001f602 mira \U0001f525 youtu.be/abc ahora",
                      _entity("url", 13, 12))
    assert message_urls(emojis) == ["https://youtu.be/abc"], message_urls(emojis)

    # A caption is the other half of MESSAGE_FILTER and uses different attributes.
    captioned = _message("mira youtu.be/abc", _entity("url", 5, 12), caption=True)
    assert message_urls(captioned) == ["https://youtu.be/abc"], message_urls(captioned)

    # A hidden URL behind display text is refused, deliberately: the group cannot
    # read it. Refusing it must not also lose the plain link next to it.
    hidden = _message("mira esto y https://youtu.be/abc",
                      _entity("text_link", 0, 9, "https://www.instagram.com/p/x/"),
                      _entity("url", 12, 20))
    assert message_urls(hidden) == ["https://youtu.be/abc"], message_urls(hidden)
    # And nothing that is not a link may reach yt-dlp because it happens to be marked.
    for kind in ("mention", "hashtag", "bold", "code", "email", "phone_number"):
        noise = _message("nasa@example.com", _entity(kind, 0, 16))
        assert message_urls(noise) == [], f"{kind} is not a link: {message_urls(noise)}"
    print("ok  entity_urls unions Telegram's own links in, offsets and all")

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

    # The album's bounds are Telegram's, taken from PTB's enum. If a library bump
    # ever moves them, this fails loudly instead of the bot guessing.
    assert (ALBUM_MIN_ITEMS, ALBUM_MAX_ITEMS) == (2, 10), (ALBUM_MIN_ITEMS, ALBUM_MAX_ITEMS)
    assert upload_ceiling("album") == TELEGRAM_MAX_PHOTO_UPLOAD, "album items are photos"
    assert delivery_decision(TELEGRAM_MAX_PHOTO_UPLOAD + 1, "album") == "link"
    print("ok  album bounds follow telegram.constants.MediaGroupLimit")

    # carousel_slides: the guard that keeps a video carousel from becoming stills.
    image_entry = {"formats": [], "thumbnails": thumbs}
    video_entry = {"formats": [{"format_id": "hd"}], "thumbnails": thumbs}
    ten = [dict(image_entry) for _ in range(10)]
    assert len(carousel_slides({"formats": [], "entries": ten})) == 10, "ten image slides is an album"
    assert len(carousel_slides({"entries": [dict(image_entry), dict(image_entry)]})) == 2, "two is the floor"
    assert carousel_slides({"entries": [dict(image_entry)]}) == [], "one entry is the single-post path"
    # The one that matters, and it is asserted HERE ONLY: the guard runs only after
    # a video carousel's download has already failed, and no public URL can be held
    # in that state. A slide with formats is a video, and three videos must never
    # become three poster frames. This also covers the unmeasured mixed carousel.
    assert carousel_slides({"entries": [dict(video_entry) for _ in range(3)]}) == [], (
        "an all-video carousel must never be delivered as photos"
    )
    assert carousel_slides({"entries": [dict(image_entry), dict(video_entry)]}) == [], (
        "a mixed photo/video carousel is unmeasured and must be refused, not half-sent"
    )
    assert carousel_slides({"formats": [{"format_id": "hd"}], "entries": ten}) == [], (
        "a post with its own video formats is a video, whatever its entries say"
    )
    assert carousel_slides({"entries": [dict(image_entry), {"formats": [], "thumbnails": []}]}) == [], (
        "a slide with no image would be a hole in the middle of the album"
    )
    assert carousel_slides({}) == [] and carousel_slides(None) == [], "no info is not a carousel"
    assert carousel_slides({"formats": [], "thumbnails": thumbs}) == [], "a single image post is not"
    # is_image_post and carousel_slides must stay disjoint: exactly one of them can
    # ever claim the same info dict, or the fallback would depend on its own order.
    for info in ({"formats": [], "thumbnails": thumbs}, {"entries": ten}, {"entries": [image_entry]},
                 {"formats": [{"format_id": "hd"}]}, {}):
        assert not (is_image_post(info) and carousel_slides(info)), info
    print("ok  carousel_slides")

    # delivery_kind and delivered_files: one file or many, one place that decides.
    photo = Media(Path("a.jpg"), has_audio=False)
    album = Media(Path("slide-001.12.jpg"), has_audio=False,
                  slides=(Path("slide-001.12.jpg"), Path("slide-002.12.jpg")), slide_total=2)
    assert delivery_kind(photo) == "photo" and delivered_files(photo) == (Path("a.jpg"),)
    assert delivery_kind(Media(Path("c.mp4"), has_audio=True)) == "video"
    assert delivery_kind(Media(Path("c.mp4"), has_audio=False)) == "animation"
    assert delivery_kind(album) == "album", "slides make it an album, whatever the suffix says"
    assert len(delivered_files(album)) == 2
    assert reply_method_name("album") == "reply_media_group"
    print("ok  delivery_kind and delivered_files")

    note = album_truncation_note(14, 10)
    assert "14" in note and "10" in note, note
    assert "Traceback" not in note and "album" not in note.lower(), note
    print("ok  album_truncation_note")


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

    # The escapes below are the real bytes of the live 2026-08-09T17:17 record, not a
    # reproduction -- nobody has been able to make yt-dlp emit them on demand since,
    # so the record IS the evidence. Only the share token in the URL is elided.
    coloured = (
        "yt-dlp could not download https://www.instagram.com/reel/DbpG4CuSKoG/?igsh=...: "
        "\x1b[0;31mERROR:\x1b[0m [Instagram] DbpG4CuSKoG: This content isn't available "
        "to everyone: It can't be seen by certain audiences."
    )
    cleaned = rejection_record("https://www.instagram.com/reel/DbpG4CuSKoG/", "ExtractionError",
                               coloured, -100123, 69732, "2026-08-09T17:17:45-03:00")["detail"]
    assert "\x1b" not in cleaned, f"a control code reached the ledger: {cleaned!r}"
    assert "[0;31m" not in cleaned and "[0m" not in cleaned, cleaned
    # What must SURVIVE. A pattern greedy enough to strip "[Instagram]" would throw
    # away the extractor name, which is the first thing the owner greps for.
    assert cleaned.startswith("yt-dlp could not download "), cleaned
    assert "ERROR: [Instagram] DbpG4CuSKoG:" in cleaned, cleaned
    assert cleaned.endswith("It can't be seen by certain audiences."), cleaned
    assert len(cleaned) == len(coloured) - len("\x1b[0;31m") - len("\x1b[0m"), cleaned
    # Ordinary text is untouched, escapes or not.
    plain = "ERROR: [facebook] 999999999999999: Cannot parse data; please report this issue"
    assert strip_ansi(plain) == plain, strip_ansi(plain)
    assert strip_ansi("") == "" and strip_ansi(None) == ""
    print("ok  the ledger stores text, not terminal colour codes")

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


def _check_failure_replies() -> None:
    """Each measured signature gets its own Spanish line; anything else stays generic.

    Plain strings and no network on purpose. The details below are verbatim from
    download_into run against the live sites on 2026-08-09 -- the exact text the bot
    would record and classify. Reading them off `yt-dlp --simulate` instead would be
    measuring a different string: on YouTube the image fallback replaces yt-dlp's
    message with the bot's own before anything downstream ever sees it.

    A check built on a broken remote post would break the day somebody restores or
    deletes it, which is why nothing here fetches anything.
    """
    measured = {
        "instagram, audience-restricted":
            "yt-dlp could not download https://www.instagram.com/reel/DbpG4CuSKoG/: "
            "ERROR: [Instagram] DbpG4CuSKoG: This content isn't available to everyone: "
            "It can't be seen by certain audiences.",
        "instagram, no such post":
            "yt-dlp could not download https://www.instagram.com/reel/AAAAAAAAAAA/: "
            "ERROR: [Instagram] AAAAAAAAAAA: Instagram sent an empty media response. "
            "Check if this post is accessible in your browser without being logged-in. "
            "If it is not, then use --cookies-from-browser or --cookies for the "
            "authentication.",
        "instagram, a profile URL not a post":
            "yt-dlp could not download https://www.instagram.com/nasa/: "
            "ERROR: [instagram:user] nasa: Unable to extract data; please report this "
            "issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the "
            "appropriate issue template.",
        # Re-measured on this branch after the image fallback stopped replacing
        # yt-dlp's diagnosis with its own. Before the fix both of these read
        # "<url> has no video and no downloadable image either" -- the bot's
        # sentence, identical for every failing YouTube link, and useless to
        # anybody reading the ledger to find out what YouTube said.
        "youtube, unavailable (A)":
            "yt-dlp could not download https://www.youtube.com/watch?v=AAAAAAAAAAA: "
            "ERROR: [youtube] AAAAAAAAAAA: Video unavailable",
        "youtube, unavailable (Z)":
            "yt-dlp could not download https://www.youtube.com/watch?v=ZZZZZZZZZZZ: "
            "ERROR: [youtube] ZZZZZZZZZZZ: Video unavailable",
        "facebook, dead post or throttled":
            "yt-dlp could not download https://www.facebook.com/watch/?v=999999999999999: "
            "ERROR: [facebook] 999999999999999: Cannot parse data; please report this "
            "issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the "
            "appropriate issue template.",
    }
    replies = {label: failure_reply(detail) for label, detail in measured.items()}
    for label, reply in replies.items():
        assert reply != FAILURE_REPLY, f"{label} is measured and must be named, not generic"

    # The two YouTube URLs are a deleted video and a nonexistent one and yt-dlp cannot
    # tell them apart, so the bot must not pretend to either.
    assert replies["youtube, unavailable (A)"] == replies["youtube, unavailable (Z)"], replies

    # One line per row, no two rows sharing one, and every row reached by something
    # measured. Tied to the table rather than to a literal, so adding a row keeps the
    # invariant instead of updating a number.
    named = set(replies.values())
    assert len(named) == len(FAILURE_SIGNATURES), \
        f"{len(FAILURE_SIGNATURES)} rows produced {len(named)} distinct lines: {named}"

    # Nothing may be claimed that the signature does not carry. These three are
    # ambiguous by measurement, so each has to offer both readings rather than pick.
    for label in ("facebook, dead post or throttled", "instagram, no such post",
                  "youtube, unavailable (A)"):
        assert "puede que" in replies[label], f"{label} must hedge, it cannot know: {replies[label]}"
    assert "probá de nuevo" in replies["facebook, dead post or throttled"], \
        "throttling is temporary, so the facebook line has to say to retry"
    assert "perfil" in replies["instagram, a profile URL not a post"], replies
    assert "no es público" in replies["instagram, audience-restricted"], replies

    # A friend reads these: Spanish, lower case, one line, no jargon and no codes.
    for markers, reply in FAILURE_SIGNATURES:
        assert reply == reply.lower(), f"the bot does not shout: {reply}"
        assert len(reply) <= 120 and "\n" not in reply, f"one short line: {reply}"
        assert not any(char.isdigit() for char in reply), f"no codes in the chat: {reply}"
        for jargon in ("error", "http", "yt-dlp", "url", "extract"):
            assert jargon not in reply, f"{jargon!r} means nothing to the group: {reply}"
        # Markers are compared against a casefolded string, so a capital in the table
        # is a row that can never fire -- silently, which is the worst way.
        for marker in markers:
            assert marker == marker.casefold(), f"marker must be casefolded: {marker!r}"

    # Every row must be backed by something actually measured. A speculative row --
    # a failure nobody has seen, mapped on a guess -- makes this go red.
    for markers, reply in FAILURE_SIGNATURES:
        assert reply in named, f"unmeasured row: nothing above produces {reply!r}"

    # The unknown stays unknown. This is the half of the feature that is a promise.
    assert failure_reply("simulated extractor failure") == FAILURE_REPLY
    assert failure_reply("") == FAILURE_REPLY and failure_reply(None) == FAILURE_REPLY
    assert failure_reply("yt-dlp could not download https://x/: ERROR: [Instagram] x: "
                         "HTTP Error 429: Too Many Requests") == FAILURE_REPLY

    # Drift: upstream rewords a sentence and the row stops matching. That must land on
    # the generic apology and never on another row's answer.
    assert failure_reply("ERROR: [Instagram] x: This content is not available to everyone: "
                         "it cannot be seen by certain audiences.") == FAILURE_REPLY
    assert failure_reply("ERROR: [facebook] 1: Could not parse the data") == FAILURE_REPLY
    # Half a signature is not a signature: the extractor tag alone must not fire.
    assert failure_reply("ERROR: [facebook] 1: Unable to extract data") == FAILURE_REPLY
    assert failure_reply("ERROR: [youtube] x: Sign in to confirm your age") == FAILURE_REPLY
    assert failure_reply("ERROR: [Instagram] x: Video unavailable") == FAILURE_REPLY
    # The bot's own sentence used to be the YouTube key, and it is gone from both the
    # table and the code. If it ever comes back it must land on the generic apology
    # rather than quietly inherit a line measured for something else.
    assert failure_reply("https://www.youtube.com/watch?v=AAAAAAAAAAA has no video and no "
                         "downloadable image either") == FAILURE_REPLY

    # Capitals in yt-dlp's prose change nothing, and neither does colour: a coloured
    # detail classifies exactly like the clean one. Both halves of this order meet here.
    assert failure_reply(measured["facebook, dead post or throttled"].upper()) == \
        replies["facebook, dead post or throttled"]
    coloured = ("yt-dlp could not download https://www.instagram.com/reel/DbpG4CuSKoG/?igsh=...: "
                "\x1b[0;31mERROR:\x1b[0m [Instagram] DbpG4CuSKoG: This content isn't available "
                "to everyone: It can't be seen by certain audiences.")
    assert failure_reply(coloured) == replies["instagram, audience-restricted"], failure_reply(coloured)
    # The line above passes with or without the strip -- in the real record every
    # escape happens to fall outside every marker, so it proves nothing on its own
    # (mutation testing caught that; the assert was vacuous). This one is the case
    # that needs the strip: colour landing INSIDE the sentence being matched. It is
    # synthetic -- nobody has seen yt-dlp colour a message mid-word -- and it is here
    # because the alternative to stripping is a row that silently stops firing.
    straddled = ("ERROR: [Instagram] x: This content isn't \x1b[0;31mavailable\x1b[0m "
                 "to everyone: It can't be seen by certain audiences.")
    assert failure_reply(straddled) == replies["instagram, audience-restricted"], failure_reply(straddled)
    print("ok  a failure the bot can name gets its own Spanish line")

    # End to end through _deliver: the group hears the named line, and the ledger
    # still gets the raw text. A wiring that drops the detail on the way to the
    # apology passes every assert above and fails here.
    class RecordingMessage:
        chat_id = -100123
        message_id = 77

        def __init__(self) -> None:
            self.said: list[str] = []

        async def reply_text(self, text: str, **_kwargs: object) -> None:
            self.said.append(text)

    def _restricted_download(_url: str, _target_dir: Path) -> Media:
        raise ExtractionError(measured["instagram, audience-restricted"])

    real_download, real_ledger = globals()["download_into"], globals()["REJECTED_LEDGER"]
    globals()["download_into"] = _restricted_download
    with temp_workspace() as workspace:
        globals()["REJECTED_LEDGER"] = workspace / "rejected.jsonl"
        try:
            message = RecordingMessage()
            with _capture_log(logging.ERROR):
                asyncio.run(_deliver(message, "https://www.instagram.com/reel/DbpG4CuSKoG/"))
            written = read_rejections(globals()["REJECTED_LEDGER"])
        finally:
            globals()["download_into"] = real_download
            globals()["REJECTED_LEDGER"] = real_ledger

    assert message.said == [replies["instagram, audience-restricted"]], message.said
    assert len(written) == 1, written
    assert "This content isn't available to everyone" in written[0]["detail"], written
    assert replies["instagram, audience-restricted"] not in written[0]["detail"], \
        "the ledger records the raw failure, not the sentence the group was told"
    print("ok  _deliver tells the group the cause and still records the raw detail")


def _check_failed_extraction_keeps_its_error() -> None:
    """A failed extraction reaches the ledger as the EXTRACTOR's words, not the bot's.

    The whole slice, end to end, with the network removed: yt-dlp itself is replaced
    by a stand-in that raises on the video download, hands the fallback the info dict
    an unavailable YouTube video really returns (no formats, 38 synthesised
    thumbnails -- measured 2026-08-09), and writes an image or not depending on the
    case. Both branches of the discrimination are covered, because a fix that made
    every image post fail would pass the negative half on its own.
    """
    unavailable = "ERROR: [youtube] AAAAAAAAAAA: Video unavailable"
    dead_youtube = {
        "formats": [],
        "thumbnails": [{"id": str(n), "url": f"https://i.ytimg.com/vi/AAAAAAAAAAA/{n}.jpg",
                        "preference": n - 37} for n in range(38)],
    }

    def _attempt(workspace: Path, info: dict, image: str | None) -> Media | str:
        """download_into with yt-dlp stubbed out. Returns the Media or the error text."""

        class _FakeYdl:
            def __init__(self, options: dict) -> None:
                self.options = options

            def __enter__(self) -> "_FakeYdl":
                return self

            def __exit__(self, *_exc: object) -> bool:
                return False

            def extract_info(self, _url: str, download: bool = False) -> dict:
                # The thumbnail run is the only one that downloads while skipping the
                # video; that is the call that either produces an image or does not.
                if download and self.options.get("skip_download"):
                    if image is not None:
                        (workspace / image).write_bytes(b"\xff\xd8\xff" + b"x" * 4000)
                    return info
                if download:
                    raise yt_dlp.utils.DownloadError(unavailable)
                return info

        class _FakeYtDlp:
            YoutubeDL = _FakeYdl
            utils = yt_dlp.utils

        real = globals()["yt_dlp"]
        globals()["yt_dlp"] = _FakeYtDlp
        try:
            return download_into("https://www.youtube.com/watch?v=AAAAAAAAAAA", workspace)
        except ExtractionError as exc:
            return str(exc)
        finally:
            globals()["yt_dlp"] = real

    with temp_workspace() as workspace:
        failed = _attempt(workspace, dead_youtube, image=None)
    assert isinstance(failed, str), f"a dead video must not be delivered as anything: {failed}"
    assert unavailable in failed, f"the extractor's own words must survive: {failed}"
    assert "no downloadable image either" not in failed, (
        f"the fallback's error replaced the extractor's, which is the whole defect: {failed}"
    )
    # And it must still be a NAMED failure to the group -- the row that used to be
    # keyed on the bot's sentence now has to fire on what actually arrives.
    assert failure_reply(failed) != FAILURE_REPLY, (
        f"the YouTube row no longer matches what the bot records: {failed}"
    )
    assert "puede que" in failure_reply(failed), failure_reply(failed)

    # The other half: a post that really is video-less still becomes a photo. Without
    # this, "always return None" passes everything above.
    with temp_workspace() as workspace:
        delivered = _attempt(workspace, dead_youtube, image="best.jpg")
    assert isinstance(delivered, Media), f"an image that downloads must still be sent: {delivered}"
    assert delivered.path.name == "best.jpg" and not delivered.has_audio, delivered
    print("ok  a failed extraction keeps its own error all the way to the ledger")


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

    for site, (url, expected_kind, expected_files) in SELF_CHECK_URLS.items():
        print(f"..  {site}: downloading {url}")
        with downloaded_media(url) as media:
            assert media.path.is_file(), f"{site}: expected a file at {media.path}"
            kind = delivery_kind(media)
            files = delivered_files(media)
            size = max(path.stat().st_size for path in files)
            assert size > 0, f"{site}: downloaded file is empty"
            assert kind == expected_kind, (
                f"{site}: expected a {expected_kind}, got a {kind} ({media.path.name}). "
                f"A reel arriving as its poster frame is exactly what this catches."
            )
            assert len(files) == expected_files, (
                f"{site}: expected {expected_files} file(s), got {len(files)} -- a carousel "
                f"losing or gaining a slide is a silent lie about what was posted"
            )
            assert delivery_decision(size, kind) == "file", (
                f"{site}: {size} bytes is over the {upload_ceiling(kind)}-byte ceiling for {kind}"
            )

            if kind == "album":
                assert media.slide_total == expected_files, (
                    f"{site}: slide_total {media.slide_total} != {expected_files}"
                )
                assert media.path == files[0], f"{site}: path must be the first slide"
                assert len(set(files)) == len(files), f"{site}: the same slide twice"
                # Order is the post's order and the zero-padded names carry it.
                # Losing it reorders somebody's ten-panel joke.
                assert list(files) == sorted(files), f"{site}: slides out of order: {files}"
                for slide in files:
                    slide_container, _, slide_w, slide_h = _probe_container_and_codec(slide)
                    assert slide_w > 0 and slide_h > 0, (
                        f"{site}: {slide.name} is {slide_container} with no dimensions"
                    )
                    # Same rule as the single image post, per slide: every
                    # thumbnail of this slide is still in the temp dir, and the one
                    # that was picked has to be the biggest of them. Sorting by
                    # filename would pick ".9.jpg" over ".12.jpg" (§4.8).
                    stem = slide.name.split(".")[0]
                    group = [
                        p for p in slide.parent.iterdir()
                        if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES
                        and p.name.split(".")[0] == stem
                    ]
                    assert len(group) > 1, f"{site}: {slide.name} had nothing to choose from"
                    biggest = max(p.stat().st_size for p in group)
                    assert slide.stat().st_size == biggest, (
                        f"{site}: slide {stem} delivered {slide.name} at "
                        f"{slide.stat().st_size} B but {biggest} B was available"
                    )
                print(
                    f"ok  {site}: {len(files)} slides, "
                    f"{sum(p.stat().st_size for p in files)} bytes total, largest {size}"
                    f" -> {reply_method_name(kind)}"
                )
            else:
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

        async def reply_media_group(self, media: object, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.media = media

    video = CapturingMessage()
    asyncio.run(_send(video, "video", Media(Path("x.mp4"), True, None, 1280, 720, 10.0)))
    assert video.kwargs.get("connect_timeout") == CONNECT_TIMEOUT, video.kwargs
    assert video.kwargs.get("write_timeout") == UPLOAD_TIMEOUT, video.kwargs
    assert video.kwargs.get("read_timeout") == UPLOAD_TIMEOUT, video.kwargs

    text = CapturingMessage()
    asyncio.run(_reply_text(text, "hola"))
    assert text.kwargs.get("connect_timeout") == CONNECT_TIMEOUT, text.kwargs
    assert text.kwargs.get("write_timeout") == TEXT_REPLY_TIMEOUT, text.kwargs

    with temp_workspace() as workspace:
        slides = []
        for index in (1, 2):
            slide = workspace / f"slide-00{index}.12.jpg"
            slide.write_bytes(b"\xff\xd8\xff\xdb" + bytes(index))
            slides.append(slide)
        album = CapturingMessage()
        asyncio.run(_send_album(album, slides))
        assert album.kwargs.get("connect_timeout") == CONNECT_TIMEOUT, album.kwargs
        assert album.kwargs.get("write_timeout") == UPLOAD_TIMEOUT, album.kwargs
        assert album.kwargs.get("read_timeout") == UPLOAD_TIMEOUT, album.kwargs
        assert len(album.media) == 2, album.media

        # The one that cannot be seen without a real group: InputMediaPhoto given a
        # Path or a str silently becomes the string "file:///...", uploads nothing,
        # and api.telegram.org rejects it. Only a file object becomes an InputFile,
        # which is what puts the bytes on the wire behind an attach:// URI.
        with ExitStack() as stack:
            built = album_media(slides, stack)
            assert all(isinstance(item.media, telegram.InputFile) for item in built), (
                f"album items must be InputFile, got {[type(i.media).__name__ for i in built]}: "
                f"a Path here becomes a file:// URI that uploads nothing"
            )
        for wrong in (slides[0], str(slides[0])):
            assert not isinstance(telegram.InputMediaPhoto(wrong).media, telegram.InputFile), (
                "if PTB ever starts uploading a Path directly, album_media can be simplified"
            )
    print("ok  outbound calls carry an explicit connect_timeout, album uploads real bytes")


def _check_album_delivery() -> None:
    """A carousel longer than an album must say so, in Spanish, before it sends.

    Instagram's own carousels are short enough that no public one has more than ten
    slides to test against, so the truncation branch is driven with a stand-in
    Media. Everything about it except the slide count is the real _deliver.
    """

    class AlbumMessage:
        chat_id = -100123
        message_id = 3

        def __init__(self) -> None:
            self.sent: list[tuple[str, object]] = []

        async def reply_text(self, text: str, **_kwargs: object) -> None:
            self.sent.append(("text", text))

        async def reply_media_group(self, media: object, **_kwargs: object) -> None:
            self.sent.append(("album", len(media)))  # type: ignore[arg-type]

    def deliver(slide_count: int, total: int, oversized: int | None = None) -> list[tuple[str, object]]:
        """Run the real _deliver over `slide_count` stand-in slides.

        `oversized` is the 1-based slide to blow past the photo ceiling. Sparse:
        truncate() sets the byte count without writing 10 MiB.
        """
        with temp_workspace() as workspace:
            slides = []
            for index in range(1, slide_count + 1):
                slide = workspace / f"slide-{index:03d}.12.jpg"
                slide.write_bytes(b"\xff\xd8\xff\xdb" + bytes(index))
                if index == oversized:
                    with slide.open("r+b") as handle:
                        handle.truncate(TELEGRAM_MAX_PHOTO_UPLOAD + 1)
                slides.append(slide)
            media = Media(slides[0], has_audio=False, slides=tuple(slides), slide_total=total)
            real, real_ledger = globals()["download_into"], globals()["REJECTED_LEDGER"]
            globals()["download_into"] = lambda _url, _dir: media
            globals()["REJECTED_LEDGER"] = workspace / "rejected.jsonl"
            try:
                message = AlbumMessage()
                asyncio.run(_deliver(message, "https://www.instagram.com/p/carousel/"))
            finally:
                globals()["download_into"] = real
                globals()["REJECTED_LEDGER"] = real_ledger
            return message.sent

    exact = deliver(10, 10)
    assert exact == [("album", 10)], f"a carousel that fits sends the album and nothing else: {exact}"

    # Telegram rejects the whole call if ONE item is over the ceiling, so the size
    # question is about the largest slide, not the first. A carousel whose last
    # slide is the heavy one is the case that catches measuring files[0].
    heavy = deliver(3, 3, oversized=3)
    assert len(heavy) == 1 and heavy[0][0] == "text", f"an oversize album must not be sent: {heavy}"
    assert "no me deja subirlo" in str(heavy[0][1]), heavy
    print("ok  an album is measured by its largest slide, not its first")

    truncated = deliver(ALBUM_MAX_ITEMS, 14)
    assert len(truncated) == 2, f"a truncated post owes the group a warning: {truncated}"
    # The warning goes FIRST. If it went second and failed, the group would be left
    # with a post that is quietly missing four slides.
    assert truncated[0][0] == "text" and truncated[1] == ("album", ALBUM_MAX_ITEMS), truncated
    said = str(truncated[0][1])
    assert "14" in said and "10" in said, said
    assert FAILURE_REPLY not in said, "a long carousel is not a failure"
    print("ok  a carousel longer than an album says so before it sends")


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
        # An unsupported host now writes to the ledger as well as to the log, so the
        # ledger goes somewhere disposable: a self-check run must never leave two
        # invented TikTok links in the owner's real rejected.jsonl.
        real_ledger = globals()["REJECTED_LEDGER"]
        with temp_workspace() as workspace:
            globals()["REJECTED_LEDGER"] = workspace / "rejected.jsonl"
            try:
                with _capture_log(logging.INFO) as messages:
                    asyncio.run(on_message(telegram.Update(update_id=1, message=message), None))
            finally:
                globals()["REJECTED_LEDGER"] = real_ledger
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


def _check_unattempted_links_are_recorded() -> None:
    """A link on a host the bot does not support is a bounce too, and a different one.

    Four shapes, all driven through the real on_message with _deliver and the ledger
    path swapped out -- no network, no token. The mixed case is the one that is easy
    to get wrong: something WAS attempted there, so nothing in that message is
    unattempted, not even the link that was skipped.
    """

    def _run(text: str, *entities: telegram.MessageEntity) -> tuple[list[dict], list[str], list[str], str]:
        message = telegram.Message(
            message_id=11,
            date=dt.datetime.fromtimestamp(0, dt.timezone.utc),
            chat=telegram.Chat(id=-100123, type=telegram.Chat.GROUP),
            from_user=telegram.User(id=1, first_name="u", is_bot=False),
            text=text,
            entities=entities,
        )
        delivered: list[str] = []

        async def _fake_deliver(_message: object, url: str) -> None:
            delivered.append(url)

        real_deliver, real_ledger = globals()["_deliver"], globals()["REJECTED_LEDGER"]
        globals()["_deliver"] = _fake_deliver
        with temp_workspace() as workspace:
            ledger = workspace / "rejected.jsonl"
            globals()["REJECTED_LEDGER"] = ledger
            try:
                with _capture_log(logging.INFO) as lines:
                    asyncio.run(on_message(telegram.Update(update_id=1, message=message), None))
                raw = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""
                return read_rejections(ledger), delivered, lines, raw
            finally:
                globals()["_deliver"] = real_deliver
                globals()["REJECTED_LEDGER"] = real_ledger

    # One unsupported URL.
    records, delivered, lines, raw = _run("miren https://www.tiktok.com/@a/video/1")
    assert delivered == [], "nothing on an unsupported host may be attempted"
    assert len(records) == 1, records
    assert records[0]["url"] == "https://www.tiktok.com/@a/video/1", records
    assert records[0]["error"] == UNSUPPORTED_ERROR, records
    assert records[0]["error"] != "ExtractionError", "a bounce and a skip are different piles"
    assert records[0]["chat_id"] == -100123 and records[0]["message_id"] == 11, records
    assert records[0]["detail"] == "", "nothing was attempted, so there is no error text"
    # The log line the person at the window reads is unchanged by any of this.
    assert len(lines) == 1 and "none on a supported host" in lines[0], lines

    # Several: every one of them, in order. Recording only the first would hide
    # exactly the thing this is for -- which site keeps coming back.
    records, delivered, _lines, raw = _run(
        "SECRETO-DEL-GRUPO https://www.tiktok.com/@a/video/1 y https://x.com/a/status/2 "
        "y https://www.reddit.com/r/a/comments/3/"
    )
    assert delivered == []
    assert [record["url"] for record in records] == [
        "https://www.tiktok.com/@a/video/1",
        "https://x.com/a/status/2",
        "https://www.reddit.com/r/a/comments/3/",
    ], records
    assert {record["error"] for record in records} == {UNSUPPORTED_ERROR}, records
    assert "SECRETO-DEL-GRUPO" not in raw, "the message body must never reach the ledger"

    # A mix: the supported link is attempted, so nothing here is unattempted.
    records, delivered, lines, _raw = _run(
        "https://www.tiktok.com/@a/video/1 y https://youtu.be/abc"
    )
    assert delivered == ["https://youtu.be/abc"], delivered
    assert records == [], f"a message that was attempted is not a skip: {records}"
    assert lines == [], "the ignore-logging must not fire when something was delivered"

    # Ordinary chat. Not a bounced link, and recording it would drown the signal.
    records, delivered, lines, _raw = _run("che alguien vio el partido ayer")
    assert records == [] and delivered == [], (records, delivered)
    assert len(lines) == 1 and "no URL recognised" in lines[0], lines

    # A schemeless link Telegram saw and the regex could not: delivered when the host
    # is supported, recorded when it is not. Both halves of the second source, through
    # the real on_message rather than through message_urls on its own.
    records, delivered, _lines, _raw = _run(
        "\U0001f602 tiktok.com/@a/video/1",
        telegram.MessageEntity(type="url", offset=3, length=21),
    )
    assert delivered == [], delivered
    assert [record["url"] for record in records] == ["https://tiktok.com/@a/video/1"], records
    assert records[0]["error"] == UNSUPPORTED_ERROR, records
    records, delivered, _lines, _raw = _run(
        "\U0001f602 youtu.be/abc", telegram.MessageEntity(type="url", offset=3, length=12)
    )
    assert delivered == ["https://youtu.be/abc"], delivered
    assert records == [], records

    # And the report keeps the two piles apart, which is why the class exists.
    report = format_rejections([
        rejection_record("https://www.tiktok.com/@a/video/1", UNSUPPORTED_ERROR, "", 1, 1,
                         "2026-08-09T10:00:00-03:00"),
        rejection_record("https://www.tiktok.com/@b/video/2", UNSUPPORTED_ERROR, "", 1, 2,
                         "2026-08-09T11:00:00-03:00"),
        rejection_record("https://youtu.be/c", "ExtractionError", "boom", 1, 3,
                         "2026-08-09T12:00:00-03:00"),
    ])
    assert f"{UNSUPPORTED_ERROR} -- 2" in report and "ExtractionError -- 1" in report, report
    assert "  tiktok.com -- 2" in report, report
    print("ok  a link the bot declined to try is recorded, separately from a bounce")


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


def _check_take_over_intent() -> None:
    """The launcher's answer has to reach the bot, and nothing else may look like it.

    That flag is the only thing that tells two instances of this file apart (§4.9), so
    both halves are asserted: that a declared take-over is read, and that a normal
    start -- or a typo -- is never mistaken for one. A false positive is the worse of
    the two, because an instance that wrongly believes it was told to take over never
    gives the baton back.
    """
    assert take_over_requested([TAKE_OVER_FLAG]) is True, "the flag must be read"
    assert take_over_requested(["--self-check", TAKE_OVER_FLAG]) is True, "position must not matter"
    assert take_over_requested([]) is False, "a normal start is not a take-over"
    assert take_over_requested(["--self-check"]) is False, "another flag is not a take-over"
    for junk in ("--takeover", "--take-over=yes", "--TAKE-OVER", "take-over", "-take-over", ""):
        assert take_over_requested([junk]) is False, f"{junk!r} must not read as a take-over"

    # And it has to arrive: main() is what turns the flag into the deadline on_error
    # reads, so it is driven here rather than asserted as a constant that agrees with
    # itself. Nothing reaches the network -- build_application is replaced first, and
    # the token below is a shaped fake.
    class PollRecordingApplication:
        def run_polling(self, **_kwargs: object) -> None:
            pass

    global _take_over_until
    saved_intent = _take_over_until
    saved_argv = sys.argv
    real_build = globals()["build_application"]
    globals()["build_application"] = lambda _token: PollRecordingApplication()
    previous = os.environ.get(TOKEN_ENV_VAR)
    os.environ[TOKEN_ENV_VAR] = "123456:AAHnot-a-real-token-nothing-is-sent"
    try:
        for argv, wanted in (
            (["bot.py"], False),
            (["bot.py", "--takeover"], False),
            (["bot.py", TAKE_OVER_FLAG], True),
        ):
            _take_over_until = None
            sys.argv = argv
            main()
            assert (_take_over_until is not None) is wanted, (argv, _take_over_until)
        remaining = _take_over_until - time.monotonic()  # type: ignore[operator]
        assert 0.0 < remaining <= TAKE_OVER_WINDOW, remaining
    finally:
        sys.argv = saved_argv
        globals()["build_application"] = real_build
        _take_over_until = saved_intent
        if previous is None:
            del os.environ[TOKEN_ENV_VAR]
        else:
            os.environ[TOKEN_ENV_VAR] = previous
    print("ok  the launcher's take-over answer reaches the bot, and nothing else does")


def _check_conflict_handling() -> None:
    """One line per conflict episode, and only one of the two sides ever stops.

    The whole path except the network: conflict_action is pure, and on_error takes a
    stand-in context, so both halves are reachable without a second live bot. Both
    roles are driven over the same simulated timeline, because the failure this file
    is guarding against is not one instance behaving badly -- it is two instances
    behaving identically. What is NOT reachable here is python-telegram-bot actually
    delivering a Conflict to the handler, nor two real instances on one token: that
    needs the owner and a second machine.
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

    # --- The same conflicts, read by the side that was told to take the bot --------
    # Monotonic 0.0 is this process's start, so the answer the person gave at the
    # launcher covers everything up to TAKE_OVER_WINDOW. Every conflict below is dated
    # from 1 s in rather than from 0.0, because polling starts after the process does:
    # a timeline beginning exactly at the deadline would let a zero-length window --
    # an intent that is never live for anything at all -- pass.
    taking_over_until = TAKE_OVER_WINDOW

    # The launcher's own probe against an instance that took the bot moments ago: one
    # line, no stop, exactly as tolerant as any other instance.
    started = last = None
    actions = []
    for moment in (1.0, 2.0, 3.5, 5.7, 9.1, 11.0):
        action, started, last = conflict_action(moment, started, last, taking_over_until)
        actions.append(action)
    assert actions == ["take-over"] + ["quiet"] * 5, actions

    # A real hand-over, driven far past the point where the incumbent above gave up
    # and past the intent's own expiry: this side never gives up. If it did, both
    # sides could stop on the same conflict and the group would be left with no bot,
    # which is exactly what the 2026-08-09 experiment produced.
    started = last = None
    moment = 1.0
    actions = []
    while moment <= CONFLICT_STANDOFF + 60.0:
        action, started, last = conflict_action(moment, started, last, taking_over_until)
        actions.append(action)
        moment += 25.0
    assert (len(actions) - 1) * 25.0 > TAKE_OVER_WINDOW, "the timeline must outlast the intent"
    assert "give-up" not in actions, actions
    assert actions.count("take-over") == 1, actions
    # And it says exactly once that the other side is not yielding either.
    assert actions.count("stand-ground") == 1, actions
    standoff = actions.index("stand-ground")
    assert standoff * 25.0 >= CONFLICT_STANDOFF, actions
    assert (standoff - 1) * 25.0 < CONFLICT_STANDOFF, actions
    assert set(actions[standoff + 1 :]) == {"quiet"}, actions

    # Two hours later somebody else asks for the baton. The intent has expired, so
    # this instance is an ordinary incumbent and yields like anybody else -- without
    # that expiry only the first hand-over of the day would ever work.
    started = last = None
    moment = 7200.0
    actions = []
    while "give-up" not in actions and moment <= 7200.0 + CONFLICT_GRACE + 30.0:
        action, started, last = conflict_action(moment, started, last, taking_over_until)
        actions.append(action)
        moment += 25.0
    assert actions[0] == "announce", actions
    assert actions[-1] == "give-up", actions

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

    global _conflict_started, _conflict_last, _take_over_until
    saved = (_conflict_started, _conflict_last, _take_over_until)
    application = StopRecordingApplication()
    try:
        _take_over_until = None
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
        # It may not claim to know what a process on another laptop is doing, and it
        # has to leave the friend able to recover if the guess was wrong.
        assert "parece que" in spoken.getvalue(), spoken.getvalue()
        assert "volvé a abrir este archivo" in spoken.getvalue(), spoken.getvalue()

        # The same event on the side that was told to take the bot over: its own
        # line, and no stop.
        _take_over_until = time.monotonic() + TAKE_OVER_WINDOW
        _conflict_started = _conflict_last = None
        with _capture_log(logging.WARNING) as warnings:
            spoken = io.StringIO()
            with redirect_stdout(spoken):
                asyncio.run(on_error(None, Context(telegram.error.Conflict("c"), application)))
                asyncio.run(on_error(None, Context(telegram.error.Conflict("c"), application)))
        assert len(warnings) == 1, f"one line per episode, got {warnings}"
        assert "taking the poll over" in warnings[0], warnings
        assert spoken.getvalue().count("\n") == 1, spoken.getvalue()
        assert "Se lo estoy sacando" in spoken.getvalue(), spoken.getvalue()
        assert application.stopped == 1, "a take-over must not stop on its first conflict"

        # And it still does not stop once that conflict has outlasted the grace the
        # yielding side stops at. Both sides stopping is the whole bug.
        _conflict_last = time.monotonic()
        _conflict_started = _conflict_last - CONFLICT_GRACE - 1.0
        with _capture_log(logging.WARNING) as warnings:
            spoken = io.StringIO()
            with redirect_stdout(spoken):
                asyncio.run(on_error(None, Context(telegram.error.Conflict("c"), application)))
        assert application.stopped == 1, "the take-over side must never give up the poll"
        assert warnings == [], warnings
        assert spoken.getvalue() == "", spoken.getvalue()

        # Past CONFLICT_STANDOFF it says so -- once -- and keeps polling.
        _conflict_started = time.monotonic() - CONFLICT_STANDOFF - 1.0
        _conflict_last = _conflict_started + CONFLICT_STANDOFF - 5.0
        with _capture_log(logging.WARNING) as warnings:
            spoken = io.StringIO()
            with redirect_stdout(spoken):
                asyncio.run(on_error(None, Context(telegram.error.Conflict("c"), application)))
                asyncio.run(on_error(None, Context(telegram.error.Conflict("c"), application)))
        assert len(warnings) == 1, f"one line per episode, got {warnings}"
        assert "not yielding" in warnings[0], warnings
        assert "que uno cierre la ventana" in spoken.getvalue(), spoken.getvalue()
        assert application.stopped == 1, "standing its ground must keep this one polling"

        # Anything that is not a Conflict keeps its traceback and stops nothing.
        _take_over_until = None
        _conflict_started = _conflict_last = None
        with _capture_log(logging.ERROR) as errors:
            asyncio.run(on_error(None, Context(telegram.error.TimedOut(), application)))
        assert len(errors) == 1, errors
        assert "unhandled error" in errors[0], errors
        assert application.stopped == 1, "only a conflict stops the bot"
    finally:
        _conflict_started, _conflict_last, _take_over_until = saved
    print("ok  one line per episode, and only the side that was not taking over stops")


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
    _check_failure_replies()
    _check_failed_extraction_keeps_its_error()
    _check_unattempted_links_are_recorded()
    _check_album_delivery()
    _check_failure_path()
    _check_take_over_intent()
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
