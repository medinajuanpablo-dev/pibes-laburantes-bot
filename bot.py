#!/usr/bin/env python3
"""Telegram meme bot.

Someone pastes a YouTube / Instagram / Facebook link in the group; the bot
replies with the media so nobody has to leave the chat.

Scale: one group, ~20 links a week. Every decision here follows from that.
Run it with `python bot.py`; run its self-check with `python bot.py --self-check`.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yt_dlp

log = logging.getLogger("the-bot")

TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
COOKIES_ENV_VAR = "YTDLP_COOKIES"

# Telegram's bot-upload ceiling, verified against
# https://core.telegram.org/bots/api#sending-files on 2026-08-07:
# "10 MB max size for photos, 50 MB for other files."
TELEGRAM_MAX_UPLOAD = 50 * 1024 * 1024
TELEGRAM_MAX_PHOTO_UPLOAD = 10 * 1024 * 1024

# Prefer merged 720p H.264 + AAC (separate streams joined by ffmpeg), then merged
# 720p in any codec, then a single-file 720p-or-below, then whatever exists.
#
# The codec preference is deliberate and costs bytes. Measured on 2026-08-07 for a
# 3.5-minute YouTube video: plain `bv*[height<=720]+ba` picks AV1 + Opus at ~20 MB,
# the avc1/mp4a pair is ~28.5 MB. Both are far under the ceiling, and H.264/AAC in
# an mp4 is what every Telegram client plays inline -- which is the whole point of
# this bot. Size is the cheap resource here; a clip that arrives as an unplayable
# blob is a failure.
#
# ponytail: 720p cap, raise it if the group complains about quality. There is
# headroom: 28.5 MB against a 50 MB ceiling.
MEDIA_FORMAT = (
    "bv*[height<=720][vcodec^=avc1]+ba[acodec^=mp4a]/"
    "bv*[height<=720]+ba/"
    "b[height<=720]/b"
)

SOCKET_TIMEOUT = 20  # seconds. Note: timeout(1) does not exist on macOS; this is the real knob.


class ExtractionError(Exception):
    """The media could not be downloaded. The message is for the log, not the group."""


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
def downloaded_media(url: str) -> Iterator[Path]:
    """Download `url` into a temp dir and yield the resulting file's path.

    Nothing in here knows about Telegram. The temp directory is removed on the way
    out whether the download succeeded, failed, or the caller raised -- which is why
    this is a context manager and not a plain function returning a path: the caller
    needs the file to still exist while it uses it.

    Raises ExtractionError if the download produced no usable file.
    """
    target_dir = Path(tempfile.mkdtemp(prefix="the-bot-"))
    try:
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
        yield path
    finally:
        shutil.rmtree(target_dir, ignore_errors=True)


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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    read_token()
    print("Token found. Nothing is wired to Telegram yet.")


# --------------------------------------------------------------------------------
# Self-check: `python bot.py --self-check`. Plain asserts, no test framework.
# --------------------------------------------------------------------------------

SELF_CHECK_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _self_check() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    assert ffmpeg_path(), "ffmpeg not found on PATH -- merged 720p cannot work without it"
    print(f"ok  ffmpeg resolved from PATH at {ffmpeg_path()}")

    # This one genuinely hits the network. It is the only honest way to know that
    # extraction still works, and it is the check this project actually needs.
    print(f"..  downloading {SELF_CHECK_URL}")
    with downloaded_media(SELF_CHECK_URL) as path:
        assert path.is_file(), f"expected a file at {path}"
        size = path.stat().st_size
        assert size > 0, f"downloaded file is empty: {path}"
        assert size <= TELEGRAM_MAX_UPLOAD, (
            f"downloaded {size} bytes, over Telegram's {TELEGRAM_MAX_UPLOAD}-byte ceiling"
        )
        print(f"ok  downloaded {path.name} ({size} bytes)")
        leftover_dir = path.parent
    assert not leftover_dir.exists(), f"temp dir {leftover_dir} survived the context manager"
    print("ok  temp directory cleaned up")

    print("\nself-check passed")


if __name__ == "__main__":
    if "--self-check" in sys.argv[1:]:
        _self_check()
    else:
        main()
