# PROMPT-ORDER 09 — send the file, delete the essay

> Self-contained. Written 2026-08-09. This order **removes** more than it adds.

## CONTEXT

Read `AGENTS.md`, then `README.md` §2.1 and the `/instalar` section.

`/instalar` currently replies with **a wall of formatted text** telling a friend to open Terminal and
paste a command. The owner's reaction, verbatim: *"pusiste algo re complejo, solo tenías que pasar el
launcher"*. He is right, and the number is embarrassing: **517 lines of `bot.py` for "hand over the
launcher"**.

**Why I rejected sending a file, and why that reasoning was weak.** Two objections:

1. *A launcher alone is inert* — true and measured: it ends at `exec "$VENV_PY" bot.py` with no
   `bot.py` present. **But that only rules out sending the existing launcher unchanged.** A tiny
   bootstrap that clones the repo and hands off to the real launcher is still "a file you double
   click", and it still lands in a git clone, so it still self-updates. I never considered it.
2. *A downloaded file is quarantined on macOS* — **I generalised from a measurement I did not take.**
   What was measured is a file extracted from a **ZIP by Archive Utility**. A file downloaded by
   Telegram Desktop was never tested. I wrote it into `AGENTS.md` as though it covered both.

So the design went to "paste this in Terminal" on the strength of one true objection that had a
cheap answer and one unmeasured assumption. Opening Terminal is the hardest step for this audience,
and I made it the only step.

## WHY IT MATTERS

Double-clicking a file a friend was handed is the whole point. Everything else was me routing around
a problem I had not confirmed.

## TERRITORY

Your own worktree, branched from current `main`. You may change:

- **`bot.py`** — the `/instalar` reply.
- **Two new bootstrap files** — name them so nobody confuses them with `run-bot.command` /
  `run-bot.cmd`.
- **`README.md`, `AGENTS.md`, `EMPEZAR-ACA.md`** — including **correcting the quarantine claim in
  `AGENTS.md`** to say exactly what was measured (ZIP via Archive Utility) and what was not
  (a Telegram download).

**Do not touch:** `run-bot.command`, `run-bot.cmd`, `requirements.txt`, `docs/**` beyond routing.

**The bot is live and I am hosting it.** No second instance, never `getUpdates`, go easy on YouTube.
`.env` does not exist in your worktree.

## THE WORK

### Slice 1 — the bootstrap files

One per platform. Each should be small enough to read in one screen. It must:

- work when double-clicked from wherever the friend's client saved it, deriving nothing from the
  current working directory;
- clone the repo into a fixed, predictable folder, and **if that folder already exists, just use
  it** — a friend who double-clicks twice must not get an error or a second copy;
- hand off to the real launcher, which already does Python, ffmpeg, venv, token and the take-over
  question. **Do not duplicate any of that.** If you find yourself re-implementing a check the
  launcher already has, stop and hand off earlier;
- fail with one Spanish line naming the only thing it needs that it cannot install: `git`.

*Check:* run the macOS one yourself, in a scratch directory, with the target folder both absent and
already present. Report both. `shellcheck` it if available.
*Commit.*

### Slice 2 — `/instalar` sends the file

Replace the essay with the file plus a caption of **at most two short lines**.

- Same platform choice as today (`mac` / `windows` / bare).
- **Delete everything the file makes redundant** — the code blocks, the git instructions, the
  formatting machinery, the HTML parse mode if nothing else needs it. **This slice should be net
  negative in `bot.py`.** Report the line count before and after; if it is not smaller, say why.
- **Keep the token guard exactly as it is.** The invariance test is the one part of the previous
  landing that earns its place, and a caption is still text the bot emits. It must cover the caption
  and the filename.
- The caption still has to carry the one thing the file cannot: **the token comes from the owner,
  separately.** One line.
- **Say in the caption what to do if macOS refuses to open it** (right-click → Open). One line, and
  it costs nothing if the quarantine never happens.

*Check:* asserts that each platform sends the right file with the right caption, that the bare case
does something sensible, and the token invariance over caption and filename.
*Commit.*

## DESIGN LAWS

1. **One file until it hurts** — `bot.py` plus the launchers plus these two bootstraps. Two new files
   is the point of this order, not a violation of it.
2. **Nothing OS-specific in `bot.py`.**
3. **No new dependency.**
4. **Secrets never enter git and never enter a chat message.**
5. **Code and docs in English; everything the group reads in Spanish.**
6. **`ponytail:` on deliberate shortcuts**, with the ceiling and the upgrade path.

## STANDING RULES

- `python -m py_compile bot.py` and `python bot.py --self-check` pass before every commit.
- **Mutation-test the token guard again** after you rewrite what it guards, and re-run the
  must-stay-red entries in the code you touch. Ship the table.
- Explicit paths; never `git add .` or `-A`.
- **Delete, do not comment out.** Anything the file replaces should leave no corpse.

## CHECKPOINT

Stop after slice 2 and report:

1. `bot.py` line count before and after, and the mutation table.
2. What happened when you ran the bootstrap with the folder absent and present.
3. Whether anything in the old reply turned out to be load-bearing after all.
4. Anything in this order that is wrong on fact.
5. What still needs the owner: whether a Telegram-downloaded file is quarantined at all, which
   nobody here has ever measured, and what he should see if it is.
