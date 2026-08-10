# PROMPT-ORDER 08 — the bot hands out its own installer, and it stays updated forever

> Self-contained. Written 2026-08-09 against a bot running in production.
> **Re-verify every claim below.** Every order on this project has been wrong about something.

## CONTEXT

Read `AGENTS.md` first, then `README.md` §2.1 (the baton pass), §4.9 and `EMPEZAR-ACA.md`.

The owner asked for two things in one breath: *"podés pedirle al bot su propio ejecutable
actualizable y te lo pasa, podés pedirle mac o windows"*, and then *"asegurate que se mantenga
siempre actualizado"*.

**The second requirement is what decides the design of the first**, and it rules out the obvious
implementation. Sending the launcher as a Telegram **file attachment** cannot work, for two
independent reasons, both already established in this repo:

1. **A launcher on its own is inert.** `run-bot.command`'s first act is `git pull` in a directory
   with no `.git`, and its last is `exec .venv/bin/python bot.py` with no `bot.py` present. It also
   needs `requirements.txt`. Measured by reading the file: it references `.git`, `bot.py`,
   `requirements.txt` and `.venv`.
2. **A downloaded file is quarantined; a git-written one is not.** That is already in `AGENTS.md` as
   a measured fact, and it is the entire reason distribution is `git clone`: a quarantined
   `.command` is refused by LaunchServices and never runs.

And shipping the whole repo as an archive (450 KB, small enough) fails the owner's actual
requirement: without a `.git` it can never `git pull`, so it is a snapshot that rots — the opposite
of *"siempre actualizado"*.

**So what the bot hands over is the one command that creates a real clone.** It travels as a
*message*, so nothing is quarantined; it lands the friend in a git working copy, so the launcher's
existing `git pull` keeps them current forever with no further action. That is the same feature the
owner asked for, built the only way that satisfies the constraint he added.

### Verified before writing this
- **Anonymous clone works** — `git ls-remote https://github.com/medinajuanpablo-dev/pibes-laburantes-bot.git`
  answers without credentials, so a friend with no GitHub account can clone.
- **The clone URL is derivable at runtime** — `git remote get-url origin` in the bot's own directory
  returns it. Whether to derive it or hardcode it is your call; argue it. Deriving means the
  instructions cannot go stale if the repo moves; hardcoding means the bot does not shell out.

## WHY IT MATTERS

Onboarding a friend today means sending them a document and hoping they follow it. This turns it
into: they ask the bot, they paste one line, they are hosting — and because it is a clone, every fix
we ship reaches them on their next double-click without anybody being told.

## TERRITORY

Your own worktree, branched from current `main`. You may change:

- **`bot.py`** — the command and its reply.
- **`README.md`, `AGENTS.md`, `EMPEZAR-ACA.md`** — this one genuinely reaches the friend-facing
  guide, because it becomes the easy path in.

**Do not touch:** the launchers, `requirements.txt` — **no new dependency** — `.gitignore`,
`docs/**` beyond routing.

**The bot is live from the main tree and I am hosting it.** Do not start an instance, **never call
`getUpdates`**, and go easy on YouTube (the IP was challenged earlier today). `.env` does not exist
in your worktree and must not.

## THE WORK

### Slice 1 — the command

A Telegram command that replies with the install instructions. `/start` already exists as a
`CommandHandler`, so this is the same shape.

- **Name it in Spanish**, like everything the group reads.
- It must serve **both platforms**: the owner asked to be able to request mac or windows. Decide
  whether that is an argument (`/x mac`) or both blocks in one reply, and argue it — remember the
  audience taps a button and does not read carefully. Bare, with no argument, must do something
  sensible rather than erroring.
- Put the pasteable part in a Telegram **code block** so the client offers tap-to-copy. Get the
  parse mode right, and note that `bot.py` currently sets no `parse_mode` anywhere — if you
  introduce one, escaping becomes your problem and the existing plain-text replies must not break.
- The command must work **in a group and in a DM**.
- **Register it with `setMyCommands`** at startup so it appears in Telegram's command menu — the
  friend does not know it exists otherwise. If you decide that is not worth it, argue why.

### Slice 2 — the content of that reply

Per platform: one command that clones and opens the launcher. macOS ends at
`run-bot.command`, Windows at `run-bot.cmd`.

Three things the reply must say, because each is a real obstacle measured or known here:

- **`git` must be installed.** On macOS, running `git` without the Command Line Tools pops Apple's
  installer dialog; on Windows it is usually absent entirely. Say so in one line, with what to do.
- **The token comes from the owner, separately.** The launcher prompts for it on first run.
- **Only one person hosts at a time**, and the launcher will ask before taking over.

**The hard rule of this whole order: the bot must NEVER send the token.** It has it in its own
environment, and it is now answering a request from anyone who can message it — including anyone who
adds it to another group. A single interpolation mistake turns this feature into a credential leak.
**Ship an assert that the reply text cannot contain the token**, driven by a fake token in the
environment, and make it impossible to satisfy by accident.

*Check:* asserts over the reply builder for each platform and the bare case — that each carries the
right launcher filename, the clone URL, and the three warnings; and the token assert above.
No network.
*Commit each slice.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **Nothing OS-specific in `bot.py`** — it *describes* two platforms, which is text, not behaviour.
3. **No new dependency.**
4. **Secrets never enter git, and now: never enter a chat message either.**
5. **Code, comments, commits and docs in English; everything the group reads is Spanish.**
6. **Mark deliberate shortcuts with `ponytail:`** naming the ceiling and the upgrade path.

## STANDING RULES

- Ship the check with the change. `python -m py_compile bot.py` and `python bot.py --self-check`
  pass before every commit.
- **Mutation-test both slices**, especially the token assert — prove it fails when the token leaks
  into the text. Ship the table.
- `git status` before every commit; explicit paths; never `git add .` or `-A`.
- You cannot send a Telegram message; I run the live layer.

## CHECKPOINT

Stop after slice 2 and report:

1. What you ran, with output — especially the mutation table and the token-leak mutation.
2. Whether you derived the clone URL or hardcoded it, and why.
3. Whether you registered the command menu, and what `setMyCommands` cost.
4. Anything in this order that turned out to be wrong on fact.
5. Every `ponytail:` left, and everything that needs my live layer.
