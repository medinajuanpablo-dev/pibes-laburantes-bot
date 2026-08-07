# PROMPT-ORDER 01 — Telegram meme bot, slices 1–3 + size fallback

> Self-contained. Written 2026-08-07 by the CEO after a live entry audit. Assume you know nothing
> about the conversation that produced this file.
> **Re-verify every factual claim below against the live environment before relying on it.** The
> measurements here are one probe, from one IP, at one moment — the method is stated so you can see
> its blind spots. If you find this order wrong on fact, that is a success: report it.

## CONTEXT

A private Telegram bot for one group of friends. Someone pastes a YouTube / Instagram / Facebook
link in the group; the bot replies with the video file so nobody leaves the chat.
**Scale: one group, roughly 20 links a week.** Every design decision follows from that alone.

`PLAN.md` in this directory is the original phase-1 plan and is your background reading. **This
order supersedes it where they differ** — the differences are listed under *Deltas from PLAN.md*
below, and each one has a measurement behind it.

**Repo state at your branch point:** `the-bot/` is a fresh git repo whose first commit contains only
docs (`PLAN.md`, `RUN-STATE.md`, this order, `.gitignore`). No application code exists. You are the
only track — nothing else is in flight.

**Environment, measured 2026-08-07 14:33–14:40 on this Mac** (`sw_vers`, `command -v`, `--version`):

| Fact | Value | How I know |
|---|---|---|
| OS | macOS 15.1.1 arm64 | `sw_vers`, `uname -m` |
| Python | 3.11.7 (`python3`) | `python3 --version` |
| ffmpeg | present at `/opt/homebrew/bin/ffmpeg` | `command -v ffmpeg` — **resolve from `PATH`, never hardcode this** |
| yt-dlp | **not installed system-wide.** I installed `yt-dlp[default]` 2026.07.04 into a throwaway venv outside this repo to run the probes | `command -v yt-dlp` → nothing |
| JS runtime | `deno` MISSING · `node` and `bun` present | `command -v` each |
| `timeout(1)` | **does not exist on this Mac.** It killed one of my own commands. Use `yt-dlp --socket-timeout N` instead | ran it, got `command not found` |

**yt-dlp probes — `--simulate` only, no downloads, against `https://www.youtube.com/watch?v=dQw4w9WgXcQ`,
from the user's residential IP, once, at 14:37 on 2026-08-07:**

| Format string | Result |
|---|---|
| *(default / best)* | `2160p 243768398 213s` |
| `b[filesize<45M]/b[height<=720]/b` | `360p mp4 11832459` |
| `bv*[height<=720]+ba/b[height<=720]` | **`720p 21026831`** |

What that establishes and what it does not:

- YouTube extraction works from this IP **today**, with no cookies, no PO token, no proxy, no bot
  check. It does not establish that it keeps working, nor that it works at volume, nor that it works
  for age-restricted or region-locked videos — **I tested exactly one public music video.**
- The 360p result is **not** a YouTube limitation. It is a format-selection consequence: the
  single-file (progressive) format tops out there. Anything above needs separate video+audio streams
  merged by ffmpeg. The third row is that merge, and it lands at ~20 MB — comfortably under
  Telegram's ceiling.
- Every yt-dlp invocation printed: *"No supported JavaScript runtime could be found. Only deno is
  enabled by default… YouTube extraction without a JS runtime has been deprecated, and some formats
  may be missing."* **Extraction still worked, at 2160p and at merged 720p.** Do not add a JS runtime
  — see DESIGN LAWS. Do document it (slice 1).
- **Instagram and Facebook were NOT re-probed by me.** `PLAN.md`'s table records both failing, but
  its Instagram test used a *profile page* (not a reel or post) and its Facebook test URL may simply
  have been dead — the cause was never isolated. **Treat "IG/FB fail" as unproven in both
  directions.** Your job is not to make them work; it is to make them fail *gracefully*.

**Telegram, measured 2026-08-07 14:40 via `getMe` on the real token:** the bot exists
(`@pibesLaburantesSyndicateBot`), `can_join_groups: true`, and **`can_read_all_group_messages:
false`** — i.e. privacy mode is currently ON, so it cannot see plain links in a group yet. The owner
has to fix that in BotFather; it is not yours and it does not block you.

## WHY IT MATTERS

The application is trivial and will be correct on day one. What decides whether this bot still works
in six months is operational: yt-dlp breaks when platforms change, cookies expire, machines reboot.
**Effort spent making the application bigger is effort spent in the wrong place.** Keep it small
enough that the port to an old spare Linux machine later is a file copy.

## TERRITORY

You own **everything inside `/Users/juampidev/Documents/theMatrix/Projects/the-bot/`** except the
four files listed as not-yours below. You are working in your own git worktree; treat its root as
that directory.

**Not yours, do not modify:**
- `PLAN.md`, `RUN-STATE.md`, `ORDER-01-slices-1-3.md` — CEO-owned foundation docs.
- `.gitignore` — you MAY append lines to it, never remove or reorder existing ones.
- Anything outside this directory, ever.

**The secret:** the real bot token lives in `.env` in the CEO's main tree. `.env` is gitignored, so
**it does not exist in your worktree and you must never create it, never ask for it, and never write
a token anywhere.** Your code reads `os.environ["TELEGRAM_BOT_TOKEN"]` and that is the whole
contract. If a check of yours seems to need a live Telegram connection, that check is wrong — see
*How this track earns its proof*.

**Escape hatch:** if a change you genuinely need falls outside your territory, **STOP and report
it.** Do not satisfy the boundary by weakening something — no swallowing an exception, no silent
default, no making a required thing optional to avoid asking.

## READ FIRST

- `PLAN.md` in this directory — background and design intent. Superseded by this order where they
  differ.
- `yt-dlp --help`, the **format selection** section specifically. That is where this project's only
  real subtlety lives.
- The `python-telegram-bot` docs for `Application.run_polling` and for the `reply_video` /
  `reply_photo` / `reply_animation` methods. Latest on PyPI is **22.8** as of today — check yourself.
- **Re-run my probes above before trusting them.** Reports are discovery-grade and platform
  behaviour drifts weekly.

## THE WORK

Four slices. **Commit each one as you finish it — an uncommitted slice dies with you.** Do not start
a slice before the previous one's check passes. `python -m py_compile bot.py` must pass before every
commit.

### Slice 1 — Scaffold that runs

A venv, `requirements.txt` pinning `yt-dlp[default,curl-cffi]` and `python-telegram-bot`, an
appended `.gitignore` if anything is missing, and a `README.md` short enough to read in a minute.

`bot.py` reads the token from `TELEGRAM_BOT_TOKEN` and exits with a clear message if it is unset.
**The token must never reach a tracked file** — not in a default, not in an example, not in a
comment.

The README must contain, in English:
1. How to run it (venv, install, env var, `python bot.py`).
2. **The BotFather steps the owner must do**, because the bot silently does nothing without them:
   `/setprivacy` → Disable, **then remove the bot from the group and re-add it** for the change to
   take effect. Verify this is still BotFather's current behaviour and say so if it is not.
3. A short *Operations* section: yt-dlp is the part that rots. Note `pip install -U yt-dlp` as the
   first thing to try when extraction starts failing, and note that yt-dlp currently warns about a
   missing JavaScript runtime — harmless today, and if YouTube extraction starts failing with
   missing formats, `--js-runtimes node` (or installing `deno`) is the escape hatch.

*Check:* `python -m py_compile bot.py`, and running `python bot.py` with `TELEGRAM_BOT_TOKEN` unset
prints the clear error and exits non-zero rather than raising a traceback.
*Commit.*

### Slice 2 — Extraction, isolated from Telegram

One function: URL in → path to a downloaded media file out, or a clear failure. **Nothing about
Telegram in it.** Downloads go to a temp directory cleaned up whether the call succeeds or fails.

Quality cap: prefer merged 720p, fall back down. Start from
`bv*[height<=720]+ba/b[height<=720]/b` — I measured that resolving to 720p / ~20 MB. **Verify
Telegram's real bot-upload ceiling yourself** (PLAN.md asserts 50 MB) and size the cap against what
you find, not against my number.

*Check:* a `__main__` self-check with plain `assert`s that extracts one known-good public YouTube URL
and asserts the file exists and is non-empty. One file, no test framework.
*Commit.*

### Slice 3 — Wire it to the group

The bot watches group messages for URLs and replies with the media.

- **Pick the reply method per media type** — `reply_video` / `reply_photo` / `reply_animation` — not
  `reply_document` for everything.
- **Failures produce a short human reply in Spanish** (`"no pude bajar ese link"`), never a silent
  drop and **never a stack trace pasted into a group of non-programmers.** Log the real error
  locally.
- Keep the Telegram-touching layer a thin shell over pure functions, so the logic is testable
  without a network. That is what makes the check below possible.

*Check:* extend the `__main__` self-check with `assert`s over the pure helpers — URL detection in a
message body (including: no URL, several URLs, a URL mid-sentence, a bare domain that is not a link),
and the media-type → reply-method mapping. **No mocking framework, no live Telegram call.**
*Commit.*

### Slice 4 — Size fallback (this half only)

If the media cannot be made to fit under the upload ceiling, **reply with the direct media link**
rather than failing.

**Explicitly out of scope: Instagram cookies, a throwaway account, and anything Instagram-specific.**
That half of PLAN.md's slice 4 is deliberately not in this order. Do not build it.

*Check:* an `assert` over the decision function (size under ceiling → send file; over → send link),
driven by a plain number, not by an actual huge download.
*Commit, then STOP — see CHECKPOINT.*

## DESIGN LAWS

1. **One file until it hurts.** `bot.py`. No `src/`, no packages, no class hierarchy, no plugin
   registry, no dependency injection. If a second file becomes genuinely necessary, say why in the
   commit message.
2. **Nothing macOS-specific, anywhere.** This gets copied to a Linux machine later. No hardcoded
   `/opt/homebrew` paths, no `launchd`, no Homebrew assumptions. **Resolve ffmpeg from `PATH`.**
3. **No process manager, no `systemd`.** The user runs it in a terminal. That belongs to the port.
4. **No database, no job queue, no web framework, no Docker.** At 20 links a week these are costs
   with no benefit.
5. **No new runtime dependency to silence a warning.** Specifically: do not add `deno` or wire in
   `node`. Extraction works without one today, and the port target is an old Linux box. Document it
   instead (slice 1).
6. **Secrets never enter git.** Run `git status` before every commit and confirm no `.env`, no
   cookie file and no token is staged. Never `git add .` or `git add -A` — stage explicit paths.
7. **Mark deliberate shortcuts with a `ponytail:` comment** naming the ceiling and the upgrade path
   — e.g. `# ponytail: 720p cap, raise if the group complains about quality`.

## STANDING RULES

- Ship the check with the change; a slice without its check is not done.
- `python -m py_compile bot.py` **and** the slice's own check pass before each commit.
- Code, comments, commit messages and docs in **English**. The bot's user-facing chat messages are in
  **Spanish** — that is the group's language.
- **Do not create or edit any `CLAUDE.md` / `AGENTS.md`.** If this work implies a foundation change,
  write the proposed delta into `NOTES-agent.md` in this directory and report it.
- If a platform extractor is broken upstream, that is **not yours to fix.** Report it and move on.

### How this track earns its proof

- **You own the deterministic checks**: `py_compile`, the `__main__` self-checks, and the real
  yt-dlp extraction in slice 2 (that one genuinely hits the network — it should).
- **The live Telegram layer is the CEO's, not yours.** Do not run the bot against the Telegram API,
  do not poll, do not try to obtain a token, do not ask the user to test anything. The CEO runs the
  real bot in a real group after merging your work. An "I couldn't test this live" note is expected
  and correct; a check that silently needs a token is a defect.

## CHECKPOINT

**Stop after slice 4** and report:

1. **What actually worked**, with the exact commands and their output — especially the real quality
   and file size the group will get, and whether ffmpeg merging was needed.
2. **Telegram's real upload ceiling** as you verified it, and what you set the cap to.
3. **Anything in this order that turned out to be wrong on fact.** Finding that is a success, not an
   inconvenience — say it plainly.
4. Anything you deferred, doubted or worked around, and any `ponytail:` ceilings you left.

Do not extend scope. Instagram cookies, TikTok, playlists, a dashboard, rate limiting and a job queue
are all explicitly rejected.

---

## Deltas from PLAN.md (each with its reason)

| # | PLAN.md said | This order says | Why |
|---|---|---|---|
| 1 | Territory is `Projects/small-shit/telegram-meme-bot/` | Territory is `Projects/the-bot/`, its own git repo | The user placed the plan here and chose a standalone repo at kickoff. Drops the shared-repo constraints entirely. |
| 2 | Quality cap implied by the 360p progressive probe | Merged **720p** via `bv*[height<=720]+ba/…` | Measured: 720p = ~20 MB, under half the ceiling. PLAN.md itself flags 360p as a format-selection consequence, not a limit. |
| 3 | Slice 4 = size fallback **+ Instagram cookies** | Size fallback only | Instagram cookies need a throwaway account, which is the user's to create. Locked out of scope at kickoff. |
| 4 | Slice 3's check is "a link posted in a test group returns a playable video" | Slice 3's check is deterministic over pure helpers | The live layer is the CEO's (it holds the token and the group). Split so this track can't produce a false green. |
| 5 | Nothing about a JS runtime | Documented as a known future breakage, no dependency added | Every yt-dlp call warns about it. Works today; the port target argues against adding one. |
