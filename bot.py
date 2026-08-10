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
import difflib
import html
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
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager, redirect_stdout
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

# python-telegram-bot's HTTP client. The bot never calls it -- PTB owns every request
# -- but the self-check drives it to prove the token cannot reach the log, and having
# the import here means a future pin that drops httpx altogether fails at startup, in
# front of whoever bumped it, instead of quietly putting the token back in the window.
import httpx
import telegram
import yt_dlp
from telegram.ext import Application, CommandHandler, MessageHandler, filters
# The one class that says "the site was never reached" rather than "the site said
# no". Imported by name so a yt-dlp release that moves it fails at startup, on the
# host, in front of whoever just bumped the pin -- rather than on the failure path,
# in front of the group. is_transport_failure is the only thing that reads it.
from yt_dlp.networking.exceptions import TransportError

log = logging.getLogger("the-bot")

TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
COOKIES_ENV_VAR = "YTDLP_COOKIES"

# python-telegram-bot's HTTP client, and the one logger that may not run at INFO: it
# logs every request with the full URL, and every Telegram API URL carries the token
# in its path. Identified on the installed httpx 0.28.1 -- `logging.getLogger("httpx")`
# in `_client.py`, logging `request.url` from `_send_single_request` -- and the name is
# re-verified at runtime by the self-check rather than trusted from that reading, so a
# pin bump that moves the request log cannot pass quietly. Child loggers need no entry
# here: a level on the parent is what an unset child inherits.
REQUEST_URL_LOGGER = "httpx"

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

# The wait between the only two attempts a link ever gets. Not a backoff schedule and
# not a loop: one retry is either enough or it was not a blip (README.md §5.3).
#
# Short on purpose, because the pause is not the whole gap -- the attempt that just
# failed already spent up to SOCKET_TIMEOUT seconds failing, so the site gets ~23 s
# of quiet, not 3. What it costs the group when the network is really down is in
# README.md §5.3: the apology arrives at roughly 43 s instead of 20 s (40 s on
# Instagram, where the image fallback used to pay for a second timeout of its own).
TRANSPORT_RETRY_PAUSE = 3  # seconds

# What the ledger says about a bounce that was already given its second chance. It
# goes in the ExtractionError's own wrapper text, which is where the ledger's
# `detail` starts, so `bot.py --rejected` shows it and no record grows a field.
# The raw extractor text follows it untouched -- §5.1's rule is that the detail is
# the failure's own words, and this is the bot's line ABOUT the attempt, in front.
RETRIED_NOTE = " (retried once)"

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

# The unsupported hosts that get an ANSWER instead of silence. Not a step towards
# supporting them: the bot still downloads nothing from any of these.
#
# The rule above -- anything unsupported is left alone -- is still right for a news
# article, a Spotify link or a Google Doc, and a bot that apologises for every URL
# in the chat is worse than one that stays quiet. It is wrong for a TikTok, where
# somebody clearly expected a video back, and from the chat "the bot does not do
# that site" and "the bot is dead" look identical. That confusion is the whole
# reason this list exists, and keeping it short is the whole reason it works.
#
# Every host here is a site where a link is a video BY CONSTRUCTION. X/Twitter,
# Reddit and Pinterest are deliberately absent: a link to one of those is as often
# an argument, a thread or a photo as a video, so answering them re-creates exactly
# the noise the silence rule protects. They are the first candidates if the ledger
# ever shows the group pasting them.
#
# ponytail: SEEDED FROM JUDGEMENT, WHICH IS THE WEAKEST PART OF THIS FEATURE. The
# ledger is what should grow this list and it has nothing to say yet -- on
# 2026-08-10 the owner's rejected.jsonl held 6 records and not one of them was an
# UnsupportedHost, because this group pastes Instagram and essentially nothing else.
# Upgrade path, and the only one: run `bot.py --rejected`, read the UnsupportedHost
# pile, and add the host the group actually keeps pasting. Do not add a site because
# it sounds likely -- that is how this ends up answering everything.
MEDIA_PLATFORM_HOSTS = frozenset(
    {
        "tiktok.com",
        "twitch.tv",
        "vimeo.com",
        "dailymotion.com",
        "streamable.com",
        "kick.com",
    }
)

# What one of those gets. It names the cause and stops there: no list of the sites
# that DO work, which would be a second sentence to skim past and would go stale the
# day a fourth host is supported. What the bot handles is in EMPEZAR-ACA.md, which
# arrives with the code.
UNSUPPORTED_MEDIA_REPLY = "ese sitio no lo manejo, no puedo bajar ese link"

# The subset of the above whose image posts this bot has ever delivered: Instagram,
# and only Instagram. A YouTube or Facebook link that fails to extract is treated as
# a failed video, full stop, so only Instagram may reach the image fallback -- see
# _image_fallback for the defect this closes.
#
# Careful with the reason, because the obvious phrasing is false. "YouTube and
# Facebook cannot have image posts" is true of YouTube and NOT of Facebook, whose
# extractor accepts photo.php and /posts/ URLs. What is true is narrower and is the
# actual basis for this list: the image path is measured on Instagram (README §4.8)
# and nowhere else, and on Facebook the cost of guessing is the defect itself --
# "Cannot parse data" fires under mere throttling (§5.2), so a Facebook fallback
# would answer a perfectly good video with its poster frame.
# ponytail: a Facebook photo post therefore gets the apology, and whether it ever
# reached the fallback before this guard is unmeasured in both directions -- no
# Facebook image post has ever been in SELF_CHECK_URLS or in the ledger. Upgrade
# path if a friend reports one: find a live public photo post, check what the probe
# returns for it, and only then add the host -- with an entry in SELF_CHECK_URLS,
# because a second site on the image path needs the same standing proof Instagram
# has.
#
# The signal is the HOST OF THE PASTED URL, not yt-dlp's `extractor` key, and the
# two can disagree. Three reasons, in order of weight:
#   * The host is known BEFORE the fallback probe, so a failing YouTube link now
#     costs zero extra requests instead of a probe plus ~38 thumbnail fetches.
#   * The `extractor` key only exists when an extraction SUCCEEDED, and the whole
#     point here is what to do after one failed. On a bot-challenged YouTube link
#     the probe does return "youtube", but reading it means paying for the probe
#     to learn what the URL already said.
#   * It is the same question is_supported() already answers about the same string,
#     so there is one host rule in this file rather than two that can drift apart.
# What the disagreement costs is measured and one-sided: a facebook.com/share/v/
# link redirects internally (the self-check's own share/v/ URL comes back as
# `1547227326881971.mp4`), so host and extractor could in principle diverge -- but
# both readings say "not Instagram", and every way this signal can be wrong ends in
# the apology rather than in a still frame.
IMAGE_POST_HOSTS = frozenset({"instagram.com", "instagr.am"})

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
    # youtube.com/shorts/5kC43KL_mBE -- YouTube challenging this IP, which is what
    # made the bot answer a blocked video with its poster frame (README.md §4.8).
    # The reply does NOT hedge because the signature does not: YouTube says plainly
    # that it wants a login, and the one thing the group can usefully be told is
    # that the link is fine and the bot is not. The person hosting has an action
    # here that nobody else does -- YTDLP_COOKIES -- and it belongs in the log and
    # in README.md §5, not in a sentence written for a friend.
    #
    # MEASURED BY THE OWNER, NOT RE-MEASURED HERE, and that is the one row where
    # this is true. He pasted the URL above on 2026-08-09 and quoted the error; by
    # the time this branch was written the challenge on this IP had already lifted
    # (`5kC43KL_mBE` extracts 28 formats), so the string below is his paste plus the
    # rest of the sentence read off yt-dlp 2026.7.4 -- extractor/common.py's
    # `_login_hint("cookies")` and youtube/_base.py's `_youtube_login_hint`.
    # The markers deliberately step AROUND the apostrophe in "you're": that
    # character comes from YouTube's own JSON and a typographic one would silently
    # kill the row, which is the failure this table's casefold rule already guards
    # against for capitals.
    (("[youtube]", "sign in to confirm", "not a bot"),
     "youtube me está bloqueando y no me deja bajar ese video: no es culpa del link, "
     "probá de nuevo más tarde"),
    # instagram.com/reel/DbqocqEsbVs/ and one more, 2026-08-10: the host's DNS timed
    # out and two perfectly good reels bounced (README.md §5.3). The only row here
    # that the bot has already acted on before the group hears it -- by the time this
    # sentence is chosen, the link has had its second attempt and lost it too.
    #
    # THE ONLY ROW WHOSE ADVICE IS "SEND IT AGAIN", which is the whole reason it is
    # worth a line: every other failure here is a fact about the post, and the friend
    # can do nothing about any of them. This one clears by itself.
    #
    # It hedges on WHOSE network, because that is genuinely unknowable from here: the
    # host's wifi, the friend's, and the site being unreachable produce the identical
    # string. Naming one would be the confident lie the Facebook row exists to avoid.
    #
    # ONE MARKER, AND THE NARROWEST ONE IN THE MEASURED TEXT. "failed to perform,
    # curl:" and "(caused by transporterror" both appear in the same record and both
    # would catch more -- a reset connection, a refused socket -- and neither is
    # taken, because what was measured clearing on its own inside three minutes is
    # THIS failure. A different transport failure gets the generic apology, which is
    # the same fail-safe the rest of this table relies on. The retry in download_into
    # is keyed on the exception, not on this string, so those failures still get their
    # second attempt; only the sentence is withheld.
    (("resolving timed out",),
     "no pude conectarme para bajar ese link: puede ser mi conexión, la tuya o la del "
     "sitio. mandámelo de nuevo"),
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

# The one thing the bot answers that is not a link. The owner's words: "cada vez
# que lea 'bot estupido' en cualquier mensaje (o cualquier variante como 'estupido
# bot' o 'vot estupido' o letras faltantes) debe registrarlo y responder 'Lo lamento,
# hago lo que puedo'". His sentence, verbatim, capital and comma included.
INSULT_REPLY = "Lo lamento, hago lo que puedo"

# The two words, and how close a token has to be to each one. TWO THRESHOLDS BECAUSE
# THEY ARE DIFFERENT LENGTHS, not because tuning wanted a free hand: at three letters
# a single substitution already costs 1/3 of the word, so one number cannot both
# accept "vot" (0.667) and refuse a longer word that shares six letters with
# "estupido". The pair is what makes this safe -- neither word fires alone.
#
# Chosen from two corpora rather than from taste; both ship as the check. The
# numbers each sit in a measured gap:
#   * "bot" 0.66 -- true hits bottom out at "vot"/"ot"/"bo" = 0.667, and the closest
#     ordinary word in the corpus is "no" at 0.400. 0.667 is exactly "one of three
#     letters is wrong", which is where a typo stops being a typo.
#   * "estupido" 0.85 -- true hits bottom out at "estupida" 0.875 and "estupdo"
#     0.933; the closest ordinary word is "estudio" at 0.800. Anything looser starts
#     answering "estudio".
# Moving either one needs a phrase the group actually sent, not a hypothetical.
INSULT_WORDS: tuple[tuple[str, float], ...] = (("bot", 0.66), ("estupido", 0.85))

# Words that clear the bar above and are not typos of anything -- they are words.
# THIS LIST EXISTS BECAUSE DIFFLIB CANNOT TELL "bro" FROM "vot": both share exactly
# two letters with "bot", so both score 0.667, and no threshold can accept the typo
# the owner named while refusing the most common English loanword in this group's
# chat. "bro que estupido" is an ordinary sentence and it fired -- found by review,
# not by the corpus, which is the point of writing this down.
#
# Everything here was enumerated rather than imagined: all 226 tokens of three
# letters or fewer that clear 0.66 against "bot", filtered down to the ones a
# Spanish chat plausibly types. Regenerate it the same way if the thresholds move.
# Adding one costs nothing -- nobody types "boa" meaning the bot -- and the two
# matched words in insults.jsonl are what will name the next one.
NOT_THE_BOT = frozenset(
    {"bro", "bio", "boa", "bol", "bon", "bos", "box", "boy", "bit", "bat", "but",
     "not", "hot", "lot", "out"}
)

# How many tokens may sit BETWEEN the two words. One, so "bot re estupido" and "bot
# es estupido" fire -- "re" is how this group says "very" -- while "el bot funciona,
# no seas estupido vos" stays quiet at a distance of four. Every token of slack is
# false-positive surface, and this is the only insult feature in a bot that reads
# every message a group of friends sends all day.
# ponytail: the ceiling is a message that says "bot" and, one word later, calls
# somebody else stupid ("gracias bot, que estupido soy"). Contrived enough to accept,
# and the cost is one apology nobody asked for. Upgrade path if it ever happens:
# drop this to 0 and lose "bot re estupido" with it.
INSULT_MAX_GAP = 1

# Insults get their own file, NOT the rejected-links ledger. They are not bounced
# links: they have no URL and no error class, `--rejected` opens with "N links
# bounced" and groups by host, and the owner reads that report to decide which site
# to support next. Mixing a joke into the one signal that drives the roadmap would
# cost the report and gain nothing -- and format_rejections is one of the most
# heavily guarded functions in this file.
#
# No reader command for this one. A ledger record needs grouping by class and host to
# mean anything; an insult record is a date, so `cat insults.jsonl` and `wc -l` are
# the whole report. Add one when there is something to group.
INSULT_LEDGER = Path(__file__).resolve().parent / "insults.jsonl"

# --- The bot handing out its own installer --------------------------------------
#
# What a friend gets when they ask the bot how to run it: the one command that makes
# a real clone, which is also the only shape that keeps them updated, since both
# launchers run `git pull --ff-only` on startup. It is a MESSAGE and not a file, and
# that is forced -- a downloaded .command has no exec bit AND carries a quarantine,
# and neither a clone-and-hand-off bootstrap nor right-click -> Open answers both
# halves. Four combinations measured 2026-08-09; do not reopen it. README.md 2.2.
#
# NOTHING BELOW MAY EVER CARRY THE TOKEN. This reply answers anybody who can message
# the bot, in any group it was added to, while the process holds the group's token in
# its environment. The protection is structural rather than a filter: every function
# here is pure and reads nothing, so there is no value to leak, and the self-check
# proves it by building the reply under two fake tokens and comparing the bytes.

# ponytail: hardcoded rather than read from `git remote get-url origin`. Deriving it
# would put a subprocess on a path anyone who can message the bot can trigger, and it
# would hand friends whatever that host's remote happens to be: an SSH remote
# (git@github.com:...) is useless to a friend with no GitHub account, and a copy with
# no .git -- or a machine with no git, which the launchers tolerate -- would have
# nothing to give at all. The ceiling is that this string goes stale if the
# repository ever moves. It cannot go stale *silently*: the self-check pins it
# against this checkout's own origin. Upgrade path if the repo does move: change it
# here and in EMPEZAR-ACA.md, which is the other place a human reads it.
CLONE_URL = "https://github.com/medinajuanpablo-dev/pibes-laburantes-bot.git"

# The folder `git clone` makes. Derived so it can never disagree with the URL above.
CLONE_DIR = CLONE_URL.rsplit("/", 1)[-1].removesuffix(".git")

# Where the clone lands. `mkdir -p` first because this line is also pasted into Git
# Bash on Windows, where ~/Documents is not guaranteed to exist -- and a `cd` into a
# missing folder would abort the whole chain before it cloned anything. On macOS the
# folder is always there and the mkdir does nothing.
CLONE_PARENT = "~/Documents"

INSTALL_COMMAND = "instalar"

# The launcher each platform ends at. The pasted line opens it directly instead of
# stopping at the folder: the friend is already in a terminal at that point, and the
# alternative -- "now go find the file and double-click it" -- is the step where
# somebody gets lost.
LAUNCHER_FILE = {"mac": "run-bot.command", "windows": "run-bot.cmd"}

# Spoken names, so the reply never has to hardcode a platform's label twice.
PLATFORM_NAMES = {"mac": "Mac", "windows": "Windows"}

# How somebody might name their platform. Generous on purpose and consulted for every
# word after the command, because the alternative to recognising "en windows" is an
# error message, and this reply is the one thing a lost friend has.
PLATFORM_WORDS = {
    **dict.fromkeys(("mac", "macos", "osx", "apple", "macbook", "imac"), "mac"),
    **dict.fromkeys(("windows", "win", "pc", "microsoft"), "windows"),
}

# The Windows bootstrap: the file a friend downloads instead of installing git. It
# fetches the repository as a tarball, unpacks it and hands off to run-bot.cmd. It
# exists on Windows only, and the asymmetry is the reason: a .cmd needs no exec bit,
# so a downloaded one runs, while a downloaded .command needs the exec bit *and* no
# quarantine and arrives with neither (README.md 2.2, measured). There is no macOS
# twin of this file and there must not be.
BOOTSTRAP_FILE = "instalar-bot.cmd"

# Derived from CLONE_URL rather than written out, for the same reason CLONE_DIR is: a
# second hand-written copy of the repository's name is a second thing that can send
# friends somewhere else. `main` is the branch every friend tracks -- both update
# mechanisms follow it, `git pull` in a clone and the bootstrap's re-fetch alike.
BOOTSTRAP_URL = (
    CLONE_URL.replace("https://github.com/", "https://raw.githubusercontent.com/", 1).removesuffix(".git")
    + f"/main/{BOOTSTRAP_FILE}"
)

# Where each platform's friend starts, carrying the one obstacle that is genuinely
# platform-specific -- and the two obstacles are no longer the same kind of thing.
# macOS pastes a command, so its obstacle is git behind Apple's Command Line Tools
# dialog. Windows downloads a file and needs no git at all, so its obstacle moves to
# the browser: raw.githubusercontent.com serves a .cmd as `text/plain` with no
# content-disposition (measured 2026-08-10), so Chrome and Edge are expected to show
# the file instead of saving it. Plain text, because install_reply escapes it, and it
# continues the bolded platform name, hence lowercase.
PLATFORM_INTRO = {
    "mac": (
        "abrí Terminal (Cmd+Espacio, escribí Terminal, Enter) y pegá esto. Si te salta "
        "una ventana pidiendo instalar las herramientas de línea de comandos, aceptá y "
        "pegalo de nuevo."
    ),
    # The URL goes last on purpose: Telegram ends an auto-detected link at the next
    # space, so nothing after it can end up inside the tappable part.
    "windows": (
        f"bajá este archivo y hacé doble clic, no hace falta instalar git ni nada "
        f"más: {BOOTSTRAP_URL}"
    ),
}

# Windows' second line, in the place macOS spends on its pasteable block. Both halves
# earn it the way every other line here does, by stopping somebody in the next minute:
# the file is expected to open as text rather than download, and Windows asks before
# running anything that arrived from the internet. Naming the words on the buttons is
# what turns a dialog that reads like a virus warning into a step.
WINDOWS_CONFIRMATION = (
    "Si se abre como texto en el navegador, guardalo con Ctrl+S. Después Windows te va "
    "a preguntar si estás seguro: Más información → Ejecutar de todas formas."
)

# `filters.TEXT | filters.CAPTION` on its own is not "new messages": MessageFilter
# tests Update.effective_message, which resolves to `edited_message` when that is
# what arrived, and to `channel_post` for a channel. So editing a typo in a message
# containing a link would make the bot download and upload the whole video again.
# filters.UpdateType.MESSAGE narrows it to updates carrying Update.message.
MESSAGE_FILTER = filters.UpdateType.MESSAGE & (filters.TEXT | filters.CAPTION)


class ExtractionError(Exception):
    """The media could not be downloaded. The message is for the log, not the group."""


class LiveStreamError(ExtractionError):
    """The link is a stream happening right now, so there is no end to download.

    Its own class because it is the one refusal the bot decides ITSELF rather than
    reads off the site, and _apologise picks the group's line from the class. It also
    makes the ledger say `LiveStreamError` instead of burying a whole category of
    refusal inside the generic bounce pile.

    A subclass of ExtractionError rather than a sibling so that nothing on the
    delivery path needs to learn about it: _deliver's `except Exception` already
    catches it, record_rejection already writes `type(exc).__name__`, and the ledger
    gets the new class for free. Only the sentence is new.
    """


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


def is_live_stream(info: dict | None) -> bool:
    """Whether this link is a stream happening RIGHT NOW, so it has no end to download.

    The one refusal this bot decides for ITSELF. Every other failure it names is the
    site's answer to a question; this is a fact the site states about its own post,
    and the bot acts on it before a byte moves.

    Two fields, ORed, and nothing else. Measured 2026-08-10 with `--simulate` against
    yt-dlp 2026.7.4, the three rows that matter:

        watch?v=X4VbdwhkE10  live now         is_live=True   live_status=is_live
        watch?v=jNQXAC9IVRw  ordinary video   is_live=False   live_status=not_live
        watch?v=zo5oewEQbsE  FINISHED stream  is_live=False   live_status=was_live

    `was_live` IS DELIBERATELY NOT READ, and that is this function's whole trap: it is
    True on the third row -- a finished stream, which is an ordinary bounded video and
    must still be delivered (that one measures 178 MB under MEDIA_FORMAT, so it leaves
    as the oversize link reply). A guard keyed on `was_live` silently refuses every
    stream replay the group pastes, and no check that only tries a live URL would ever
    notice.

    A SIZE OR DURATION CEILING CANNOT DO THIS JOB -- do not reach for one later. On the
    live row `duration`, `filesize` and `filesize_approx` are all None, both on the info
    dict and on the format MEDIA_FORMAT selects, so a ceiling cannot fire on a live
    stream at all; and it would false-positive on any long ordinary video.

    Both fields rather than one because they come from different layers: `is_live` is
    the extractor's own flag, `live_status` is yt-dlp's normalisation of it. Either
    alone is a single point of drift for a refusal that has to be right.

    False on None and on {}: an info dict that says nothing is not a live stream. That
    is not defensiveness -- yt-dlp also runs the filter over playlist entries, which
    arrive with almost nothing in them.

    ponytail: the two live_status values NOT refused are `is_upcoming` (a premiere that
    has not started, so there is nothing to download yet) and `post_live` (a stream that
    ENDED and is still being processed, which is bounded by definition and may well
    download). Neither has been measured here, and the bot has no separate sentence for
    either, so both fall through to whatever yt-dlp does with them today rather than
    being guessed at. Upgrade path if one shows up in the ledger: `is_upcoming` earns its
    own Spanish line ("todavía no empezó"), and `post_live` earns a measurement first --
    it is the one of the two that might just work.
    """
    if not info:
        return False
    return bool(info.get("is_live")) or info.get("live_status") == "is_live"


# The reason handed back to yt-dlp when the filter refuses. It goes to the log and
# never to the group -- the group's sentence is LIVE_STREAM_REPLY, chosen from the
# exception class. English, like every other thing written for the owner's eyes.
LIVE_STREAM_SKIP_REASON = "live stream: refused before reading any of it"


def _refuse_live_stream(info: dict, *, incomplete: bool = False) -> str | None:
    """yt-dlp's `match_filter`: the only hook that answers BEFORE a byte is read.

    Read off YoutubeDL.py at yt-dlp 2026.7.4: `_match_entry` is consulted from
    `process_video_result` (line 3042) after `formats` is populated and BEFORE format
    selection and `process_info`, and a non-None return makes it `return info_dict`
    immediately (line 3043). So the refusal costs the metadata request that had already
    happened and nothing else. There is no earlier hook that has seen the info dict.

    Returns a reason string to refuse, None to download. TWO WAYS THIS GOES WRONG, both
    from that same function:

    * `_match_entry` calls this as `match_filter(info_dict, incomplete=...)` inside a
      `try` that catches TypeError and retries POSITIONALLY, returning None -- download
      it -- whenever `incomplete` is truthy (it is: `process_video_result` passes
      `self._format_fields`, a non-empty set). So a callable that does not accept
      `incomplete` as a keyword, or that raises TypeError internally, fails OPEN and in
      silence. `incomplete` is accepted and deliberately unused: `is_live` comes from the
      extractor and is present on a partial dict too.
    * Returning yt-dlp's NO_DEFAULT sentinel makes it prompt the user on `input()`
      (line 1636). There is nobody at the terminal on a friend's laptop, so the bot would
      hang forever on the read -- the same unbounded failure in a new costume. This
      returns a string or None and never that sentinel.

    Not raising from here on purpose. An exception thrown inside a match_filter unwinds
    through yt-dlp's own extraction machinery, and the one raised where the bot can see
    it (download_into) is worth more than the two lines it saves.
    """
    if is_live_stream(info):
        return LIVE_STREAM_SKIP_REASON
    return None


def _ydl_options(target_dir: Path) -> dict:
    options = {
        "format": MEDIA_FORMAT,
        # The one guard that must answer before bytes move. See _refuse_live_stream:
        # a live stream downloads forever and takes the whole bot with it, and no
        # timeout, size or duration knob can fire on one (README.md §4.13).
        "match_filter": _refuse_live_stream,
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


def _extract(url: str, target_dir: Path) -> dict:
    """One yt-dlp extraction-and-download of `url`. Raises DownloadError, like yt-dlp.

    Its own function only because download_into calls it twice and the second call
    must be identical to the first -- a fresh YoutubeDL over the same options, no
    state carried over from the attempt that failed.
    """
    with yt_dlp.YoutubeDL(_ydl_options(target_dir)) as ydl:
        return ydl.extract_info(url, download=True)


def is_transport_failure(exc: BaseException) -> bool:
    """Whether yt-dlp never REACHED the site, as opposed to the site answering it.

    The discrimination is the exception, not the message, and it is available without
    guessing: yt-dlp wraps everything in a DownloadError and hangs the original on
    `exc_info` (YoutubeDL.trouble does it explicitly), and everything below the HTTP
    layer -- a name that would not resolve, a reset connection, a timeout, a broken
    proxy -- arrives there as a networking.exceptions.TransportError. Verified on
    2026-08-10 against yt-dlp 2026.7.4 by driving a real extraction into a dead
    listener: the DownloadError's exc_info[1] is the TransportError itself.
    Instagram's live bounce says the same thing in words -- "(caused by
    TransportError(...))" is in the ledger record -- but reading the class is exact
    where reading its own prose is a string match waiting to drift.

    An HTTP status is deliberately NOT one. HTTPError is a SIBLING of TransportError
    in yt-dlp's hierarchy, not a subclass, so a 404, a 403 and a rate-limit page are
    answers and are never retried. Neither is an ExtractorError: "this post is
    private" is a fact about the post and asking twice only wastes the group's time
    and the site's patience.

    ponytail: the TransportError subclasses come along for the ride -- an expired
    certificate and a misconfigured proxy are permanent and still cost one extra
    attempt. Accepted: they are host misconfiguration rather than weather, nobody
    here has ever seen one, and the cost of being wrong is one attempt, not a wrong
    answer. Upgrade path if one ever shows up in the ledger: exclude
    SSLError/ProxyError by name here, which is a one-line change with a check.
    """
    original = getattr(exc, "exc_info", None)
    return isinstance(original[1] if original else None, TransportError)


def _fallback_or_raise(
    url: str, target_dir: Path, exc: yt_dlp.utils.DownloadError, retried: bool
) -> Media:
    """What a failed video extraction becomes: an image post, or the error itself.

    A TRANSPORT failure skips the image fallback entirely, and that is not a saving,
    it is the only correct answer: the fallback's whole job is to ask the site what
    kind of post this is, and the site is exactly what could not be reached. Asking
    anyway costs the group another SOCKET_TIMEOUT seconds of silence to arrive at the
    same apology -- measured, it is why an Instagram bounce cost ~40 s and not ~20 s
    before this branch existed.
    """
    if not is_transport_failure(exc):
        # The video path is untouched: this runs only after it has already failed.
        # "No video in this post" is not necessarily a failure -- an Instagram image
        # post lands here and can still be delivered as a photo.
        image = _image_fallback(url, target_dir)
        if image is not None:
            return image
    note = RETRIED_NOTE if retried else ""
    raise ExtractionError(f"yt-dlp could not download {url}{note}: {exc}") from exc


def download_into(url: str, target_dir: Path) -> Media:
    """Download `url` into `target_dir`. Blocking. Nothing here knows about Telegram.

    Raises ExtractionError if the download produced no usable file. Split out from
    the context manager below so the Telegram layer can run it off the event loop
    while still holding the file open for the upload.

    A TRANSPORT failure buys one more attempt and nothing more. Two good links
    bounced on 2026-08-10 because the host's DNS timed out for a few minutes, and
    none of yt-dlp's own retry knobs reaches that failure: `retries` is the media
    downloader's loop (downloader/http.py), `fragment_retries` the fragment
    downloaders', and `extractor_retries` only exists where an extractor wraps a
    section in a RetryManager -- nine extractors do and Instagram is not one of them.
    All three were measured at exactly one transport attempt (README.md §5.3), so
    there was no knob to set instead of this.

    Written as two attempts rather than a loop deliberately: the evidence is a blip
    that cleared in under three minutes, so a second attempt is either enough or it
    was not a blip, and a schedule of them would only make the group wait longer for
    the same apology.
    """
    try:
        info = _extract(url, target_dir)
    except yt_dlp.utils.DownloadError as first:
        if not is_transport_failure(first):
            return _fallback_or_raise(url, target_dir, first, retried=False)
        # Not str(first) in the log: this one line stands in for the whole failure
        # while it is still recoverable, and the record with the full text is only
        # written if the second attempt fails too.
        log.warning("could not reach the site for %s; trying once more in %ss", url, TRANSPORT_RETRY_PAUSE)
        time.sleep(TRANSPORT_RETRY_PAUSE)
        try:
            info = _extract(url, target_dir)
        except yt_dlp.utils.DownloadError as second:
            return _fallback_or_raise(url, target_dir, second, retried=True)
        log.info("the second attempt at %s worked; the first one was the network", url)

    # BEFORE the `path is None` check, and that order is the whole point. A refused
    # live stream is a successful extraction that deliberately left no file, so the
    # generic "reported success but left no file" would fire first and misreport the
    # one refusal that has its own name, its own ledger class and its own sentence.
    # Asked again here rather than trusted from the filter's return value: the filter
    # answers inside yt-dlp and its reason is not handed back to the caller.
    if is_live_stream(info):
        raise LiveStreamError(f"{url} is a live stream; refused before downloading any of it")

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
    may treat it as final. TODAY IT IS NEVER ASKED OFF INSTAGRAM: _image_fallback
    checks the pasted URL's host first and declines for every other site, because a
    site with no image posts cannot have one. That guard is upstream of this
    function and stays upstream of it -- this one keeps answering about the dict it
    is given, which is what makes it assertable with plain dicts.

    Below that guard the discrimination this function cannot make -- "video-less
    post" vs "extraction that produced nothing" -- is still made one step later, in
    _image_fallback, by the only signal that is not a guess: whether any thumbnail
    yields an image. That guard is what stops an Instagram extraction that produced
    nothing, and it is NOT redundant with the host one: the host guard covers the
    sites that have no image posts, this one covers the site that does. Do not try
    to move either decision up here: the metadata an image post and a dead video
    hand back is the same shape, and every up-front signal available (a `preference`
    key, a placeholder title, a thumbnail count) would put the WORKING image path at
    risk to save requests on a failing one.

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

    THE FIRST QUESTION IS THE SITE, and it is asked before anything else runs. The
    owner pasted a YouTube short on 2026-08-09 and got a still frame back: YouTube
    was challenging this IP ("Sign in to confirm you're not a bot"), the extractor
    reports that through the same no-formats mechanism `ignore_no_formats_error`
    suppresses, so the probe returned no formats and 38 thumbnails, `is_image_post`
    said yes -- and unlike the dead-video case, the video EXISTS, so its thumbnails
    are real and one of them came down. Neither guard below can fire on that: there
    are genuinely no formats, and there genuinely is an image. The discrimination
    that works is the one nothing here was using -- only Instagram has image posts
    at all -- and it kills the whole class instead of the one symptom. It is also
    free: a failing YouTube link no longer pays for a probe and ~38 thumbnail
    fetches to arrive at the same apology.
    """
    if not has_image_posts(url):
        # Not a hedge and not a cost saving: on this site "a post with no video in
        # it" does not exist, so there is nothing for this function to find and the
        # caller must re-raise what the extractor said.
        log.info("%s is not a site with image posts; keeping the original failure", url)
        return None
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


def insult_tokens(text: str | None) -> list[str]:
    """`text` as bare comparable words: unaccented, lower case, no repeats, no noise.

    Four normalisations, each closing one way the same insult is typed in this group:

      * NFD plus dropping the combining marks makes "estúpido" and "estupido" the
        same word without a table of accented letters.
      * casefold, so shouting matches.
      * splitting on anything that is not a-z, so "bot," "¡estúpido!" and an emoji
        before the word are separators rather than part of it.
      * collapsing a run of one letter to a single one, so "estupidoooo" and "bott"
        are the words they are meant to be. This cannot lose anything the match
        needs: neither word being looked for has a doubled letter.
    """
    plain = "".join(
        char for char in unicodedata.normalize("NFD", text or "")
        if not unicodedata.combining(char)
    )
    return [re.sub(r"(.)\1+", r"\1", token) for token in re.split(r"[^a-z]+", plain.casefold()) if token]


def insult_words(text: str | None) -> tuple[str, str] | None:
    """The two words that insulted the bot, or None. Pure, and the whole feature.

    "bot estupido" in either order, any case, accents optional and tolerant of the
    typos the owner named -- "vot estupido", missing letters -- without a fuzzy
    matching library: difflib scores each token against each word and unicodedata
    handles the accents. Returns the matched tokens as normalised, because they are
    the only thing worth recording about a hit (see record_insult).

    THE FALSE-POSITIVE SIDE IS THE ONE THAT MATTERS. This reads every message in a
    group of friends who talk all day, and a bot that apologises when nobody
    insulted it is worse than one that misses a typo. Three rules do that work, and
    every one of them was earned by a phrase in the corpus:

      * BOTH WORDS, NEAR EACH OTHER. Neither fires alone -- "sos un estupido" is
        about a person and "gracias bot" is not an insult -- and they have to be
        within INSULT_MAX_GAP tokens of each other. This is the rule that carries
        almost everything: "estupido" next to something bot-shaped is essentially
        never anything but this.
      * A TOKEN MAY NOT BE LONGER THAN THE WORD IT MATCHES. A typo drops or changes
        a letter; a longer word is a different word. Without this, "bot" matches
        "bota" at 0.857 and "boton" at 0.750, and the bot apologises to a message
        about a boot or a button. It also refuses "robot", which is a real miss and
        the price of refusing "boton" -- difflib scores the two identically.
      * A THRESHOLD PER WORD, both sitting in a measured gap. See INSULT_WORDS.
      * AND A LIST OF WORDS THAT ARE NOT TYPOS, because the threshold cannot see
        the difference: "bro" and "vot" both share two letters with "bot" and both
        score 0.667. See NOT_THE_BOT.

    Known misses, all deliberate and all cheap: "botestupido" written without the
    space (nothing here joins tokens, and the owner did not ask for it), "robot
    estupido" (above), and anything further apart than the gap, like "el bot es un
    estupido". A missed joke costs nothing; a wrong apology costs the group's trust
    in every other thing the bot says.
    """
    tokens = insult_tokens(text)
    found: dict[str, list[tuple[int, str]]] = {}
    for index, token in enumerate(tokens):
        if token in NOT_THE_BOT:
            continue
        for word, threshold in INSULT_WORDS:
            if len(token) > len(word):
                continue
            if difflib.SequenceMatcher(None, token, word).ratio() >= threshold:
                found.setdefault(word, []).append((index, token))
    first, second = INSULT_WORDS[0][0], INSULT_WORDS[1][0]
    for left_at, left in found.get(first, ()):
        for right_at, right in found.get(second, ()):
            if abs(left_at - right_at) <= INSULT_MAX_GAP + 1:
                return left, right
    return None


def _host_is_one_of(url: str, hosts: frozenset[str]) -> bool:
    """Whether `url`'s host is one of `hosts`, or a subdomain of one.

    One host rule for the two questions this file asks about a pasted URL -- "do we
    handle this site" and "can this site have image posts" -- so a subdomain or a
    short form can never be read one way by one of them and the other way by the
    other. `m.instagram.com` is Instagram to both, or to neither.
    """
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host in hosts or any(host.endswith("." + h) for h in hosts)


def is_supported(url: str) -> bool:
    """Whether the URL points at one of the three sites the group actually pastes."""
    return _host_is_one_of(url, SUPPORTED_HOSTS)


def has_image_posts(url: str) -> bool:
    """Whether a post on this site can be images-only, i.e. whether it is Instagram.

    The one question the image fallback asks before it does anything. A YouTube or
    Facebook link that failed to extract is a failed video, and answering it with a
    still frame is the defect this closes (README.md §4.8).
    """
    return _host_is_one_of(url, IMAGE_POST_HOSTS)


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


def install_platform(text: str | None) -> str | None:
    """Which platform "/instalar algo" asked for, or None meaning both.

    Every word after the command is looked at, so "en windows" and "para mac" work;
    the first recognised one wins. Nothing here can fail -- a bare command, a typo, a
    word nobody anticipated all mean both, because the person asking is the person who
    does not know the words and an error would be the worst possible reply.
    """
    parts = (text or "").split(None, 1)
    after_command = parts[1] if len(parts) > 1 else ""
    for word in re.split(r"[^a-z]+", after_command.casefold()):
        platform = PLATFORM_WORDS.get(word)
        if platform:
            return platform
    return None


def install_command_line(platform: str) -> str:
    """The single line the friend pastes, as plain text. The caller escapes it.

    One line and not a numbered list, because it is copied by tapping a block, not
    read. `&&` and not `;` so a failing clone stops instead of running the launcher in
    the wrong directory.
    """
    return (
        f"mkdir -p {CLONE_PARENT} && cd {CLONE_PARENT} && git clone {CLONE_URL} "
        f"&& cd {CLONE_DIR} && ./{LAUNCHER_FILE[platform]}"
    )


def install_block(platform: str) -> list[str]:
    """The two lines one platform gets, in Spanish, as Telegram HTML.

    Two lines each, which is what keeps the budget where it was, but they no longer
    say the same *kind* of thing and that is this feature rather than an accident:
    macOS is handed a command to paste because a downloaded launcher cannot run there
    at all, and Windows is handed a file to download because a .cmd needs no exec bit.
    README.md 2.2 has both measurements.

    Which means the second line differs too. On macOS it is the pasteable block, and
    it has to be a <pre> or Telegram offers nothing to copy. On Windows it is prose:
    a URL inside a code block would not be tappable, and what Windows needs said is
    what the machine will ask before it runs the file.
    """
    intro = f"<b>En {html.escape(PLATFORM_NAMES[platform])}</b> — {html.escape(PLATFORM_INTRO[platform])}"
    if platform == "windows":
        return [intro, html.escape(WINDOWS_CONFIRMATION)]
    return [intro, f"<pre>{html.escape(install_command_line(platform))}</pre>"]


def install_reply(platform: str | None = None) -> str:
    """The install instructions the bot hands out, in Spanish, as Telegram HTML.

    Three lines for one platform, five for both, and that ceiling is the feature: the
    audience taps and skims, so a wall buries the one line that matters. A line has to
    earn its place by stopping a friend in the next minute -- on macOS git, on Windows
    what the machine asks before it runs a downloaded file, and on both the token.
    Everything else a host needs is in EMPEZAR-ACA.md, which arrives with the code
    either way, and the launcher asks about the hand-over itself, when it matters.
    README.md 2.2.

    `platform` None means both blocks, and that is the common case rather than the
    fallback: a tap on Telegram's command menu sends the bare command.

    Pure, and that is a security property and not a style preference: nothing here
    reads the environment, a file or a subprocess, so there is no token to leak, and
    _check_install_instructions proves it by invariance. HTML and not MarkdownV2 --
    only the code block needs markup, and MarkdownV2 would demand escaping . - ( ) !
    in every Spanish sentence. Every interpolated value goes through html.escape; the
    `&&` in the pasted command is the one that bites.
    """
    lines = []
    for each in (platform,) if platform else ("mac", "windows"):
        lines.extend(install_block(each))
    # The one obstacle no friend can clear alone, and the rule as much as the copy:
    # the bot says who hands out the token, and it is never the bot.
    lines.append(
        "El token te lo pasa el dueño por privado, nunca por acá: la ventana te lo "
        "pide la primera vez y queda guardado."
    )
    return "\n".join(lines)


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


def record_insult(
    message: telegram.Message, words: tuple[str, str], path: Path | None = None
) -> None:
    """Append one insult to its own file. Like record_rejection, this may not raise.

    Same reason as the ledger's: this is diagnostics bolted onto a reply, and a full
    disk may not cost the group the answer it is about to get. Same shape too --
    when, chat, message -- so one pair of eyes reads both files the same way.

    WHAT IS STORED OF THE MESSAGE IS THE TWO MATCHED WORDS AND NOTHING ELSE. The
    privacy rule that keeps message bodies out of the ledger holds here, and two
    words are not the body: by construction they are near-copies of "bot" and
    "estupido" -- that is the only way they got matched -- so they cannot carry
    anything private. They are worth the two words of exposure because they are the
    only thing that can tell the owner a hit was WRONG. A record of dates alone
    cannot be audited: a false positive and a real insult look identical in it, and
    the thresholds could never be moved on evidence. Their ratios are not stored
    because they are recomputable from the words.
    """
    try:
        record = {
            "when": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
            "chat_id": getattr(message, "chat_id", None),
            "message_id": getattr(message, "message_id", None),
            "words": list(words),
        }
        with (path or INSULT_LEDGER).open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        log.exception("could not write the insult log; the reply is unaffected")


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
        await message.reply_text(
            f"mandá un link de YouTube, Instagram o Facebook y te lo bajo.\n"
            f"Para prenderlo en tu compu: /{INSTALL_COMMAND}"
        )


async def on_install(update: telegram.Update, _context: object) -> None:
    """Hand out the install instructions. Works in a group and in a DM alike.

    The argument is read off the message text rather than out of PTB's context, so
    the whole path from what the friend typed to what is sent is reachable from a
    plain Message and the self-check drives all of it.

    Nothing here reads the environment. That is the point -- see install_reply.
    """
    message = update.effective_message
    if message is None:
        return
    await _reply_text(
        message,
        install_reply(install_platform(getattr(message, "text", None))),
        parse_mode=telegram.constants.ParseMode.HTML,
    )


async def on_message(update: telegram.Update, _context: object) -> None:
    """Everything the bot does with a message. Two reasons to react, in this order.

    Links first, and the insult after, on purpose. `_deliver` cannot raise -- it
    catches its own failures and apologises -- while the insult reply is an ordinary
    reply_text that can time out like any other, and that used to be enough to lose
    a whole update (README.md §4.6). Answering the links first means the worst a
    failed insult reply can cost is the joke: the videos are already sent and the
    ledger is already written. The other order would let a network hiccup swallow
    the download that somebody actually asked for.

    Neither half knows about the other. A message can be both, and then both happen.
    """
    message = update.effective_message
    if message is None:
        return
    await _handle_links(message)
    await _handle_insult(message)


async def _handle_insult(message: telegram.Message) -> None:
    """Answer a message that called the bot stupid, and write it down.

    The record goes first, for the same reason the ledger is written before the
    apology: it cannot raise, so it cannot cost the reply, and a network bad enough
    to kill the reply still leaves the evidence behind.

    Not protected by a try/except of its own, and that is deliberate -- this file
    swallows exceptions in exactly three places and each one is load-bearing (see
    AGENTS.md). If the reply fails, the exception reaches on_error, which logs it;
    the links are already delivered by then, so nothing that matters is lost, and a
    fourth silent swallow would cost more than the joke it protects.
    """
    words = insult_words(getattr(message, "text", None) or getattr(message, "caption", None))
    if words is None:
        return
    # The two matched words, never the message. Same rule as the ledger and the
    # ignore-logging: enough to tell a real insult from a false alarm, and no more.
    log.info("message %s: the bot was called %s; apologising", message.message_id, " ".join(words))
    record_insult(message, words)
    await _reply_text(message, INSULT_REPLY)


async def _handle_links(message: telegram.Message) -> None:
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

    "Nothing to do" below is about links and stays literally true of them: a message
    that only insults the bot logs this line and is then answered by _handle_insult,
    which says so on its own line. The wording is not softened because it is what
    README.md §5 tells whoever is diagnosing a link that produced no video.

    SILENCE IS STILL THE DEFAULT for a host the bot does not handle, and the one
    exception is narrow on purpose: MEDIA_PLATFORM_HOSTS, where somebody pasting a
    link was plainly waiting for a video and cannot tell "not my site" from "the bot
    is down". Everything else -- a news article, a Spotify link, a Google Doc -- is
    still left alone, because a bot that answers every URL in the chat is worse than
    one that stays quiet. A message with no URL in it is untouched by all of this.
    """
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
        # And on a handful of sites, silence is the wrong answer: somebody who pastes
        # a TikTok is waiting for a video, and from the chat "I do not do that site"
        # is indistinguishable from the bot being down. ONE reply for the message and
        # not one per URL -- three TikToks pasted together are one question.
        #
        # Recording and replying are separate on purpose and in that order. Every
        # unsupported URL is recorded whether or not anything is said, because the
        # record is the roadmap input and the reply is courtesy to whoever is waiting;
        # and the record goes first so a failing reply cannot cost it.
        #
        # Not wrapped in a try/except, like the insult reply and for the same reason:
        # this file swallows an exception in exactly four places and each one is
        # load-bearing. Nothing was delivered here and nothing is owed to the group,
        # the ledger is already written, and the worst a failure can cost is the
        # insult answer of the same message -- cheaper than a fifth silent swallow.
        if any(_host_is_one_of(url, MEDIA_PLATFORM_HOSTS) for url in urls):
            await _reply_text(message, UNSUPPORTED_MEDIA_REPLY)
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


async def _reply_text(
    message: telegram.Message, text: str, parse_mode: str | None = None
) -> None:
    """Send a short text reply with timeouts of its own. May raise.

    `parse_mode` defaults to None and every caller but one leaves it there. That
    default is load-bearing: the apology, the oversize reply and the insult answer
    are plain Spanish that nobody escapes, and one of them carries a raw URL. Turning
    markup on for all of them -- or configuring a PTB `Defaults` object, which would
    do it invisibly -- would make an unescaped character in a future line either
    vanish from the message or fail the send outright.
    """
    await message.reply_text(
        text,
        parse_mode=parse_mode,
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


BOT_COMMANDS = (
    ("start", "Qué hace el bot"),
    (INSTALL_COMMAND, "Cómo prenderlo en tu compu (Mac o Windows)"),
)


async def _publish_commands(app: Application) -> None:
    """Put the commands in Telegram's menu, so the friend can find them by tapping.

    Called by PTB after start-up, once per run. It is a global bot setting rather
    than a per-instance one, so every host re-declares the same list and the last one
    wins -- idempotent, and it means the menu cannot drift from this file no matter
    who is hosting or what anybody typed into BotFather once.

    The fourth place in this file that swallows an exception, and it is here for the
    same reason as _apologise: a convenience must never cost the group its bot. A
    post_init callback that raises aborts run_polling, so without this a transient
    network blip while publishing a *menu* would end with a friend staring at a
    traceback in a window they cannot read, and no bot running. Narrow on purpose --
    only Telegram's own error class, so a real bug in the list above still shouts.
    """
    try:
        await app.bot.set_my_commands(
            [telegram.BotCommand(command, description) for command, description in BOT_COMMANDS]
        )
    except telegram.error.TelegramError:
        log.warning("could not publish the command menu; the bot works, the menu may be stale")


def build_application(token: str) -> Application:
    """Wire the handlers onto an Application. Separate from main() so the self-check
    can assert the wiring: a handler that exists but was never registered is the one
    failure this file cannot see from the outside, and forgetting add_error_handler
    would silently restore the wall of tracebacks. Builds nothing over the network."""
    app = Application.builder().token(token).post_init(_publish_commands).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler(INSTALL_COMMAND, on_install))
    app.add_handler(MessageHandler(MESSAGE_FILTER, on_message))
    app.add_error_handler(on_error)
    return app


def configure_logging() -> None:
    """The levels the bot runs at. Split out of main() so the self-check can assert them.

    INFO for the bot itself, because the launcher's window is the only diagnostic a
    friend has (README.md §5): the deliveries, the bounced links, the conflict lines
    and the retry line all live there and none of them may go quiet.

    WARNING for REQUEST_URL_LOGGER, because at INFO it prints the bot's own token.
    Measured on the live process on 2026-08-10: of 379 lines logged in 64 minutes of
    production, **374 carried the token** -- about six a minute, forever. The launcher
    does not redirect stdout, so on a friend's machine those go to a visible Terminal
    window, and that window is exactly what he pastes when he says "mirá, no anda".
    The paste would be full control of the bot: read the group and post as the bot.

    Silencing the one logger instead of filtering the records is deliberate. A
    redaction filter has to be right about every URL shape httpx will ever log and is
    silent when it is wrong; a level that is too high fails closed.

    The root level is set explicitly rather than through basicConfig's `level=`:
    basicConfig does nothing at all once the root logger has a handler, which is the
    state the self-check is in when it calls this, and a check that cannot observe
    this function is a check of nothing.
    """
    # ponytail: this silences the logger measured to leak, not every conceivable leak.
    # A rename inside the httpx namespace is covered, and so is anything that logs a
    # Telegram URL through that logger; a future python-telegram-bot on a different
    # HTTP client is not, and the self-check drives httpx directly so it would keep
    # passing. Upgrade path the day PTB changes client: assert the token's absence
    # around a real telegram.request object, which needs a transport seam PTB does not
    # expose today. Nothing here redacts: whatever the bot logs on purpose still prints.
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger(REQUEST_URL_LOGGER).setLevel(logging.WARNING)


def main() -> None:
    global _take_over_until

    configure_logging()
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

    # is_live_stream: the three rows of the measured table, and the trap is row three.
    # Field values are the real ones from `--simulate` on 2026-08-10, not invented.
    assert is_live_stream({"is_live": True, "live_status": "is_live", "was_live": False,
                           "duration": None}), "a stream happening now must be refused"
    assert not is_live_stream({"is_live": False, "live_status": "not_live", "was_live": False,
                               "duration": 19}), "an ordinary video must be downloaded"
    # THE ONE THAT SILENTLY BREAKS THIS FEATURE. A finished stream reports was_live=True
    # and is an ordinary bounded video -- 1146 s, 178 MB. Refusing it would reject every
    # stream replay the group pastes, and no live URL would ever reveal it.
    assert not is_live_stream({"is_live": False, "live_status": "was_live", "was_live": True,
                               "duration": 1146}), "a FINISHED stream is an ordinary video"
    # Either field alone is enough, because either one alone could drift.
    assert is_live_stream({"is_live": True}), "the extractor's own flag is enough"
    assert is_live_stream({"live_status": "is_live"}), "yt-dlp's normalisation is enough"
    # Nothing said is not a live stream: yt-dlp runs the filter over playlist entries too.
    assert not is_live_stream({}), "an empty info dict is not a live stream"
    assert not is_live_stream(None), "no info at all is not a live stream"
    # The values deliberately left alone, pinned so a later widening is a decision.
    assert not is_live_stream({"live_status": "is_upcoming"}), "a premiere is not refused here"
    assert not is_live_stream({"live_status": "post_live"}), "an ended stream is bounded"
    print("ok  is_live_stream")

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
        # The owner's paste of 2026-08-09 plus the tail yt-dlp appends to it. NOT
        # re-measured here and the only row where that is true: the challenge on
        # this IP lifted before the fix was written. See BOT_CHECK_ERROR.
        "youtube, challenging this address":
            "yt-dlp could not download https://youtube.com/shorts/5kC43KL_mBE: "
            + BOT_CHECK_ERROR,
        # The 2026-08-10 bounce, and the only detail here that the bot itself has
        # already added to: a transport failure reaches the group only after the
        # retry has failed too, so the string this row must match is the one that
        # carries RETRIED_NOTE. Building it that way rather than from the raw error
        # is the point -- a note placed where it breaks the match would be invisible
        # to every other assert in this function.
        "the network was down, and still down a moment later":
            "yt-dlp could not download https://www.instagram.com/reel/DbqocqEsbVs/"
            + RETRIED_NOTE + ": " + DNS_FAILURE_ERROR,
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

    # The fourth hedge is a different KIND of hedge and gets its own asserts. The
    # other three cannot tell two causes apart; this one cannot tell whose network
    # broke -- the host's, the friend's, or the site being unreachable all produce
    # the identical string -- so the reply offers all three and blames none. And it
    # is the only line in the table that asks for something back, because it is the
    # only failure that clears on its own.
    down = replies["the network was down, and still down a moment later"]
    assert "puede ser" in down, f"it cannot know whose connection it was: {down}"
    assert "de nuevo" in down, f"the one failure worth resending has to say so: {down}"
    for blame in ("tu wifi", "tu internet", "tu conexión"):
        assert blame not in down, f"the friend's network is a guess, not a diagnosis: {down}"
    # A different transport failure is deliberately NOT this row: it is retried the
    # same way, but nobody has measured it clearing on its own, so it stays generic.
    #
    # THE STRING BELOW IS MEASURED, and it has to be, because the two markers this
    # row does not use are both inside it. Produced on 2026-08-10 by pointing a real
    # Instagram extraction at a local listener that accepts and closes the
    # connection: same call site as the DNS timeout, different curl code. Written
    # short, it would agree with a widened row by accident and prove nothing --
    # mutation testing caught exactly that, so the tail stays.
    reset = (
        "yt-dlp could not download https://www.instagram.com/reel/DbGNFqVKnB-/ (retried once): "
        "ERROR: [Instagram] DbGNFqVKnB-: Unable to download webpage: Failed to perform, "
        "curl: (56) Recv failure: Connection reset by peer. "
        "See https://curl.se/libcurl/c/libcurl-errors.html first for more details. "
        "(caused by TransportError('Failed to perform, curl: (56) Recv failure: Connection "
        "reset by peer. See https://curl.se/libcurl/c/libcurl-errors.html first for more "
        "details.'))"
    )
    assert "failed to perform, curl:" in reset.casefold(), "the wider marker has to really be in here"
    assert "caused by transporterror" in reset.casefold(), "so does the widest one"
    assert failure_reply(reset) == FAILURE_REPLY, \
        "this row is keyed on the failure that was measured, not on curl or transport in general"

    # And the mirror image of that rule: a signature that IS unambiguous must not
    # hedge. YouTube says plainly that it wants a login, so "puede que" here would
    # be a hesitation the evidence does not call for -- and the group is told the
    # one thing it can act on, that the link is not the problem.
    blocked = replies["youtube, challenging this address"]
    assert "puede que" not in blocked, f"this one is not ambiguous: {blocked}"
    assert blocked != replies["youtube, unavailable (A)"], \
        "a blocked video and a deleted one are different failures with different answers"
    # The markers step around the apostrophe in "you're" on purpose: it comes from
    # YouTube's own JSON, and a typographic one would kill the row silently. Both
    # spellings must land on the same line.
    assert failure_reply(
        "ERROR: [youtube] x: Sign in to confirm you’re not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication."
    ) == blocked, "a typographic apostrophe must not be able to kill the row"
    # Half of it is not it: YouTube's OTHER sign-in wall is the age one, and it is a
    # different failure with no measured signature, so it stays generic.
    assert failure_reply("ERROR: [youtube] x: Sign in to confirm your age") == FAILURE_REPLY

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


@contextmanager
def _yt_dlp_stub(
    workspace: Path,
    info: dict,
    video_error: str,
    image: str | None = None,
    slides: int = 0,
) -> Iterator[list[str]]:
    """yt-dlp replaced by a stand-in, yielding the list of what it was asked to do.

    The fallback's whole behaviour is "which network calls happen after a download
    failed", so the check has to be able to assert that a call did NOT happen, not
    only that the outcome was right. Each `extract_info` appends one of "video",
    "probe", "thumbnails" or "slides"; the three are told apart the same way the
    real options tell them apart -- `download`, `skip_download` and the playlist cap.

    `image` is the file the thumbnail run leaves behind, or None for a run that
    yields nothing; `slides` is how many carousel files the album run writes.
    """
    asked: list[str] = []
    jpeg = b"\xff\xd8\xff" + b"x" * 4000

    class _FakeYdl:
        def __init__(self, options: dict) -> None:
            self.options = options

        def __enter__(self) -> "_FakeYdl":
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def extract_info(self, _url: str, download: bool = False) -> dict:
            if not download:
                asked.append("probe")
                return info
            # The thumbnail and carousel runs both skip the video; only the carousel
            # one lifts the one-item cap, exactly as _download_carousel_slides does.
            if self.options.get("skip_download"):
                if self.options.get("playlist_items") == f"1:{ALBUM_MAX_ITEMS}":
                    asked.append("slides")
                    for n in range(1, slides + 1):
                        (workspace / f"slide-{n:03d}.12.jpg").write_bytes(jpeg + bytes(n))
                    return info
                asked.append("thumbnails")
                if image is not None:
                    (workspace / image).write_bytes(jpeg)
                return info
            asked.append("video")
            raise yt_dlp.utils.DownloadError(video_error)

    class _FakeYtDlp:
        YoutubeDL = _FakeYdl
        utils = yt_dlp.utils

    real = globals()["yt_dlp"]
    globals()["yt_dlp"] = _FakeYtDlp
    try:
        yield asked
    finally:
        globals()["yt_dlp"] = real


def _attempt_download(url: str, workspace: Path, **stub: object) -> tuple[Media | str, list[str]]:
    """download_into with yt-dlp stubbed out: the Media or the error text, and the calls."""
    with _yt_dlp_stub(workspace, **stub) as asked:  # type: ignore[arg-type]
        try:
            return download_into(url, workspace), asked
        except ExtractionError as exc:
            return str(exc), asked


# What an unavailable YouTube video really hands back: no formats and 38 thumbnails
# synthesised from a URL template, each with a `preference` an Instagram thumbnail
# does not have. Measured 2026-08-09. A bot-challenged video hands back the same
# SHAPE -- which is the entire reason neither guard downstream can tell them apart.
DEAD_YOUTUBE_INFO = {
    "formats": [],
    "thumbnails": [{"id": str(n), "url": f"https://i.ytimg.com/vi/AAAAAAAAAAA/{n}.jpg",
                    "preference": n - 37} for n in range(38)],
}

# The failure the owner hit on 2026-08-09, and the only signature in this file the
# owner measured and the branch could not re-measure: the challenge on this IP had
# lifted by the time the fix was written (5kC43KL_mBE extracts 28 formats again).
# His paste is the first sentence; the rest is yt-dlp 2026.7.4's own text, read off
# extractor/common.py `_login_hint("cookies")` + youtube/_base.py
# `_youtube_login_hint`, which is what appends it.
BOT_CHECK_ERROR = (
    "ERROR: [youtube] 5kC43KL_mBE: Sign in to confirm you're not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication. "
    "See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  "
    "for how to manually pass cookies. "
    "Also see  https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies  "
    "for tips on effectively exporting YouTube cookies"
)


def _check_failed_extraction_keeps_its_error() -> None:
    """A failed extraction reaches the ledger as the EXTRACTOR's words, not the bot's.

    The whole slice, end to end, with the network removed: yt-dlp is replaced by the
    stand-in above, which raises on the video download and then hands the fallback
    the info dict an unavailable YouTube video really returns. Both branches of the
    discrimination are covered, because a fix that made every image post fail would
    pass the negative half on its own -- and the positive half is an INSTAGRAM post,
    because since the host guard landed a YouTube URL cannot reach that path at all.
    """
    unavailable = "ERROR: [youtube] AAAAAAAAAAA: Video unavailable"
    with temp_workspace() as workspace:
        failed, asked = _attempt_download(
            "https://www.youtube.com/watch?v=AAAAAAAAAAA", workspace,
            info=DEAD_YOUTUBE_INFO, video_error=unavailable, image=None,
        )
    assert asked == ["video"], f"a YouTube failure no longer probes at all: {asked}"
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
    empty_media = "ERROR: [Instagram] DbvWPFQxPkI: There is no video in this post"
    image_post = {"formats": [], "thumbnails": [{"id": "12", "url": "https://x/a.jpg"}]}
    with temp_workspace() as workspace:
        delivered, asked = _attempt_download(
            "https://www.instagram.com/p/DbvWPFQxPkI/", workspace,
            info=image_post, video_error=empty_media, image="best.jpg",
        )
    assert isinstance(delivered, Media), f"an image that downloads must still be sent: {delivered}"
    assert delivered.path.name == "best.jpg" and not delivered.has_audio, delivered
    assert asked == ["video", "probe", "thumbnails"], asked

    # The third branch, and READ THIS BEFORE MOVING IT: "thumbnails that yield no
    # image mean the post never was an image post". Its cover used to be the dead
    # YouTube link, and the host guard took that away -- YouTube does not reach the
    # fallback at all any more, so deleting this branch went green until this case
    # existed (caught by mutation testing, 2026-08-09). The guard is still
    # load-bearing where it still runs: an Instagram post whose signed thumbnail
    # URLs all fail leaves the fallback with nothing, and raising an error of its
    # own there would put the FALLBACK's opinion in the ledger instead of the
    # extractor's, which is the defect the previous branch fixed.
    with temp_workspace() as workspace:
        nothing, asked = _attempt_download(
            "https://www.instagram.com/p/DbvWPFQxPkI/", workspace,
            info=image_post, video_error=empty_media, image=None,
        )
    assert isinstance(nothing, str), f"no image means the failure stands: {nothing}"
    assert empty_media in nothing, f"the extractor's own words must survive: {nothing}"
    assert "no downloadable image either" not in nothing, (
        f"the fallback's error replaced the extractor's, which is the whole defect: {nothing}"
    )
    assert asked == ["video", "probe", "thumbnails"], asked
    print("ok  a failed extraction keeps its own error all the way to the ledger")


def _check_only_instagram_can_be_an_image_post() -> None:
    """A site with no image posts never reaches the image fallback at all.

    The defect this closes, in one line: a YouTube video the site refuses to serve
    comes back from the fallback probe with no formats and REAL thumbnails, so
    `is_image_post` says yes and one of them downloads -- and the group gets a still
    frame of a video it asked for. Neither existing guard can fire on that, which is
    why the discrimination is the site.

    Dict-driven and no network. The cases below are the two failing sites, the two
    Instagram host spellings and both Instagram post shapes; what is asserted is not
    only the outcome but WHICH CALLS HAPPENED, because "never reaches the image
    path" is a statement about calls, and an implementation that probed first and
    threw the answer away would pass an outcome-only check.
    """
    for url in ("https://www.instagram.com/p/x/", "https://instagram.com/reel/x/",
                "https://instagr.am/p/x/", "https://m.instagram.com/p/x/",
                "https://INSTAGRAM.COM/p/x/"):
        assert has_image_posts(url), f"{url} is Instagram and can be an image post"
    for url in ("https://www.youtube.com/watch?v=x", "https://youtu.be/x",
                "https://youtube.com/shorts/5kC43KL_mBE", "https://www.youtube-nocookie.com/x",
                "https://www.facebook.com/share/v/1L8yZSLkWq/",
                "https://www.facebook.com/share/r/1L8yZSLkWq/",
                "https://fb.watch/x/", "https://fb.com/x", "https://www.tiktok.com/@a/video/1",
                "https://notinstagram.com/p/x/", "https://instagram.com.evil.example/p/x/",
                "not a url at all", ""):
        assert not has_image_posts(url), f"{url} has no image posts and must keep its error"
    # A site the bot does not even handle can never be the one site that has image
    # posts; is_supported is the outer gate and this is a subset of it.
    assert IMAGE_POST_HOSTS <= SUPPORTED_HOSTS, IMAGE_POST_HOSTS - SUPPORTED_HOSTS
    for host in IMAGE_POST_HOSTS:
        assert is_supported(f"https://{host}/p/x/"), host

    # The live defect, with an image that WOULD have come down. This is the case the
    # thumbnail guard cannot catch: the video exists, so its thumbnails are real.
    with temp_workspace() as workspace:
        blocked, asked = _attempt_download(
            "https://youtube.com/shorts/5kC43KL_mBE", workspace,
            info=DEAD_YOUTUBE_INFO, video_error=BOT_CHECK_ERROR, image="poster.jpg",
        )
    assert isinstance(blocked, str), f"a blocked video must never be delivered as a still: {blocked}"
    assert "Sign in to confirm" in blocked, f"the extractor's own words must survive: {blocked}"
    assert asked == ["video"], f"nothing may run after the download failed off Instagram: {asked}"
    # And the group is told the truth about it, in its own line.
    named = failure_reply(blocked)
    assert named != FAILURE_REPLY, f"the bot check is measured and must be named: {named}"
    assert named != failure_reply("ERROR: [youtube] x: Video unavailable"), \
        "a blocked video and a deleted one are different things and must read differently"
    assert "youtube" in named and "puede que" not in named, \
        f"this signature is not ambiguous, so the line must not hedge: {named}"

    # Facebook is the other site with no image posts, and its share/v/ links are the
    # ones whose host and extractor could disagree. Same answer either way.
    with temp_workspace() as workspace:
        facebook, asked = _attempt_download(
            "https://www.facebook.com/share/v/1L8yZSLkWq/", workspace,
            info=DEAD_YOUTUBE_INFO, video_error="ERROR: [facebook] 1: Cannot parse data",
            image="poster.jpg",
        )
    assert isinstance(facebook, str), f"facebook has no image posts either: {facebook}"
    assert asked == ["video"], asked

    # Instagram, unchanged: the single image post and the carousel both still work,
    # including through the short host, or this fix would have broken the feature it
    # is protecting.
    image_post = {"formats": [], "thumbnails": [{"id": "12", "url": "https://x/a.jpg"}]}
    no_video = "ERROR: [Instagram] x: There is no video in this post"
    for url in ("https://www.instagram.com/p/DbvWPFQxPkI/", "https://instagr.am/p/DbvWPFQxPkI/"):
        with temp_workspace() as workspace:
            photo, asked = _attempt_download(
                url, workspace, info=image_post, video_error=no_video, image="best.jpg",
            )
        assert isinstance(photo, Media), f"{url}: an Instagram image post must still be sent: {photo}"
        assert photo.path.name == "best.jpg" and not photo.slides, photo
        assert asked == ["video", "probe", "thumbnails"], asked

    carousel = {"formats": [],
                "entries": [{"formats": [], "thumbnails": [{"id": "12", "url": "https://x/a.jpg"}]}
                            for _ in range(10)]}
    with temp_workspace() as workspace:
        album, asked = _attempt_download(
            "https://www.instagram.com/p/DbcsX-BlkZX/?img_index=9", workspace,
            info=carousel, video_error=no_video, slides=10,
        )
    assert isinstance(album, Media), f"an Instagram carousel must still be an album: {album}"
    assert len(album.slides) == 10 and album.slide_total == 10, album
    assert asked == ["video", "probe", "slides"], asked
    print("ok  only Instagram reaches the image fallback; everything else keeps its error")


# The bounce that prompted all of this, copied out of the ledger record written at
# 2026-08-10T14:30:38-03:00 rather than retyped from anybody's summary of it. Both
# halves of this feature are keyed on this one failure: the exception underneath it
# is what earns the retry, and the words in it are what the group hears when the
# retry does not help either.
DNS_FAILURE_ERROR = (
    "ERROR: [Instagram] DbqocqEsbVs: Unable to download webpage: Failed to perform, "
    "curl: (28) Resolving timed out after 20001 milliseconds. "
    "See https://curl.se/libcurl/c/libcurl-errors.html first for more details. "
    "(caused by TransportError('Failed to perform, curl: (28) Resolving timed out "
    "after 20001 milliseconds. See https://curl.se/libcurl/c/libcurl-errors.html "
    "first for more details.'))"
)

# What the site says when it HAS answered. Not a transport failure, not retryable,
# and the reason the discrimination has to be the exception: this text and the one
# above arrive as the same DownloadError class and differ only in what yt-dlp hung
# on its exc_info.
EMPTY_MEDIA_ERROR = "ERROR: [Instagram] AAAAAAAAAAA: Instagram sent an empty media response"


def _download_error(text: str, cause: BaseException | None) -> yt_dlp.utils.DownloadError:
    """The DownloadError yt-dlp really raises, with `cause` where yt-dlp puts it.

    Raised and caught rather than constructed, because what makes a failure a
    transport failure is the sys.exc_info() tuple YoutubeDL.trouble copies onto the
    DownloadError -- a hand-built lookalike would let a mutation that reads the wrong
    attribute pass. `cause=None` is the shape of a failure that was nobody's
    exception, which must never be read as a transport one.
    """
    if cause is None:
        return yt_dlp.utils.DownloadError(text)
    try:
        raise cause
    except BaseException:  # noqa: BLE001 -- the point is to be inside the except block
        return yt_dlp.utils.DownloadError(text, sys.exc_info())


def _check_transport_failures_are_retried() -> None:
    """A blip costs one more attempt; an answer costs none.

    Everything here is driven by fakes that raise -- no network, and the retry's
    pause is a fake clock, so the check does not spend TRANSPORT_RETRY_PAUSE seconds
    proving that it waited. The clock is asserted on: a retry with no pause at all is
    a different feature from the one this file documents.

    The case that matters most is the second one. A retry that fires on a restricted
    post or a dead video is invisible in production -- the group still gets the right
    apology, just twice as slowly, on every single failure -- so it is asserted here
    rather than left to be noticed.
    """
    curl_28 = "Failed to perform, curl: (28) Resolving timed out after 20001 milliseconds"

    # The predicate on its own, against the four shapes it has to tell apart.
    assert is_transport_failure(_download_error(DNS_FAILURE_ERROR, TransportError(curl_28)))
    assert is_transport_failure(
        _download_error("ERROR: incomplete read", yt_dlp.networking.exceptions.IncompleteRead(9, 99))
    ), "a TransportError subclass is still the network, not an answer"
    assert not is_transport_failure(
        _download_error(EMPTY_MEDIA_ERROR, yt_dlp.utils.ExtractorError("empty media response"))
    ), "the site answering is not a transport failure"
    assert not is_transport_failure(_download_error(EMPTY_MEDIA_ERROR, None)), (
        "a DownloadError carrying no original exception is not evidence of anything"
    )
    assert not is_transport_failure(RuntimeError("boom")), "only yt-dlp's own shape counts"
    assert TRANSPORT_RETRY_PAUSE > 0, "a retry with no pause at all just asks the same dead socket twice"

    def _run(outcomes: list[object], url: str) -> tuple[object, list[str], list[float], list[str]]:
        """download_into over a yt-dlp that hands back `outcomes`, one per attempt.

        Returns what came out (a Media or the error text), what was asked of yt-dlp,
        how long the bot slept, and what it logged. An attempt beyond the end of
        `outcomes` is an IndexError rather than a repeat: "not a loop" is part of the
        contract. The log is captured rather than printed because every failure in
        here is deliberate and a self-check that shouts about them teaches whoever
        runs it to ignore its output.
        """
        asked: list[str] = []
        pauses: list[float] = []
        remaining = list(outcomes)

        class _FakeYdl:
            def __init__(self, options: dict) -> None:
                self.options = options

            def __enter__(self) -> "_FakeYdl":
                return self

            def __exit__(self, *_exc: object) -> bool:
                return False

            def extract_info(self, _url: str, download: bool = False) -> dict:
                if not download:
                    # The image fallback's probe. Reaching it at all is the thing
                    # asserted on below, so it answers "not an image post" and lets
                    # the original failure stand.
                    asked.append("probe")
                    return {"formats": [{"format_id": "0"}]}
                asked.append("video")
                outcome = remaining.pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                # An attempt that works leaves a file behind, like the real one.
                Path(self.options["outtmpl"]).parent.joinpath("reel.mp4").write_bytes(b"\x00" * 2048)
                return outcome

        class _FakeYtDlp:
            YoutubeDL = _FakeYdl
            utils = yt_dlp.utils

        class _FakeClock:
            def sleep(self, seconds: float) -> None:
                pauses.append(seconds)

        real_yt_dlp, real_time = globals()["yt_dlp"], globals()["time"]
        globals()["yt_dlp"], globals()["time"] = _FakeYtDlp, _FakeClock()
        try:
            with temp_workspace() as workspace, _capture_log(logging.WARNING) as logged:
                try:
                    return download_into(url, workspace), asked, pauses, logged
                except ExtractionError as exc:
                    return str(exc), asked, pauses, logged
        finally:
            globals()["yt_dlp"], globals()["time"] = real_yt_dlp, real_time

    reel = "https://www.instagram.com/reel/DbqocqEsbVs/"

    def blip() -> yt_dlp.utils.DownloadError:
        """The 2026-08-10 bounce, as the exception the bot actually caught."""
        return _download_error(DNS_FAILURE_ERROR, TransportError(curl_28))

    # 1. The network is down and stays down: two attempts, one pause, and the record
    #    says both -- that a retry happened, and what the failure really was.
    failed, asked, pauses, logged = _run([blip(), blip()], reel)
    assert isinstance(failed, str), f"a failure that survives the retry is still a failure: {failed}"
    assert asked == ["video", "video"], f"one retry, not none and not a loop: {asked}"
    assert pauses == [TRANSPORT_RETRY_PAUSE], f"exactly one pause, of the documented length: {pauses}"
    assert RETRIED_NOTE in failed, f"the ledger cannot tell a retry happened: {failed}"
    assert "Resolving timed out" in failed, f"the raw failure must survive the note: {failed}"
    # The note is the bot's words in front of the extractor's, so it sits where it
    # could break the classification of the very failure it describes. It must not.
    assert failure_reply(failed) != FAILURE_REPLY, f"the note came between the detail and its row: {failed}"
    # Whoever is watching the window learns it too, and learns it while the link can
    # still be saved -- the ledger record only exists once both attempts are spent.
    assert len(logged) == 1 and reel in logged[0], logged

    # 2. The blip clears. This is the whole point of the feature: the group gets its
    #    video and never learns anything happened.
    delivered, asked, pauses, _logged = _run([blip(), {}], reel)
    assert isinstance(delivered, Media), f"the second attempt has to be able to succeed: {delivered}"
    assert asked == ["video", "video"], asked
    assert pauses == [TRANSPORT_RETRY_PAUSE], pauses

    # 3. The site answered. One attempt, no pause, and the image fallback still gets
    #    its turn -- on Instagram that branch is the difference between a photo post
    #    being delivered and being apologised for.
    answered, asked, pauses, logged = _run([_download_error(EMPTY_MEDIA_ERROR, None)], reel)
    assert isinstance(answered, str), answered
    assert asked == ["video", "probe"], f"an answer must not be retried, and must still fall back: {asked}"
    assert pauses == [], f"nothing waited: {pauses}"
    assert logged == [], f"nothing was retried, so nothing may say so: {logged}"
    assert RETRIED_NOTE not in answered, f"nothing was retried, so nothing may say so: {answered}"

    # 4. And the same for a failure the site raised as its own exception, which is
    #    the shape every named row in FAILURE_SIGNATURES arrives in.
    answered, asked, pauses, _logged = _run(
        [_download_error(EMPTY_MEDIA_ERROR, yt_dlp.utils.ExtractorError("empty media response"))], reel
    )
    assert asked == ["video", "probe"], asked
    assert pauses == [], pauses

    # 5. Off Instagram the fallback declines on the host, so a transport failure and
    #    an answer differ only in the retry -- which is the cheapest place to see
    #    that the retry is keyed on the exception and not on the site.
    youtube = "https://youtu.be/never-fetched"
    _failed, asked, _pauses, _logged = _run([blip(), blip()], youtube)
    assert asked == ["video", "video"], asked
    _answered, asked, _pauses, _logged = _run(
        [_download_error("ERROR: [youtube] x: Video unavailable", None)], youtube
    )
    assert asked == ["video"], f"no probe off Instagram, and no retry for an answer: {asked}"

    print("ok  a transport failure costs one retry; an answer from the site costs none")


# The two info dicts this feature turns on, both measured with --simulate on
# 2026-08-10. The finished stream is the one that must still be delivered, and it is
# the reason `was_live` sits in both dicts: a guard that reads it passes the live case
# and fails here, which is exactly what the assertions below are for.
LIVE_STREAM_INFO = {
    "id": "X4VbdwhkE10", "is_live": True, "live_status": "is_live",
    "was_live": False, "duration": None, "formats": [{"format_id": "95"}],
}
FINISHED_STREAM_INFO = {
    "id": "zo5oewEQbsE", "is_live": False, "live_status": "was_live",
    "was_live": True, "duration": 1146, "formats": [{"format_id": "136"}],
}


@contextmanager
def _match_filter_stub(workspace: Path, info: dict) -> Iterator[list[str]]:
    """yt-dlp replaced by a stand-in that HONOURS match_filter the way the real one does.

    Mirrors YoutubeDL.process_video_result at yt-dlp 2026.7.4: the filter is consulted
    (line 3042) after `formats` is populated, and a non-None answer makes it return the
    info dict immediately (line 3043) -- no format selection, no download, no file. The
    `incomplete` keyword is passed the way the real caller passes it, truthy, because
    that is the argument shape a filter can silently fail open on.

    Yields the list of what yt-dlp was asked to do, so the check can assert the CALL and
    not only the outcome: "no bytes were written" is this feature's entire claim, and an
    outcome-only assert would pass on a bot that downloaded the stream and then errored.
    """
    asked: list[str] = []

    class _FakeYdl:
        def __init__(self, options: dict) -> None:
            self.options = options

        def __enter__(self) -> "_FakeYdl":
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def extract_info(self, _url: str, download: bool = False) -> dict:
            match_filter = self.options.get("match_filter")
            reason = match_filter(info, incomplete={"id", "title"}) if match_filter else None
            if reason is not None:
                asked.append(f"filtered({reason})")
                return info
            asked.append("downloaded")
            (workspace / f"{info['id']}.mp4").write_bytes(b"\x00" * 4096)
            return info

    class _FakeYtDlp:
        YoutubeDL = _FakeYdl
        utils = yt_dlp.utils

    real = globals()["yt_dlp"]
    globals()["yt_dlp"] = _FakeYtDlp
    try:
        yield asked
    finally:
        globals()["yt_dlp"] = real


def _check_live_streams_are_refused() -> None:
    """A live stream is refused before a byte is written; a finished one is delivered.

    The defect this covers is not a wrong answer, it is an unbounded one: measured at
    ~375 MB/h and never returning, which also means temp_workspace never cleans up and
    -- delivery being serial -- the bot never handles another message (README.md §4.13).
    """
    # 1. The filter is actually installed. A guard that is not wired is not a guard,
    #    and this is the assert that dies if `match_filter` is ever dropped from the
    #    options dict while every other assertion here keeps passing on the callable.
    with tempfile.TemporaryDirectory() as tmp:
        options = _ydl_options(Path(tmp))
    assert options.get("match_filter") is _refuse_live_stream, (
        "the live-stream filter must be wired into the options yt-dlp actually gets"
    )

    # 2. The signature yt-dlp calls it with. `_match_entry` catches TypeError and
    #    retries positionally, returning None -- DOWNLOAD IT -- when `incomplete` is
    #    truthy, so a filter that cannot take this keyword fails open and in silence.
    assert _refuse_live_stream(LIVE_STREAM_INFO, incomplete=True) is not None
    assert _refuse_live_stream(LIVE_STREAM_INFO, incomplete=False) is not None
    assert _refuse_live_stream(FINISHED_STREAM_INFO, incomplete=True) is None
    # 3. Never the sentinel that makes yt-dlp block on input() with nobody there.
    for info in (LIVE_STREAM_INFO, FINISHED_STREAM_INFO, {}, {"live_status": "is_upcoming"}):
        assert _refuse_live_stream(info, incomplete=False) is not yt_dlp.utils.NO_DEFAULT, (
            "NO_DEFAULT makes yt-dlp prompt on input() and the bot would hang forever"
        )

    # 4. The live stream: refused, with its own exception, and NOTHING DOWNLOADED.
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        with _match_filter_stub(workspace, LIVE_STREAM_INFO) as asked:
            try:
                download_into("https://www.youtube.com/watch?v=X4VbdwhkE10", workspace)
            except LiveStreamError as exc:
                refusal = str(exc)
            else:
                raise AssertionError("a live stream must not be delivered")
        assert asked == [f"filtered({LIVE_STREAM_SKIP_REASON})"], asked
        assert list(workspace.iterdir()) == [], "a refused live stream must leave no file"
    # The distinct class is what the ledger and the reply both key on.
    assert isinstance(LiveStreamError(""), ExtractionError), "_deliver must still catch it"
    assert type(LiveStreamError("")).__name__ == "LiveStreamError", "the ledger's error class"
    assert "X4VbdwhkE10" in refusal, refusal
    # The generic no-file message would be a lie about a deliberate refusal, and it is
    # what fires if the raise is ever moved after the `path is None` check.
    assert "left no file" not in refusal, refusal

    # 5. The FINISHED stream: an ordinary bounded video, downloaded and delivered. This
    #    is the assertion a guard keyed on `was_live` dies on, and the only one that
    #    could ever catch it -- the live case passes either way.
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        with _match_filter_stub(workspace, FINISHED_STREAM_INFO) as asked:
            media = download_into("https://www.youtube.com/watch?v=zo5oewEQbsE", workspace)
        assert asked == ["downloaded"], f"a finished stream is an ordinary video: {asked}"
        assert media.path.is_file() and media.path.stat().st_size > 0, media.path
        assert media.duration == 1146, media.duration

    print("ok  a live stream is refused before a byte; a finished one is still delivered")


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
    # And no markup, for every reply but the one that asked for it. The apology, the
    # oversize line and the insult answer are unescaped Spanish -- one of them
    # carrying a raw URL -- so a parse mode leaking onto this default would either
    # eat characters or fail the send.
    assert text.kwargs.get("parse_mode") is None, text.kwargs

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


def _check_the_log_never_shows_the_token() -> None:
    """The window the bot prints must never contain the token -- and must still contain
    everything else.

    Both halves are the check. Asserting only the first passes if the fix silenced the
    whole process, which would cost a friend the one diagnostic he has and hide the
    conflict lines that explain a bot that stopped.

    Driven through a fake transport: a real httpx request object, the real logging call
    inside `Client.send`, and no network, no token and no second poller anywhere near
    it. Driving httpx rather than asserting a level number is what pins REQUEST_URL_LOGGER
    to whatever actually prints the URL on the installed version; a level assertion would
    still pass if the request log moved to a name nothing here silences.
    """
    fake_token = "123456789:AAFakeTokenNeverIssuedByTelegramOnlyForThisCheck"
    own_line = "polling; the line the window has to keep"
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Collect(logging.INFO)
    root, urls = logging.getLogger(), logging.getLogger(REQUEST_URL_LOGGER)
    previous_root, previous_urls = root.level, urls.level
    root.addHandler(handler)
    try:
        configure_logging()
        with httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200))
        ) as client:
            client.get(f"https://api.telegram.org/bot{fake_token}/getMe")
        log.info(own_line)
    finally:
        # The rest of the self-check runs at WARNING on purpose (see _capture_log), so
        # both levels this touched go back exactly as they were.
        root.removeHandler(handler)
        root.setLevel(previous_root)
        urls.setLevel(previous_urls)

    leaked = [line for line in records if fake_token in line]
    assert not leaked, (
        f"a request URL reached the log with the token in it: {leaked}. Either "
        f"{REQUEST_URL_LOGGER} is no longer the logger that prints request URLs, or "
        f"configure_logging stopped raising its level"
    )
    assert own_line in records, (
        "the bot's own INFO lines no longer reach the log: the fix silenced the window "
        "a friend reads instead of the one logger that prints the token"
    )
    print(
        f"ok  the log keeps the bot's own lines and never a request URL "
        f"({REQUEST_URL_LOGGER} raised above INFO, {len(records)} line(s) still got through)"
    )


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
        # invented TikTok links in the owner's real rejected.jsonl. BOTH files, not
        # just that one: on_message can write to either now, and nothing here should
        # depend on the texts below never being read as an insult.
        real_rejected, real_insults = globals()["REJECTED_LEDGER"], globals()["INSULT_LEDGER"]
        # The TikTok below is now answered in the chat, and this Message has no bot
        # behind it to answer with. The reply itself is asserted where it belongs,
        # in _check_unsupported_media_hosts_get_an_answer; here it is only silenced.
        real_reply = globals()["_reply_text"]

        async def _swallow(_message: object, _text: str, **_kwargs: object) -> None:
            return None

        globals()["_reply_text"] = _swallow
        with temp_workspace() as workspace:
            globals()["REJECTED_LEDGER"] = workspace / "rejected.jsonl"
            globals()["INSULT_LEDGER"] = workspace / "insults.jsonl"
            try:
                with _capture_log(logging.INFO) as messages:
                    asyncio.run(on_message(telegram.Update(update_id=1, message=message), None))
            finally:
                globals()["REJECTED_LEDGER"] = real_rejected
                globals()["INSULT_LEDGER"] = real_insults
                globals()["_reply_text"] = real_reply
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

        async def _swallow(_message: object, _text: str, **_kwargs: object) -> None:
            return None

        real_deliver, real_ledger = globals()["_deliver"], globals()["REJECTED_LEDGER"]
        real_insults, real_reply = globals()["INSULT_LEDGER"], globals()["_reply_text"]
        globals()["_deliver"] = _fake_deliver
        # An unsupported MEDIA host now gets a line in the chat, and a telegram.Message
        # with no bot behind it cannot send one. Only silenced here -- what is said,
        # and to which hosts, is asserted in the check below this one.
        globals()["_reply_text"] = _swallow
        with temp_workspace() as workspace:
            ledger = workspace / "rejected.jsonl"
            globals()["REJECTED_LEDGER"] = ledger
            # on_message reaches the insult half too, and nothing here should depend
            # on these texts never being read as one.
            globals()["INSULT_LEDGER"] = workspace / "insults.jsonl"
            try:
                with _capture_log(logging.INFO) as lines:
                    asyncio.run(on_message(telegram.Update(update_id=1, message=message), None))
                raw = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""
                return read_rejections(ledger), delivered, lines, raw
            finally:
                globals()["_deliver"] = real_deliver
                globals()["REJECTED_LEDGER"] = real_ledger
                globals()["INSULT_LEDGER"] = real_insults
                globals()["_reply_text"] = real_reply

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


def _check_unsupported_media_hosts_get_an_answer() -> None:
    """A TikTok is told why nothing came back; a news article is still left alone.

    THE SILENT HALF IS THE ONE THAT NEEDS ASSERTS. The decision this narrows --
    "anything unsupported is left alone rather than attempted and apologised for" --
    is still right for every URL that is not a video by construction, and a list that
    quietly grows until it catches everything would undo it without anybody noticing.
    So both halves are driven here, through the real on_message, with _deliver, both
    ledgers and _reply_text swapped out. No network, no token.

    The ledger is asserted alongside every case for the same reason: the record and
    the reply have different audiences, and a change that ties one to the other --
    "only record what we answered", or the reverse -- destroys the report that
    decides which site gets supported next.
    """

    def _run(text: str) -> tuple[list[str], list[dict], list[str]]:
        message = telegram.Message(
            message_id=13,
            date=dt.datetime.fromtimestamp(0, dt.timezone.utc),
            chat=telegram.Chat(id=-100123, type=telegram.Chat.GROUP),
            from_user=telegram.User(id=1, first_name="u", is_bot=False),
            text=text,
        )
        said: list[str] = []
        delivered: list[str] = []

        async def _fake_reply(_message: object, reply: str, **_kwargs: object) -> None:
            said.append(reply)

        async def _fake_deliver(_message: object, url: str) -> None:
            delivered.append(url)

        real = {name: globals()[name] for name in
                ("_reply_text", "_deliver", "REJECTED_LEDGER", "INSULT_LEDGER")}
        globals()["_reply_text"], globals()["_deliver"] = _fake_reply, _fake_deliver
        with temp_workspace() as workspace:
            globals()["REJECTED_LEDGER"] = workspace / "rejected.jsonl"
            globals()["INSULT_LEDGER"] = workspace / "insults.jsonl"
            try:
                with _capture_log(logging.INFO):
                    asyncio.run(on_message(telegram.Update(update_id=1, message=message), None))
                return said, read_rejections(globals()["REJECTED_LEDGER"]), delivered
            finally:
                globals().update(real)

    # The case the owner named: a video site the bot does not do. One line, and the
    # record too, because the two answer different questions.
    said, records, delivered = _run("miren esto https://www.tiktok.com/@a/video/1")
    assert said == [UNSUPPORTED_MEDIA_REPLY], f"a TikTok cannot look like a dead bot: {said}"
    assert [r["url"] for r in records] == ["https://www.tiktok.com/@a/video/1"], records
    assert delivered == [], delivered

    # And the case the silence rule was written for. Still silent, still recorded.
    for quiet in (
        "https://www.lanacion.com.ar/algo/una-nota/",
        "https://open.spotify.com/track/abc",
        "https://docs.google.com/document/d/abc/edit",
        "https://x.com/a/status/2",
    ):
        said, records, _delivered = _run(f"che miren {quiet}")
        assert said == [], f"{quiet} is not a request for a video: {said}"
        assert [r["url"] for r in records] == [quiet], records

    # Three of them in one message is one question, so it gets one answer -- and
    # three records, because which site recurs is the entire point of the ledger.
    said, records, _delivered = _run(
        "https://www.tiktok.com/@a/video/1 https://vm.tiktok.com/ZM2/ https://vimeo.com/3"
    )
    assert said == [UNSUPPORTED_MEDIA_REPLY], f"one reply per message, not per link: {said}"
    assert len(records) == 3, records
    # vm.tiktok.com is the short form TikTok's own share sheet produces, and it is a
    # subdomain -- the same host rule the rest of the file uses has to cover it.
    assert "https://vm.tiktok.com/ZM2/" in [r["url"] for r in records], records

    # A mix of both kinds: one reply, and every URL still recorded.
    said, records, _delivered = _run(
        "https://www.lanacion.com.ar/nota/ y https://www.tiktok.com/@a/video/1"
    )
    assert said == [UNSUPPORTED_MEDIA_REPLY], said
    assert len(records) == 2, records

    # A message that also carried a supported link is not unattempted and is not
    # answered: something WAS tried, and whatever it produced is the answer.
    said, records, delivered = _run("https://www.tiktok.com/@a/video/1 y https://youtu.be/abc")
    assert said == [] and records == [], (said, records)
    assert delivered == ["https://youtu.be/abc"], delivered

    # Ordinary chat is untouched by all of it.
    said, records, delivered = _run("che alguien vio el partido ayer")
    assert (said, records, delivered) == ([], [], []), (said, records, delivered)

    # The list itself. A host in both frozensets would be dead configuration -- the
    # supported one wins before this is ever consulted -- and it is the way this
    # feature rots: somebody adds a site to SUPPORTED_HOSTS and leaves it here.
    assert not (MEDIA_PLATFORM_HOSTS & SUPPORTED_HOSTS), \
        f"a supported host can never reach this reply: {MEDIA_PLATFORM_HOSTS & SUPPORTED_HOSTS}"
    assert MEDIA_PLATFORM_HOSTS, "an empty list makes the reply unreachable"
    assert len(MEDIA_PLATFORM_HOSTS) <= 10, \
        "this list answers; it does not become a chatbot. Grow it from the ledger, not from ideas"

    # And the sentence a friend reads: Spanish, one short line, no jargon, and it
    # does not promise a fix or list what does work.
    assert UNSUPPORTED_MEDIA_REPLY == UNSUPPORTED_MEDIA_REPLY.lower(), UNSUPPORTED_MEDIA_REPLY
    assert len(UNSUPPORTED_MEDIA_REPLY) <= 80 and "\n" not in UNSUPPORTED_MEDIA_REPLY
    for jargon in ("http", "url", "host", "yt-dlp", "error", "tiktok"):
        assert jargon not in UNSUPPORTED_MEDIA_REPLY, UNSUPPORTED_MEDIA_REPLY
    assert UNSUPPORTED_MEDIA_REPLY != FAILURE_REPLY, \
        "nothing was attempted here, so this is not the apology for a bounce"
    print("ok  an unsupported video site gets one line; everything else still gets silence")


# Everything the bot must answer, and everything it must stay quiet through. THE
# SECOND LIST IS THE FEATURE. This runs on every message a group of friends sends
# all day, and a bot that apologises when nobody insulted it is worse than one that
# misses a typo -- so the near-misses are not decoration: each of these killed a
# candidate rule while the matcher was being tuned.
#
#   "esa bota estupida" / "el boton estupido"  -> killed a plain ratio on "bot",
#       which scores 0.857 on "bota" and 0.750 on "boton"; hence the length rule.
#   "abri el bot, estudio despues"             -> killed a 0.80 threshold on
#       "estupido", which is exactly what "estudio" scores against it.
#   "el bot funciona, no seas estupido vos"    -> killed matching the two words
#       anywhere in the message; hence INSULT_MAX_GAP.
#   "sos un estupido" / "gracias bot"          -> killed either word on its own.
#   "bro que estupido"                         -> killed the thresholds ALONE:
#       "bro" scores 0.667 against "bot", exactly what "vot" scores, so no number
#       accepts the typo the owner named and refuses the loanword. Hence
#       NOT_THE_BOT. This one was found by review after the corpus was written,
#       which is the honest state of the list: it is as good as the imagination
#       that produced it.
INSULTS_THAT_MUST_FIRE = (
    "bot estupido",
    "bot estúpido",
    "estupido bot",
    "estúpido bot",
    "BOT ESTUPIDO",
    "Bot, estúpido!",
    "vot estupido",            # the owner named this one
    "estupido vot",
    "bot estupdo",             # and these: letras faltantes
    "bot estpido",
    "bot etupido",
    "bo estupido",
    "bot estupidoooo",         # chat elongation
    "boooot estupido",
    "BOT ESTUPIDOOO!!!",
    "sos un bot estupido",
    "che bot estupido, bajame el video",
    "bot re estupido",         # one word between, which is how this group talks
    "bot es estupido",
    "que bot mas estupido",
    "estupido el bot",
    "bot estupida",
    "anda a cagar bot estupido",
    "\U0001f916 bot estupido",
    "bot estupido https://youtu.be/abc",
)

ORDINARY_CHAT_THAT_MUST_NOT_FIRE = (
    "che alguien vio el partido ayer",
    "jajaja que grande",
    "sos un estupido",                              # a person, not the bot
    "que estupido que sos",
    "no seas estupido",
    "vos sos estupido",
    "estupido",                                     # the word alone
    "estúpido",
    "bot",                                          # and the other word alone
    "el bot anda joya",
    "gracias bot",
    "el bot no anda",
    "dale bot, mandame el video",
    "buenisimo el bot, gracias",
    "mandame el link del bot que te pase",
    "que estupidez",
    "una estupidez de partido",
    "no entiendo nada, esto es una estupidez total",
    "esa bota estupida",                            # 0.857 against "bot"
    "el boton estupido de la app",                  # 0.750 against "bot"
    "la bota nueva",
    "mira esta foto estupida",
    "que voto estupido",
    "esa moto estupida",
    "el robot de la fabrica",
    "bro que estupido",                             # "bro" is 0.667, same as "vot"
    "que estupido bro",
    "jaja bro, que estupida esa peli",
    "esa bio estupida que tiene",
    "abri el bot, estudio despues",                 # 0.800 against "estupido"
    "me voy a estudiar",
    "mira que boludo estupido",                     # insulting a friend, not the bot
    "ese tipo es un estupido barbaro",
    "que estupido este partido",
    "que estupido, o no",                           # "o" is 0.500 against "bot"
    "estupido el arbitro",
    "no puedo creer lo estupido que soy",
    "un dia estupido, no puedo mas",
    "estupida app",
    "el bot funciona, no seas estupido vos",        # both words, four apart
    "https://www.instagram.com/p/DbvWPFQxPkI/",
)


def _check_insult_detection() -> None:
    """The bot answers being called stupid, records it, and stays quiet otherwise.

    The two corpora above are the check; everything below them is the wiring -- the
    exact sentence, the record, the privacy rule, and the one interaction that
    matters: a message carrying both an insult and a link still delivers the link.

    No network and no token. `_deliver` and both files are swapped out, because a
    self-check that forgets leaves invented records in the owner's real files.
    """
    for text in INSULTS_THAT_MUST_FIRE:
        assert insult_words(text) is not None, f"must be read as an insult: {text!r}"
    for text in ORDINARY_CHAT_THAT_MUST_NOT_FIRE:
        assert insult_words(text) is None, (
            f"ordinary chat must not be answered: {text!r} matched {insult_words(text)}"
        )
    assert insult_words("") is None and insult_words(None) is None
    assert insult_words("\U0001f602\U0001f602\U0001f602") is None, "emoji are not words"

    # Every word in NOT_THE_BOT has to be one the matcher would otherwise accept --
    # a dead entry is a claim about the thresholds that stopped being true, and it
    # would go unnoticed exactly like an unreachable FAILURE_SIGNATURES row.
    for word in NOT_THE_BOT:
        assert insult_words(f"{word} estupido") is None, f"NOT_THE_BOT failed to stop {word!r}"
        assert difflib.SequenceMatcher(None, word, "bot").ratio() >= INSULT_WORDS[0][1], (
            f"{word!r} does not clear the threshold anyway; the entry is dead"
        )
    assert "bot" not in NOT_THE_BOT, "the bot is the bot"
    # The matched words are what gets recorded, so their shape is part of the contract.
    assert insult_words("che VOT estúpido") == ("vot", "estupido"), insult_words("che VOT estúpido")

    # The owner asked for this sentence, exactly. Capital and comma included.
    assert INSULT_REPLY == "Lo lamento, hago lo que puedo", INSULT_REPLY

    class RecordingMessage:
        chat_id = -100123
        message_id = 42

        def __init__(self, text: str | None = None, caption: str | None = None) -> None:
            self.text = text
            self.caption = caption
            self.said: list[str] = []

        async def reply_text(self, text: str, **_kwargs: object) -> None:
            self.said.append(text)

    async def _both_halves(message: RecordingMessage) -> None:
        # A RecordingMessage is not a telegram.Message, so the two halves are called
        # the way on_message calls them; that the DISPATCHER still calls both, in
        # this order, is asserted separately at the end on a real Update.
        await _handle_links(message)
        await _handle_insult(message)

    def _run(message: RecordingMessage) -> tuple[list[dict], list[dict], list[str], str]:
        """on_message's two halves with _deliver and BOTH files swapped out."""
        events: list[str] = []

        async def _fake_deliver(_message: object, url: str) -> None:
            events.append(f"deliver {url}")

        real_deliver = globals()["_deliver"]
        real_rejected, real_insults = globals()["REJECTED_LEDGER"], globals()["INSULT_LEDGER"]
        globals()["_deliver"] = _fake_deliver
        with temp_workspace() as workspace:
            insult_file = workspace / "insults.jsonl"
            globals()["REJECTED_LEDGER"] = workspace / "rejected.jsonl"
            globals()["INSULT_LEDGER"] = insult_file
            try:
                with _capture_log(logging.INFO) as lines:
                    asyncio.run(_both_halves(message))
                events.extend(f"said {said}" for said in message.said)
                # read_rejections is a JSON-lines reader that happens to be named for
                # its first caller; the two files have the same one-object-per-line
                # shape on purpose.
                insults = read_rejections(insult_file)
                rejected = read_rejections(globals()["REJECTED_LEDGER"])
                raw = insult_file.read_text(encoding="utf-8") if insult_file.is_file() else ""
                return insults, rejected, events, raw + "\n".join(lines)
            finally:
                globals()["_deliver"] = real_deliver
                globals()["REJECTED_LEDGER"] = real_rejected
                globals()["INSULT_LEDGER"] = real_insults

    # An insult on its own: the sentence, once, and one record.
    insulted = RecordingMessage(text="SECRETO-DEL-GRUPO bot estupido, no bajas nada")
    insults, rejected, _events, trace = _run(insulted)
    assert insulted.said == [INSULT_REPLY], insulted.said
    assert len(insults) == 1, insults
    assert insults[0]["words"] == ["bot", "estupido"], insults
    assert insults[0]["chat_id"] == -100123 and insults[0]["message_id"] == 42, insults
    assert insults[0]["when"], insults
    assert rejected == [], "an insult is not a bounced link and never touches that file"
    # Neither the file nor the log may carry the message. The two matched words are
    # not the body: they are near-copies of "bot" and "estupido" by construction.
    assert "SECRETO-DEL-GRUPO" not in trace and "no bajas nada" not in trace, trace
    # And the link half still says its line: "nothing to do" is about links, and a
    # message that is only an insult really does have none.
    assert "no URL recognised" in trace and "the bot was called" in trace, trace

    # Ordinary chat: nothing said, nothing written, both files untouched.
    quiet = RecordingMessage(text="che alguien vio el partido ayer")
    insults, rejected, _events, _trace = _run(quiet)
    assert quiet.said == [] and insults == [] and rejected == [], (quiet.said, insults, rejected)

    # THE INTERACTION THAT MATTERS: both in one message. The link is delivered, the
    # insult is answered, and the link goes FIRST -- _deliver cannot raise, an
    # ordinary reply can, and the download is the part somebody actually asked for.
    both = RecordingMessage(text="bot estupido, bajame https://youtu.be/abc")
    insults, rejected, events, _trace = _run(both)
    assert events == ["deliver https://youtu.be/abc", f"said {INSULT_REPLY}"], events
    assert len(insults) == 1 and rejected == [], (insults, rejected)

    # An unsupported link plus an insult: the ledger keeps recording the skip, and
    # the two files stay in their own lanes.
    mixed = RecordingMessage(text="bot estupido https://www.tiktok.com/@a/video/1")
    insults, rejected, _events, _trace = _run(mixed)
    assert len(insults) == 1 and len(rejected) == 1, (insults, rejected)
    assert rejected[0]["error"] == UNSUPPORTED_ERROR and "words" not in rejected[0], rejected

    # A caption is the other half of MESSAGE_FILTER: a photo captioned "bot estupido"
    # is the same message.
    captioned = RecordingMessage(caption="bot estupido")
    insults, _rejected, _events, _trace = _run(captioned)
    assert captioned.said == [INSULT_REPLY] and len(insults) == 1, (captioned.said, insults)

    # A link with no insult writes nothing to the insult file.
    linked = RecordingMessage(text="miren https://youtu.be/abc")
    insults, _rejected, events, _trace = _run(linked)
    assert insults == [] and events == ["deliver https://youtu.be/abc"], (insults, events)

    # record_insult may never raise: it is diagnostics bolted onto a reply, exactly
    # like the ledger, and a full disk may not cost the group its answer.
    with temp_workspace() as workspace:
        unwritable = workspace / "a-directory"
        unwritable.mkdir()
        with _capture_log(logging.ERROR) as complaints:
            record_insult(RecordingMessage(text="bot estupido"), ("bot", "estupido"), unwritable)
        assert complaints, "a failed insult write must be swallowed AND logged"

    # Finally the dispatcher itself: both halves reached, links first, and a message
    # that is not there at all is still nothing to do.
    real = telegram.Message(
        message_id=9,
        date=dt.datetime.fromtimestamp(0, dt.timezone.utc),
        chat=telegram.Chat(id=-100, type=telegram.Chat.GROUP),
        from_user=telegram.User(id=1, first_name="u", is_bot=False),
        text="bot estupido https://www.tiktok.com/@a/video/1",
    )
    reached: list[str] = []

    async def _note_links(_message: object) -> None:
        reached.append("links")

    async def _note_insult(_message: object) -> None:
        reached.append("insult")

    real_links, real_insult = globals()["_handle_links"], globals()["_handle_insult"]
    globals()["_handle_links"], globals()["_handle_insult"] = _note_links, _note_insult
    try:
        asyncio.run(on_message(telegram.Update(update_id=1, message=real), None))
        asyncio.run(on_message(telegram.Update(update_id=2), None))
    finally:
        globals()["_handle_links"], globals()["_handle_insult"] = real_links, real_insult
    assert reached == ["links", "insult"], reached
    print(f"ok  the bot answers {len(INSULTS_THAT_MUST_FIRE)} insults and stays quiet through "
          f"{len(ORDINARY_CHAT_THAT_MUST_NOT_FIRE)} ordinary messages")


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


# Two fakes, both shaped like a real Telegram token, so a leak of either is caught by
# the shape rule as well as by name. They differ in every character after the colon:
# that is what makes "the reply is the same under both" mean "it does not read one".
FAKE_TOKENS = (
    "123456789:AAFfakeTOKENoneDoNotLeakThis000000000",
    "987654321:BBZfakeTOKENtwoDoNotLeakThat111111111",
)

# What a Telegram bot token looks like. Rejects a leak of ANY token, not only of the
# two fakes: an edit interpolating the real read_token() would pass a "the fake is
# absent" assert and be caught by this one.
TOKEN_SHAPE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")

# The only markup the reply may contain. Anything else means an unescaped character
# reached the text, which Telegram answers with a 400 the group never sees.
ALLOWED_HTML = re.compile(r"</?(?:b|pre)>")


class _InstallMessage:
    """A message that records what was replied to it, and its own Update.

    `effective_message` is itself, which is all on_install reads of the Update.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.effective_message = self
        self.sent: str | None = None
        self.kwargs: dict = {}

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.sent = text
        self.kwargs = kwargs


def _install_texts() -> dict[str, str]:
    """Every string this feature can put in a chat, keyed by how it got there.

    Builder and handler both, because they are two different chances to leak: the
    builder could interpolate a secret and the handler could append one. The group
    form as well as the DM form, since in a group Telegram glues the bot's username
    onto the command and that is the only difference between the two.
    """
    texts = {f"install_reply({platform!r})": install_reply(platform) for platform in (None, "mac", "windows")}
    for label, typed in (
        ("DM, bare", f"/{INSTALL_COMMAND}"),
        ("DM, mac", f"/{INSTALL_COMMAND} mac"),
        ("group, bare", f"/{INSTALL_COMMAND}@pibes_laburantes_bot"),
        ("group, windows", f"/{INSTALL_COMMAND}@pibes_laburantes_bot windows"),
    ):
        message = _InstallMessage(typed)
        asyncio.run(on_install(message, None))
        assert message.sent is not None, f"{label}: the bot answered nothing"
        assert message.kwargs.get("parse_mode") == telegram.constants.ParseMode.HTML, (
            f"{label}: without HTML the code block is not a code block and nothing "
            f"offers tap-to-copy: {message.kwargs}"
        )
        texts[f"on_install({label})"] = message.sent
    return texts


def _check_install_instructions() -> None:
    """The bot hands out its own installer, and can never hand out the token.

    Two things are proved here and the second one is the load-bearing one. What the
    reply says, and how little of it: a pasteable block per platform, the two
    obstacles a friend cannot clear alone -- git, and that the token comes from the
    owner -- and nothing else. The line budget is asserted, because the defect this
    replaced was a 22-line wall in which the token line was what got skimmed past.

    What it can never say is the token, and the guard for that is **invariance, not a
    filter**: a filter only catches the value it was told to look for, so instead
    every string this feature can produce is built under two different fake tokens and
    once under none, and all three runs must be byte-identical. A text that does not
    change when the token changes cannot contain it. Do not "improve" this into a
    redaction pass -- that would let a transformed leak through. README.md 2.2.

    No network. The origin pin below shells out to git and is skipped where it cannot.
    """
    # --- what "/instalar algo" means ---------------------------------------------
    # Bare is the common case rather than the fallback (a tap on the menu sends it),
    # and an unrecognised word means both rather than an error.
    for said in ("", "@pibes_laburantes_bot", " linux", " asdfgh", " ?", " no se"):
        assert install_platform(f"/{INSTALL_COMMAND}{said}") is None, said
    assert install_platform(None) is None, "a message with no text is still not an error"
    for said in ("mac", "MAC", "macos", "macOS", "para mac", "en mi macbook", "osx"):
        assert install_platform(f"/{INSTALL_COMMAND} {said}") == "mac", said
    for said in ("windows", "Windows", "win", "pc", "en windows", "windows 11"):
        assert install_platform(f"/{INSTALL_COMMAND} {said}") == "windows", said
    # The command word itself must never be read as a platform, or "/pc" style typos
    # in the command would pick a side at random.
    assert install_platform("/mac") is None, "the command word is not an argument"

    # --- what the reply carries, and what it costs to read -------------------------
    both = install_reply()
    mac = install_reply("mac")
    windows = install_reply("windows")
    assert both == install_reply(None), "the bare default must be the both-platforms reply"

    # The folder name is derived, so pin the derivation rather than letting the reply
    # agree with itself: `cd pibes-laburantes-bot.git` would satisfy "CLONE_DIR is in
    # the text" and fail on the friend's machine.
    assert not CLONE_DIR.endswith(".git"), CLONE_DIR
    assert CLONE_URL.endswith(f"/{CLONE_DIR}.git"), (CLONE_URL, CLONE_DIR)

    # The bootstrap's link is derived too, and from the same string, so the same rule
    # applies: pin the derivation instead of letting the reply agree with itself.
    assert BOOTSTRAP_URL.startswith("https://raw.githubusercontent.com/"), BOOTSTRAP_URL
    assert BOOTSTRAP_URL.endswith(f"/main/{BOOTSTRAP_FILE}"), BOOTSTRAP_URL
    assert f"/{CLONE_DIR}/" in BOOTSTRAP_URL, (
        f"the download link points somewhere other than {CLONE_DIR}: {BOOTSTRAP_URL}"
    )
    assert ".git/" not in BOOTSTRAP_URL, BOOTSTRAP_URL

    # The first obstacle, per platform, and they are no longer the same kind of thing.
    # macOS has to get past git -- a bare `"git" in text` would be satisfied by the
    # `git clone` inside its own command, so it is pinned on Apple's dialog instead.
    # Windows installs nothing now, so its obstacle is the machine asking whether to
    # run a file that came from the internet, and the reply is pinned on the words on
    # the buttons.
    obstacle = {
        "mac": "herramientas de línea de comandos",
        "windows": "Ejecutar de todas formas",
    }
    # What each platform is handed, and what it must never be handed instead. macOS
    # gets a command whose last act opens run-bot.command. Windows gets the bootstrap:
    # run-bot.cmd does run in a downloaded copy, it just never updates it, so naming
    # that file here would quietly freeze that friend at the version they first
    # downloaded -- which is the whole reason this order exists.
    handed = {"mac": f"./{LAUNCHER_FILE['mac']}", "windows": BOOTSTRAP_FILE}

    for label, text, lines, covers in (
        ("mac", mac, 3, ("mac",)),
        ("windows", windows, 3, ("windows",)),
        ("both", both, 5, ("mac", "windows")),
    ):
        # The budget, and it is the feature rather than a style note: the 22-line wall
        # this replaced was skimmed, and the token line was what got skimmed past. It
        # did not move when Windows changed mechanism -- two lines per platform, still.
        assert len(text.splitlines()) == lines, (
            f"{label}: {len(text.splitlines())} lines, not {lines} -- the wall is back"
        )
        for each in covers:
            assert obstacle[each] in text, f"{label}: {each} lost its own obstacle"
            assert handed[each] in text, f"{label}: {each} is not handed what it needs"
        for each in set(handed) - set(covers):
            assert handed[each] not in text, f"{label} must not hand out {each}'s half"
        # Never, on any platform: a downloaded copy that is told to open the launcher
        # directly stops updating, silently and for good.
        assert LAUNCHER_FILE["windows"] not in text, (
            f"{label}: run-bot.cmd is the file the bootstrap hands off to, never the "
            f"one a friend is sent to -- opening it directly skips every update"
        )
        if "mac" in covers:
            assert f"cd {CLONE_DIR} " in text, f"{label}: the friend has to cd into the folder git made"
            block = re.search(r"<pre>(.*?)</pre>", text, re.DOTALL)
            assert block and CLONE_URL in block.group(1), (
                f"{label}: the pasteable part must be inside the code block or nothing offers to copy it"
            )
        if covers == ("windows",):
            # The point of the whole change: Windows is not sent to install anything.
            assert "<pre>" not in text, (
                f"{label}: there is nothing to paste, and a URL inside a code block is not tappable"
            )
            assert CLONE_URL not in text and "git-scm.com" not in text, (
                f"{label}: the download exists so that this friend needs no git at all"
            )
        # The second obstacle, and the only one that is the same on both platforms.
        assert "token" in text and "dueño" in text, f"{label}: the token comes from the owner, separately"
        # Escaping, which is the trap that comes with introducing a parse mode. The
        # && in the pasted command is the character that bites: raw, Telegram may
        # swallow it or reject the message. Only macOS has one to escape now -- the
        # Windows reply has no shell command in it at all, which is the point of it.
        if "mac" in covers:
            assert "&amp;&amp;" in text, f"{label}: the shell && must be HTML-escaped"
        assert "&&" not in text, f"{label}: a raw && survived escaping"
        stripped = ALLOWED_HTML.sub("", text)
        assert "<" not in stripped and ">" not in stripped, (
            f"{label}: a stray angle bracket is an unsupported tag and a 400 from Telegram"
        )
        assert re.search(r"&(?!amp;|lt;|gt;|quot;|#\d+;)", stripped) is None, (
            f"{label}: a bare & is not a valid HTML entity"
        )
        # Not implied by the line budget: five very long lines would still 400.
        assert len(text) <= int(telegram.constants.MessageLimit.MAX_TEXT_LENGTH), len(text)

    # --- the token can never be in any of it --------------------------------------
    # The bait has to be shaped like the thing being hunted, or the shape rule below
    # is decoration: a fake that TOKEN_SHAPE does not match would let a real leak
    # through the one assert written to catch a token nobody planted.
    assert all(TOKEN_SHAPE.fullmatch(fake) for fake in FAKE_TOKENS), FAKE_TOKENS
    assert FAKE_TOKENS[0].split(":", 1)[1] != FAKE_TOKENS[1].split(":", 1)[1], (
        "the two fakes must differ, or 'the reply did not change' proves nothing"
    )
    previous = os.environ.get(TOKEN_ENV_VAR)
    try:
        runs = []
        for fake in FAKE_TOKENS:
            os.environ[TOKEN_ENV_VAR] = fake
            texts = _install_texts()
            for where, text in texts.items():
                assert fake not in text, f"{where} LEAKED THE TOKEN"
                assert fake.split(":", 1)[1] not in text, f"{where} leaked the secret half of the token"
                found = TOKEN_SHAPE.search(text)
                assert found is None, f"{where} contains something shaped like a token: {found}"
            runs.append(texts)
        os.environ.pop(TOKEN_ENV_VAR, None)
        runs.append(_install_texts())
    finally:
        if previous is None:
            os.environ.pop(TOKEN_ENV_VAR, None)
        else:
            os.environ[TOKEN_ENV_VAR] = previous

    first = runs[0]
    for other in runs[1:]:
        assert other == first, (
            "the install reply CHANGED when the token changed, so it is reading the "
            "environment: " + repr([k for k in first if first[k] != other.get(k)])
        )

    # --- the command exists, and Telegram is told it exists ------------------------
    wired = build_application(FAKE_TOKENS[0])
    handlers = [h for group in wired.handlers.values() for h in group]
    installed = [
        h for h in handlers
        if isinstance(h, CommandHandler) and INSTALL_COMMAND in h.commands
    ]
    assert len(installed) == 1, f"/{INSTALL_COMMAND} must be registered exactly once: {handlers}"
    assert installed[0].callback is on_install, installed[0].callback
    assert wired.post_init is _publish_commands, (
        "nobody discovers a command that is not in Telegram's menu"
    )

    class MenuBot:
        """The Application and its .bot at once -- both are all _publish_commands reads."""

        def __init__(self, blow_up: bool = False) -> None:
            self.bot = self
            self.published: list = []
            self.blow_up = blow_up

        async def set_my_commands(self, commands: list) -> None:
            if self.blow_up:
                raise telegram.error.TimedOut
            self.published = commands

    menu = MenuBot()
    asyncio.run(_publish_commands(menu))
    published = {command.command: command.description for command in menu.published}
    assert INSTALL_COMMAND in published, published
    assert published[INSTALL_COMMAND].strip(), "an empty description is refused by Telegram"
    assert "start" in published, "publishing a menu must not hide the command that already existed"

    # A menu that will not publish must not cost the group its bot: post_init raising
    # aborts run_polling, and the person at the window cannot read a traceback.
    with _capture_log(logging.WARNING) as warnings:
        asyncio.run(_publish_commands(MenuBot(blow_up=True)))
    assert len(warnings) == 1 and "menu" in warnings[0], warnings

    # --- the hardcoded URL cannot go stale silently --------------------------------
    def _slug(url: str) -> str:
        return "/".join(re.split(r"[/:]", url.strip().rstrip("/").removesuffix(".git"))[-2:]).casefold()

    # True of the constant on its own, so it is asserted whether or not git is here.
    assert CLONE_URL.startswith("https://"), (
        "an ssh clone URL is useless to a friend with no GitHub account"
    )
    origin = None
    if shutil.which("git"):
        probe = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=15,
        )
        origin = probe.stdout.strip() if probe.returncode == 0 else None
    if origin:
        assert _slug(origin) == _slug(CLONE_URL), (
            f"CLONE_URL points at {_slug(CLONE_URL)} but this checkout's origin is "
            f"{_slug(origin)}: the bot would send friends to the wrong repository"
        )
        pinned = f"pinned against origin {_slug(origin)}"
    else:
        pinned = "origin not readable here, URL not pinned"

    # --- the file the reply links to has to be the one in this repository -----------
    # The bootstrap is a fourth place the repository is named -- CLONE_URL, the reply's
    # derived link, EMPEZAR-ACA.md and the archive URL inside the .cmd -- and the reply
    # hands out a link to it, so a disagreement means a friend downloads a file that
    # fetches somebody else's repository.
    #
    # There is exactly one legitimate reason for the file to be missing, and the skip
    # has to be pinned to it rather than to the absence: a copy the bootstrap unpacked
    # does not contain the bootstrap, because it excludes itself from its own unpack,
    # and it is the `.tarball-install` stamp that says so. Skipping on absence alone
    # was a hole -- mutation testing caught it: renaming BOOTSTRAP_FILE without
    # renaming the file made this check quietly skip itself while the reply handed out
    # a link to a file that is not in the repository.
    # The one string the two Windows files have to agree on by name: the bootstrap
    # writes it, the launcher reads it to decide which of two sentences it is allowed to
    # say. A rename on one side turns the launcher back into a liar -- it would tell a
    # friend with a working updater that their copy cannot update itself.
    stamp = ".tarball-install"
    here = Path(__file__).resolve().parent
    bootstrap = here / BOOTSTRAP_FILE
    if not bootstrap.exists():
        assert (here / stamp).exists(), (
            f"{BOOTSTRAP_FILE} is not in this checkout, and no .tarball-install stamp "
            f"explains why: /instalar windows hands out a link to a file that does not "
            f"exist in the repository"
        )
    if bootstrap.exists():
        body = bootstrap.read_text(encoding="ascii")
        archive_url = (
            CLONE_URL.replace("https://github.com/", "https://codeload.github.com/", 1).removesuffix(".git")
            + "/tar.gz/refs/heads/main"
        )
        assert archive_url in body, (
            f"{BOOTSTRAP_FILE} does not fetch {archive_url}: the link the bot hands out "
            f"and the repository that file downloads have drifted apart"
        )
        # The exclude keeps the running .cmd from being overwritten by its own unpack,
        # so it has to name this file. A rename that forgets it would put a second .cmd
        # in the friend's folder and overwrite a batch file while cmd.exe reads it.
        assert f'--exclude "*/{BOOTSTRAP_FILE}"' in body, (
            f"{BOOTSTRAP_FILE} must exclude itself from its own unpack"
        )
        # Written, not merely mentioned. `stamp in body` was the first version of this
        # and mutation testing walked straight through it: the name also appears in the
        # `set` that defines it and in the comments, so deleting the redirection that
        # creates the file left the assert green. These pin the two halves separately.
        assert f'set "STAMP={stamp}"' in body, f"{BOOTSTRAP_FILE} must name the stamp {stamp}"
        assert '> "%TARGET%\\%STAMP%"' in body, (
            f"{BOOTSTRAP_FILE} must actually write the stamp into the folder: without it "
            f"{LAUNCHER_FILE['windows']} cannot tell that copy from a hand-unpacked zip"
        )

        launcher = here / LAUNCHER_FILE["windows"]
        if launcher.exists():
            # Read as ASCII on purpose, which pins the other rule those two files live
            # by: cmd.exe reads a .cmd in the console's OEM codepage, so an accent added
            # to either of them is mojibake in the window a friend is reading.
            drives = launcher.read_text(encoding="ascii")
            assert f'if exist "{stamp}"' in drives, (
                f"{LAUNCHER_FILE['windows']} does not read {stamp}, so it still tells a "
                f"downloaded copy that it cannot update itself -- which is false of one"
            )
            # The sentences the friend reads, and not just the bytes of the file: both
            # names appear in that file's comments, so a check written against the whole
            # text passes while the window says something else entirely.
            spoken = [line for line in drives.splitlines() if line.startswith("echo ")]
            assert any(BOOTSTRAP_FILE in line for line in spoken), (
                f"{LAUNCHER_FILE['windows']} has to say {BOOTSTRAP_FILE} out loud: telling "
                f"somebody their copy has an updater without naming the file helps nobody"
            )
            # And the case the old sentence was written for must survive intact. A zip
            # somebody unpacked by hand has no .git and no stamp, and for that copy the
            # sentence is true; making it accurate for one copy must not cost the other.
            assert any("no se puede actualizar sola" in line for line in spoken), (
                f"{LAUNCHER_FILE['windows']} lost the line for a copy that genuinely "
                f"cannot update itself"
            )
        linked = "the .cmd it links to fetches the same repo, and both agree on the stamp"
    else:
        linked = f"{BOOTSTRAP_FILE} absent here, its URL not pinned"

    print(f"ok  the bot hands out its own installer and never the token ({pinned}, {linked})")


def _self_check() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    _check_pure_helpers()
    _check_the_log_never_shows_the_token()
    _check_send_timeouts()
    _check_message_logging()
    _check_rejected_ledger()
    _check_failure_replies()
    _check_failed_extraction_keeps_its_error()
    _check_only_instagram_can_be_an_image_post()
    _check_transport_failures_are_retried()
    _check_live_streams_are_refused()
    _check_unattempted_links_are_recorded()
    _check_unsupported_media_hosts_get_an_answer()
    _check_insult_detection()
    _check_album_delivery()
    _check_failure_path()
    _check_take_over_intent()
    _check_conflict_handling()
    _check_startup_drops_the_backlog()
    _check_install_instructions()
    _check_extraction()
    print("\nself-check passed")


if __name__ == "__main__":
    if "--self-check" in sys.argv[1:]:
        _self_check()
    elif "--rejected" in sys.argv[1:]:
        print_rejections()
    else:
        main()
