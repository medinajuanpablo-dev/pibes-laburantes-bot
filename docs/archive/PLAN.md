# PROMPT-ORDER — Telegram meme bot, phase 1 (run on the Mac)

> Self-contained. Written 2026-08-07. Assume you know nothing about the conversation that produced
> this file. **Re-verify every factual claim below against the live environment before relying on
> it — the evidence here is discovery-grade and platform behaviour drifts weekly.**

## CONTEXT

A private Telegram bot for one group of friends. Someone pastes a YouTube / Instagram / Facebook
link in the group; the bot replies with the video file so nobody leaves the chat.

Nothing is shipped yet. This directory is empty except for this file. There is no in-flight
parallel work — you are the only track.

**Scale is the single most important thing to internalise: one group of friends, roughly 20 links
a week.** Every design decision below follows from that and from nothing else.

**Target for THIS order: the user's Mac** (macOS 15.1.1, arm64, Python 3.11.7, ffmpeg present at
`/opt/homebrew/bin/ffmpeg`, Docker installed). It will be ported to an old spare machine later —
which is a hard design constraint, see DESIGN LAWS — but do **not** build anything for that
machine now.

### Evidence already gathered (method stated so its blind spots are visible)

Run on 2026-08-07 from the user's residential IP with yt-dlp `2026.07.04`, using `--simulate`
(no downloads). This is one probe from one IP on one day — not a survey:

| Command run | Result |
|---|---|
| `yt-dlp --simulate --print "%(height)sp %(filesize_approx)s" <youtube watch URL>` | `2160p 243768398` — no cookies, no PO token, no proxy, no bot check |
| same with `-f "b[filesize<45M]/b[height<=720]/b"` | `360p mp4 11832459` |
| `yt-dlp --simulate <instagram.com/nasa/>` | `ERROR: Unable to extract data` |
| `yt-dlp --simulate <facebook.com/watch/?v=...>` | `ERROR: Cannot parse data` (also with curl-cffi installed) |
| `yt-dlp --simulate <tiktok.com/@nasa/video/...>` | `ERROR: Your IP address is blocked from accessing this post` |

What this evidence does and does not establish:

- **YouTube works from this IP today.** It does not establish that it will keep working, nor that
  it works at higher volume.
- **The 360p result is not a YouTube limitation, it is a format-selection consequence:** the
  single-file (progressive) format tops out there. Anything above 360p requires merging separate
  video and audio streams, which requires ffmpeg. Verify this yourself before designing around it.
- **Instagram fails without cookies even for public content.** Only one URL shape was tried
  (a profile page). Reels and posts were not tested.
- **Facebook failed, but the cause was NOT isolated** — the test URL may simply be dead. Do not
  conclude the extractor is broken without testing a URL you know is live.
- **TikTok was not requested by the user.** It is in the table only because it was proposed and
  the evidence killed it. Do not add TikTok support in this order.

## WHY IT MATTERS

The application is trivial and will be correct on day one. What decides whether this bot still
works in six months is operational: yt-dlp breaks when platforms change, cookies expire, machines
reboot. **Effort spent making the application bigger is effort spent in the wrong place.** Keep it
small enough that the port to the old machine is a file copy.

## TERRITORY

You own everything under `Projects/small-shit/telegram-meme-bot/`.

- **Do NOT touch anything else in the `small-shit` repo** — it is a shared repo holding several
  unrelated small projects. In particular, do not edit a repo-root `.gitignore`; put your ignore
  rules in `telegram-meme-bot/.gitignore`.
- The repo was clean (`git status --porcelain` empty) when this order was written. **Re-check
  before your first commit** — if there is uncommitted work that is not yours, stop and report it.
  Never `git add .` or `git add -A`; stage explicit paths only.
- If a change you need genuinely falls outside this directory, **STOP and report it**. Do not
  satisfy the boundary by weakening something — no swallowing errors, no silent defaults, no
  broadening an exception to avoid asking.

## READ FIRST

- `yt-dlp --help`, and the format-selection section specifically. Format selection is where this
  project's only real subtlety lives.
- The `python-telegram-bot` docs for polling (`Application.run_polling`).
- Re-run the probes in the evidence table yourself before trusting any of them.

## THE WORK

Four slices. **Commit each slice as you finish it — an uncommitted slice dies with you.** Do not
start the next slice before the current one's check passes.

### Slice 1 — Scaffold that runs

Project skeleton: a venv, `yt-dlp[default,curl-cffi]` and `python-telegram-bot` pinned in
`requirements.txt`, a `.gitignore`, and a `README.md` short enough to be read in a minute.

The bot token comes from an environment variable. **The token must never reach a tracked file**,
not in a default, not in an example, not in a comment.

*Check:* the bot starts, and replying to `/start` in a direct message returns something.
*Commit.*

### Slice 2 — Extraction, isolated from Telegram

One function: URL in, path to a downloaded media file out (or a clear failure). Nothing about
Telegram in it. Quality capped so the result fits comfortably under Telegram's 50 MB bot-upload
ceiling — verify that ceiling yourself rather than trusting this number.

Downloads go to a temp directory that is cleaned up whether the call succeeds or fails.

*Check:* a `__main__` self-check with asserts that extracts one known-good public YouTube URL and
asserts the file exists and is non-empty. One file, no test framework.
*Commit.*

### Slice 3 — Wire it to the group

The bot watches for messages containing URLs and replies with the media. Telegram's own reply
methods handle video, photo and animation differently — pick per media type rather than sending
everything as a document.

Failures must produce a short human reply in the chat ("no pude bajar ese link"), never a silent
drop and never a stack trace pasted into a group of non-programmers.

**Operational step the user must do, and the bot will silently do nothing without it:** Telegram
bots default to *privacy mode*, where they only receive messages that mention them or start with
a command. For the bot to see plain links in a group, privacy mode must be disabled via BotFather
(`/setprivacy` → Disable) **and the bot must be removed and re-added to the group** for the change
to take effect. Verify this is the current BotFather behaviour and write the exact steps into the
README.

*Check:* a link posted in a test group returns a playable video.
*Commit, then STOP — see CHECKPOINT.*

### Slice 4 — Only after the checkpoint clears

Size fallback (if the media cannot be made to fit, reply with the direct media link rather than
failing), and Instagram cookies via a gitignored cookie file from a **throwaway account** — never
the user's own Instagram account, because the ban risk is real and the account is the price.

## DESIGN LAWS

1. **One file until it hurts.** `bot.py`. No `src/`, no packages, no class hierarchy, no plugin
   registry, no dependency injection. If a second file becomes genuinely necessary, say why in the
   commit message.
2. **Nothing macOS-specific, anywhere.** This gets copied to a Linux machine later. No hardcoded
   `/opt/homebrew` paths, no `launchd`, no Homebrew assumptions. Resolve ffmpeg from `PATH`.
3. **No process manager in this order.** The user runs it in a terminal. `systemd` belongs to the
   port, not here.
4. **No database, no job queue, no web framework, no Docker.** At 20 links a week these are
   costs with no benefit.
5. **Secrets never enter git.** Token and cookie file are gitignored and env-configured. Verify
   with `git status` before every commit that neither is staged.
6. **Mark deliberate shortcuts with a `ponytail:` comment** naming the ceiling and the upgrade
   path — e.g. `# ponytail: 720p cap, raise if the group complains about quality`.

## STANDING RULES

- Ship the check with the change; a slice without its check is not done.
- `python -m py_compile bot.py` and the slice's own check must pass before each commit.
- **Do not edit any always-loaded doc** (`CLAUDE.md` / `AGENTS.md`) anywhere in the repo. If this
  work implies a foundation change, write the proposed delta into a scratch note in this directory
  and report it — it gets integrated separately.
- Commit messages, code, comments and docs in English.
- If a platform extractor is broken upstream, that is **not yours to fix**. Report it and move on.

## CHECKPOINT

**Stop after slice 3** — when a link posted in a real group returns a video — and report:

1. Which of the three sites actually worked, with the exact commands and output.
2. Whether ffmpeg merging was needed and what quality the group is actually getting.
3. Anything in the evidence table above that turned out to be wrong. **Finding this order wrong on
   fact is a success, not an inconvenience.**

Do not begin slice 4 before that report is acknowledged. Scope is not yours to extend.

---

## Deferred to the port (NOT this order)

`systemd` unit with `Restart=always`; a weekly timer running `pip install -U yt-dlp`; and an alert
that DMs the owner when extraction starts failing with an auth error — because expiring Instagram
cookies kill this class of bot silently, and the group finding out before the owner does is the
actual failure mode.

## Explicitly rejected (do not build)

- A local Telegram Bot API server for 2 GB uploads — compiling tdlib for a ceiling that meme-length
  clips rarely reach.
- TikTok support — evidence above shows the IP is blocked; not requested either.
- Playlist / channel support, a web dashboard, user accounts, rate limiting, a job queue.
