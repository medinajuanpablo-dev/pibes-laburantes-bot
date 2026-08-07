# AGENTS.md — read this before changing anything

A private Telegram meme bot. **One group of friends, ~20 links a week.** That number is the design
input for every decision here; if a change only makes sense at higher volume, it does not belong.

Everything is `bot.py`. Its self-check is in the same file: `python bot.py --self-check`.

## Routing — where the answers are

| You need… | Go to |
|---|---|
| what it does, how to run it, the architecture | `README.md` §1–§2 |
| privacy mode / why the bot sees nothing in a group | `README.md` §3 |
| **every measured fact — codecs, sizes, ceilings, timeouts** | `README.md` §4 ← read before touching `MEDIA_FORMAT` or any timeout |
| what breaks in production and how to diagnose it | `README.md` §5 |
| why something was *not* built | `README.md` §6 |
| how the project got here, and which premises turned out false | `docs/history.md` |
| the original plan and prompt-order (superseded) | `docs/archive/` |

## Design laws

1. **One file until it hurts.** No `src/`, no packages, no class hierarchy, no plugin registry, no
   dependency injection. A second file needs its reason in the commit message.
2. **Nothing OS-specific, anywhere.** This gets copied onto an old Linux box. No `/opt/homebrew`
   paths, no `launchd`, no Homebrew assumptions. Resolve ffmpeg from `PATH`.
3. **No database, no job queue, no web framework, no Docker, no process manager.** At this volume
   they are cost with no benefit. `systemd` belongs to the port, not here.
4. **Secrets never enter git.** The token is `TELEGRAM_BOT_TOKEN` from the environment, kept in a
   gitignored `.env`. Never a default, never an example, never a comment. Run `git status` before
   every commit and stage explicit paths — never `git add .` or `git add -A`.
5. **Code, comments, commit messages and docs in English. The bot's chat messages are in Spanish** —
   that is the group's language.
6. **Mark deliberate shortcuts with a `ponytail:` comment** naming the ceiling *and* the upgrade
   path.

## Non-obvious things you cannot derive from the code

- **`MEDIA_FORMAT` is load-bearing and its branches are not interchangeable.** Telegram delivers
  AV1-in-webm as a **document** — a grey file row with no playback — which defeats the entire point
  of the bot. The rule is *mp4 container, not AV1*; it is **not** *h264 only*, because vp9 in an mp4
  played inline. Both facts are measured (`README.md` §4.1). The `?` in `height<=?720` is load-bearing
  too: without it Instagram and Facebook match no branch at all.
- **A height cap is not a size guarantee.** Portrait video (720x900, 1440x1800) exceeds a 720 height
  cap while being a small file. The only real size guard is the byte count of the finished file, and
  `filesize_approx` is `NA` on two of the three sites, so anything built on the estimate is dead code.
- **`_apologise()` swallowing its exception is correct — and it is the only place that is.** The
  failure reply is the last line of defence; if it re-raises, the group gets nothing at all. This
  happened in production.
- **`connect_timeout` must be passed explicitly.** python-telegram-bot defaults it to 5.0 s and only
  substitutes its own default when the caller passes nothing, so `write_timeout` alone does not
  protect an upload.
- **The self-check's log-capture raises the logger's level.** `_self_check` configures logging at
  WARNING, so INFO records are dropped before reaching any handler; a logging assert written without
  that is silently vacuous.

## How to prove a change

- **Ship the check with the change.** A slice without its check is not done. `python -m py_compile
  bot.py` **and** `python bot.py --self-check` pass before every commit.
- **Green is not enough — mutation-test it.** On a clean copy (`git archive HEAD | tar -x -C <tmp>`),
  sabotage one thing at a time and confirm the check goes red. Prove it dies when the feature is
  *absent* **and** when it is *present but wrong*. In this repo mutation testing caught three real
  holes that a passing run did not, including a fix that shipped with no guard at all and a privacy
  assert that covered only one of two branches.
  Mutations that must stay red: `MEDIA_FORMAT` → codec-agnostic · `_send` drops `connect_timeout` ·
  the apology left unprotected · `MESSAGE_FILTER` → `filters.TEXT | filters.CAPTION` ·
  `delivery_decision` `<=` → `<` · the ignore-logging dropping its rejected URLs or leaking the body.
- **The self-check really downloads from all three sites.** That is deliberate: extraction rotting is
  this project's actual failure mode and only a real download detects it. Keep the clips short, and
  when you change one, verify the codec mutation still goes red on it — a clip that only offers h264
  would silently empty that check.
- **You cannot test the Telegram layer without a token, and you should not try.** No `.env` exists in
  a fresh worktree. Deterministic checks are yours; the live run belongs to whoever holds the token.
  *"I could not test this live"* is the correct note, not a failure.

## Open, known, and deliberately unfixed

- **`URL_PATTERN` requires an explicit `http(s)://` scheme**, so a bare `youtu.be/xyz` — which
  Telegram itself renders as a link — is ignored. The log now says `no URL recognised` for exactly
  this case, which discriminates it from an unsupported host. Upgrade path: read `message.entities`.
  Not done because no confirmed case has needed it yet.
- **The oversize → direct-link path has never run against Telegram.** Nothing in the live session
  exceeded 50 MB. It is covered by asserts and a source read only. Treat it as unproven.
- **`pool_timeout` is left at its 1.0 s default.** It governs contention for a 256-connection pool
  that a sequential bot never contends for.
- **PTB processes updates sequentially**, so a slow upload blocks the handler for its duration. The
  upgrade path, if it ever matters, is passing a file object instead of a `Path` — which also stops
  holding 50 MB in RAM.
