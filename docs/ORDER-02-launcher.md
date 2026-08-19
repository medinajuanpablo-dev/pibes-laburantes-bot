# PROMPT-ORDER 02 — a double-click launcher for the baton pass

> Self-contained. Written 2026-08-07 after the bot shipped and ran in production.
> **Re-verify every factual claim below against the live environment before relying on it.**

## CONTEXT

The bot is finished, merged, pushed to `github.com/medinajuanpablo-dev/pibes-laburantes-bot`, and has
delivered real media to a real group. Read `README.md` and `AGENTS.md` first — they are accurate and
carry every measured fact.

**The new need.** The owner cannot keep the bot running on his laptop all the time. He wants to hand
his friends something they can **double-click** so that whoever is around can take a turn hosting it.

**This is a baton pass, not parallelism, and that distinction is measured, not assumed.** Telegram
allows exactly one poller per token. Verified today, twice:

- Two concurrent `getUpdates` calls on this token: **both got HTTP 409**, with Telegram's own words —
  `Conflict: terminated by other getUpdates request; make sure that only one bot instance is running`.
- With the real bot running, a single competing `getUpdates` call stole the poll. The bot **did not
  exit** — it survived and kept retrying, logging **6 conflict lines and 3 tracebacks** in ten
  seconds, saying nothing a non-programmer could act on.

That second measurement is the whole reason slice 2 exists. The people using this are the owner's
friends, not developers. A wall of Python tracebacks reads as "this is broken", and the actual
situation — *someone else has it running* — is completely normal and easily explained.

**Environment, measured today:** macOS 15.1.1 arm64 · Python 3.11.7 · ffmpeg at
`/opt/homebrew/bin/ffmpeg` · `.command` files resolve to UTI `com.apple.terminal.shell-script`, i.e.
Terminal is their handler and they are double-clickable from Finder. `timeout(1)` does **not** exist
on this Mac.

## WHY IT MATTERS

The bot only has value while something is running it. Today that is one person's laptop. This turns
"the owner is away, so the group has no bot" into "someone else double-clicks a file". The whole
value is in it working for a person who will not read a README and will not open a terminal — so the
bar is that **every failure mode prints one sentence in Spanish that says what to do next.**

## TERRITORY

You own the repo at `/Users/juampidev/Documents/theMatrix/Projects/the-bot`, working in your own
worktree, branched from current `main` (`9a0bc6e`).

Files you may create or change:
- `run-bot.command` — new, the launcher.
- `bot.py` — **only** the conflict-handling change in slice 2. Nothing else.
- `README.md`, `AGENTS.md` — the documentation in slice 3.

**Do not touch:** `docs/history.md`, `docs/RUN-STATE.md`, `docs/archive/**`, `requirements.txt`,
`.gitignore` (append only if genuinely needed), and **never** `.env` — it exists in the main tree
with the real token, it will not exist in your worktree, and that is correct.

**Escape hatch:** if a change you need falls outside this list, STOP and report it. Do not satisfy
the boundary by weakening something.

## READ FIRST

- `README.md` §2 (run it), §3 (BotFather / privacy mode), §5 (what breaks).
- `AGENTS.md` in full — the design laws and the non-derivable facts.
- Re-run the 409 measurement yourself if you want it; it is two `curl` calls.

## THE WORK

Three slices. Commit each as you finish it, explicit paths only.

### Slice 1 — `run-bot.command`

A bash script, `chmod +x`, committed with its executable bit (`git update-index --chmod=+x` if
needed — **verify the committed mode is 100755**, because a file that arrives without it does not
double-click and the whole deliverable fails at the friend's machine).

It runs from its own directory regardless of where Finder launches it from — derive the directory
from `$0`, do not assume the working directory.

On every run, in order, each step printing **one short Spanish line** on failure and exiting:

1. **Python 3.11+** present. If not: tell them to install it from python.org, with the link.
2. **ffmpeg** on `PATH`. If missing and Homebrew is present, offer to run `brew install ffmpeg` and
   do it if they agree. If Homebrew is absent, print the one-line instruction and the link. **Do not
   download or execute a binary from anywhere yourself** — that is out of bounds, no exceptions.
3. **venv + dependencies.** Create `.venv` and `pip install -r requirements.txt` if the venv is
   missing or the install is stale. First run will take a minute; say so, so it does not look hung.
4. **Token.** If `.env` is absent, prompt for it and write `.env` with mode `600`. **The token must
   never be baked into this script or any tracked file** — the package has to be safe to pass around
   on its own, with the token sent separately by whatever channel the owner chooses.
5. **The one-at-a-time check.** Call `getUpdates` with `timeout=0` once. On HTTP **409**, someone
   <br>⚠️ **Superseded 2026-08-18: `timeout=0` cannot detect anybody** — a new `getUpdates` always
   wins the race and answers 200, so this step never asked the question and the started instance was
   killed by the incumbent's retry instead. The probe now long-polls (`timeout=10`) so the *other*
   side displaces it. Shipped in both launchers; see README.md §4.9.
   else is running it: say so in Spanish, explain that only one person can have it on at a time, and
   ask whether to take over. Exit quietly if they say no.
   - **Name the cost honestly in a comment:** this probe momentarily steals the poll from whoever is
     running, so their instance logs one conflict and recovers. Measured today. There is no
     side-effect-free way to ask Telegram "is someone polling?" — accept the blip, do not invent a
     lock file that will go stale on another machine.
6. **Run the bot** in the foreground, and tell them plainly how to stop it (Ctrl-C, or close the
   window) and that closing it means the group loses the bot until someone else opens it.

*Check:* run it yourself end to end on a **copy of the repo with no `.venv` and no `.env`** — that is
the friend's actual first-run experience and it is the only way to catch a step that only works
because your machine is already set up. **You have no token: stop at the token prompt**, confirm
every step before it behaved, and say so in your report.
*Commit.*

### Slice 2 — a human sentence instead of tracebacks

When another instance takes the poll, the running bot currently logs 6 conflict lines and 3
tracebacks and keeps going. Make it log **one clear line** instead — in the same operational voice as
the rest of the logging — saying another instance has taken over and this one is no longer receiving
messages.

Use `Application.add_error_handler` for this. **Note the apparent contradiction and why it is not
one:** an earlier order told you not to reach for a global error handler — that was scoped to the
*delivery failure* path, where the fix had to be local to `_deliver`. A poll-level `Conflict` is a
different class of event, it has no message to reply to, and the global handler is the correct tool.

Decide, and justify in the commit message, whether the bot should **keep retrying** or **exit
cleanly** on a sustained conflict. Argue it from the baton-pass use case, not from taste. Whatever
you choose, the person watching the window must be able to tell what happened.

*Check:* an assert over whatever pure part you can reach without a network. The full path needs two
live instances, which you cannot run — say so; the owner will verify it live.
*Commit.*

### Slice 3 — documentation, in two voices

- **`README.md`**: a short section on the baton pass — what the launcher does, that only one person
  can run it at a time, and how a friend gets set up. **In English, like the rest of the file.**
- **A friend-facing quickstart, in Spanish**, because the audience is non-technical Spanish speakers
  and this is product copy, not developer docs — the same reason the bot's chat messages are Spanish.
  Put it where a friend will actually find it; recommend a placement and say why.
  It must cover **the Gatekeeper trap**: a `.command` file that arrives via download or zip gets
  quarantined, and double-clicking shows a scary "unidentified developer" dialog. **Verify the exact
  current behaviour and wording on this machine before writing it** — do not describe it from memory.
  Then give the fix in one sentence (right-click → Open, or clone with git instead of downloading).
- **`AGENTS.md`**: add only what is non-derivable — the one-poller constraint and the 409 measurement.

*Commit.*

## DESIGN LAWS

1. **`bot.py` stays OS-agnostic.** The launcher is macOS-specific by nature and that is fine; **none
   of that may leak into `bot.py`**, which still has to be a file copy onto an old Linux box.
2. **One file until it hurts** still holds for the application. The launcher is a second file because
   it is a different artifact, not a second module.
3. **No process manager, no LaunchAgent, no auto-start.** The baton pass is deliberately manual: the
   owner rejected the always-on host today, and a background service that survives reboots quietly
   re-creates the 409 problem when two friends both have one installed.
4. **Secrets never enter git**, and never enter the distributed artifact.
5. **Mark deliberate shortcuts with a `ponytail:` comment** naming the ceiling and the upgrade path.
6. **Every message a friend can see is Spanish. Code, comments, commits and developer docs are
   English.**

## STANDING RULES

- Ship the check with the change. `python -m py_compile bot.py` and `bot.py --self-check` pass before
  every commit that touches `bot.py`.
- Run `shellcheck` on the launcher if it is available; if it is not, say so rather than skipping
  silently.
- `git status` before every commit; explicit paths; never `git add .` or `-A`.
- **Mutation-test anything you assert.** `AGENTS.md` lists the mutations that must stay red; do not
  break them.
- You have no token and no `.env`. Do not try to obtain one. "I could not test this live" is correct.

## CHECKPOINT

Stop after slice 3 and report:

1. **What you actually ran**, with output — especially the clean-copy first-run walkthrough.
2. **What the Gatekeeper behaviour really is** on this machine, measured, not remembered.
3. **Your call on retry-vs-exit** in slice 2 and the reasoning.
4. Anything in this order that turned out to be wrong on fact. Finding that is a success — the last
   three orders each contained at least one claim you correctly refuted.
5. Every `ponytail:` ceiling you left, and anything you could not verify without a token.
