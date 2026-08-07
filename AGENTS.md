# AGENTS.md — read this before changing anything

A private Telegram meme bot. **One group of friends, ~20 links a week.** That number is the design
input for every decision here; if a change only makes sense at higher volume, it does not belong.

Everything is `bot.py`. Its self-check is in the same file: `python bot.py --self-check`.

## Routing — where the answers are

| You need… | Go to |
|---|---|
| what it does, how to run it, the architecture | `README.md` §1–§2 |
| the launchers, the baton pass, one-poller-per-token | `README.md` §2.1 and §4.9 |
| how the owner ships a change, bumps a pin, sets up a new host | `docs/updating.md` |
| what a friend hosting the bot is told (Spanish, product copy) | `EMPEZAR-ACA.md` |
| privacy mode / why the bot sees nothing in a group | `README.md` §3 |
| **every measured fact — codecs, sizes, ceilings, timeouts** | `README.md` §4 ← read before touching `MEDIA_FORMAT` or any timeout |
| what breaks in production and how to diagnose it | `README.md` §5 |
| why something was *not* built | `README.md` §6 |
| how the project got here, and which premises turned out false | `docs/history.md` |
| the original plan and prompt-order (superseded) | `docs/archive/` |

## Design laws

1. **One file until it hurts.** No `src/`, no packages, no class hierarchy, no plugin registry, no
   dependency injection. A second file needs its reason in the commit message.
2. **Nothing OS-specific in `bot.py`.** It gets copied onto an old Linux box. No `/opt/homebrew`
   paths, no `launchd`, no Homebrew assumptions. Resolve ffmpeg from `PATH`. The two launchers are
   the only OS-specific files here, that is what they are for, and none of it may leak inwards.
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
- **`ignore_no_formats_error` does nothing while downloading.** yt-dlp's `dl()` calls
  `raise_no_formats(info, forced=True)` and the forced arm raises whatever the flag says. It works
  only with `download=False`, which is why the image fallback probes separately instead of folding
  the flag into `_ydl_options` (`README.md` §4.8).
- **Telegram allows exactly one poller per token, and a conflict here is normal, not a bug.** Two
  pollers both get HTTP 409; the losing bot does not exit, it retries. The launcher's "is anybody
  running?" probe is itself a competing `getUpdates`, so *asking* costs the running instance one
  conflict — which is why `on_error` announces a conflict but only gives up on one that lasts
  `CONFLICT_GRACE`. Exiting on the first one would make the question a remote kill switch, and
  `CONFLICT_EPISODE_GAP` must stay above python-telegram-bot's retry backoff, capped at 30 s.
  Numbers and method: `README.md` §4.9.
- **`drop_pending_updates=True` is a decision, not a default.** Telegram holds updates ~24 h and
  every handover follows a gap in which nobody hosted, so replaying the queue dumps the whole gap
  into the group at once. The accepted cost is that a link posted while the bot was off is lost.
- **A file written by `git` carries no `com.apple.quarantine`; a downloaded one does.** That is the
  entire reason distribution is `git clone` rather than a zip: a quarantined `.command` is refused
  by LaunchServices and never runs. It is also why the launcher can update itself.
- **An image post's thumbnails carry no dimensions, and the list is not sorted worst-to-best.** A
  reel's thumbnails *do* carry width/height, which makes the wrong assumption easy. Selection is by
  downloaded file size for that reason. Also: `duration` and `title` discriminate image from video
  not at all — `formats` is the only signal.

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
  `delivery_decision` `<=` → `<` · the ignore-logging dropping its rejected URLs or leaking the body ·
  `is_image_post` dropping its `formats` or carousel guard · the thumbnail chosen by list order
  rather than by size · `main` not registering `on_error` · the conflict handler's `quiet` branch ·
  `CONFLICT_GRACE` set to 0 (a probe would then kill a healthy bot) · the episode reset in
  `conflict_action` · the Spanish line the person at the window reads ·
  `run_polling` losing `drop_pending_updates`.
- **The self-check really downloads four times** — YouTube, an Instagram reel, an Instagram image
  post, Facebook. That is deliberate: extraction rotting is this project's actual failure mode and
  only a real download detects it. Keep the clips short, and when you change one, verify the codec
  mutation still goes red on it — a clip that only offers h264 would silently empty that check.
  Entries are `(url, expected_kind)` and the kind is asserted, so a reel arriving as a still fails.
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
- **Instagram carousels are refused, not handled.** A multi-item post makes `is_image_post` return
  `False`, so the group gets the apology. yt-dlp models carousels as playlists and its mixed
  photo/video handling is an open upstream problem (#7569, #11792); no public carousel was available
  to measure. Upgrade path: find one, measure the entries, then decide first-slide vs all.
- **Nobody has watched python-telegram-bot hand a `Conflict` to `on_error`.** The rule and the
  handler are asserted, and the library's own code routes polling errors to `process_error`, but
  the end-to-end path needs two live instances on the real token.
- **`run-bot.cmd` has never run on Windows.** It was written on a Mac and only statically checked.
  Say "untested" in those words until somebody watches it; `docs/updating.md` lists what to watch.
- **`pool_timeout` is left at its 1.0 s default.** It governs contention for a 256-connection pool
  that a sequential bot never contends for.
- **PTB processes updates sequentially**, so a slow upload blocks the handler for its duration. The
  upgrade path, if it ever matters, is passing a file object instead of a `Path` — which also stops
  holding 50 MB in RAM.
