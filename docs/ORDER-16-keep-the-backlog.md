# PROMPT-ORDER 16 — a link posted while nobody hosted must still arrive

> Self-contained. Written 2026-08-10 18:0x as item 3 of a three-item GOAL.
> **Re-verify every claim below against the live repo.** Reports are discovery-grade and they drift.
> **This is the smallest order this project has had. Do not make it bigger.** The whole change is
> one argument, and most of your work is the comment that explains why, plus deleting the assertion and
> the `ponytail:` that currently say the opposite.

## CONTEXT

Read `AGENTS.md`, then `README.md` §2 and §5.

**Run `git merge --ff-only main` first and report what it moved** — the last three worktrees here were
handed to agents several commits behind while their orders said "current `main`".

### What the code does today, and why

`bot.py` ends `main()` with `app.run_polling(drop_pending_updates=True)`, above a `ponytail:` (around
line 2479 — re-grep, it will have moved) that lays out this exact trade-off and lands on the other side:

> whatever was posted while nobody was hosting is lost. Telegram holds updates for about 24 hours and
> `run_polling()` replays them all by default, so the first person to open the launcher would dump
> everything posted in the gap into the group in one burst […] **Measured with nobody running: 7 updates
> queued, 2 of them reels.** At ~20 links a week the flood is clearly worse than the loss […] Upgrade
> path if it ever bites […] keep the backlog but deliver only the updates whose message date is within a
> few minutes of startup, dropping the rest.

There is also a self-check asserting `drop_pending_updates is True` (around line 4844).

### The decision, and it is the owner's, already made

**The owner chose: recover everything Telegram still holds — no age filter.** The reasoning, which you
should understand rather than just obey, is that the comment's own measurement undercuts its conclusion:
**the "burst" was 7 updates.** Seven. At ~20 links a week, a host opening the launcher in the morning
gets a handful of last night's links, not a flood — and the loss it was avoiding is the thing the owner
actually complained about. **So the filter the `ponytail:` proposed is NOT wanted**: the generous version
is both more valuable and one word shorter.

**Do not build the age filter.** If you think the measurement is wrong or you find evidence of a genuinely
large backlog, **stop and report it** instead of building a filter nobody asked for.

## WHY IT MATTERS

The hosting rotates between friends, so gaps are the normal state, not an edge case. Today every link
posted in a gap is gone forever and nobody is told — which is the same silence two other orders were spent
eliminating.

## TERRITORY

Your own worktree. You may change **`bot.py`**, `README.md`, `AGENTS.md`.

**Do not touch:** the launchers, `instalar-bot.cmd`, `requirements.txt` (**no new dependency**),
`.gitignore`, `EMPEZAR-ACA.md`, `docs/**`.

**The CEO is hosting the live bot from the main tree**: **never call `getUpdates`, never start a second
bot instance.** No `.env` in your worktree and you do not need one. **No live surface** — deterministic
checks only. The live layer here is genuinely interesting (force a real gap, paste into it, start the bot,
watch it arrive) and it is **the CEO's**, after merge.

## THE WORK

### Slice 1 — keep the backlog

- Flip the argument so pending updates are delivered instead of dropped.
- **Rewrite the `ponytail:` — do not leave it.** It currently documents the opposite decision and its
  reasoning, so leaving it makes the file contradict itself, which is worse than having no comment. What
  replaces it is **not** a `ponytail:` at all, because this is no longer a shortcut with a ceiling: it is
  a decision with a cost. Say what the cost is (a host who starts after a long gap may see several older
  links arrive at once), that the burst was **measured at 7 updates**, and that the owner chose recovery
  over silence on 2026-08-10.
- **Invert the self-check assertion** so it pins the new behaviour. An assertion left asserting `True`
  would fail the gate and tell you this; one silently deleted would leave the choice unprotected. **Pin
  it, do not delete it** — this argument is exactly the kind of thing a later "cleanup" flips back.
- README §2 or §5, wherever the run behaviour is described for a host: one line saying what a host now
  sees when they start after a gap. Spanish is for the group only; the README is English.

**One thing to check rather than assume, and say what you find:** does anything else in `main()` or in the
conflict/baton-pass path depend on the backlog being empty at startup? The baton pass hands over between
two hosts, and I have not traced whether a replayed backlog interacts with it. My read is that it does
not — updates are consumed by offset, so an update the incumbent already delivered is not pending for the
taker — but **that is a read, not a measurement**, and it is yours to confirm or refute.

*Check:* the self-check pins the new value. Whatever you find about the baton pass goes in the report.
*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **No new dependency.**
3. **Secrets never enter git; message bodies never enter any file.**
4. **Code and comments in English; everything the group reads in Spanish.**
5. **A comment that documents a decision the code no longer makes is a defect**, not stale prose.

## STANDING RULES

- `python -m py_compile bot.py` and `python bot.py --self-check` **both pass before the commit.**
- **Commit the moment it is green.** Two agents here have died with finished work uncommitted.
- **Mutation-test it**: the argument flipped back must turn the check red. That is the whole point of
  pinning it.
- Explicit paths only. Never `git add .` or `-A`.
- **Resist scope.** If you find yourself adding a constant, a filter, a helper or a second slice, you have
  left the order. Report the idea instead of building it.

## CHECKPOINT

Report: what you ran; the mutation result; **what you found about the baton pass**; whether the line
numbers in this order had drifted; anything here wrong on fact; every `ponytail:` you removed or added.

---
*Self-audit 6/6 before sending: contradiction — this order deliberately reverses a `ponytail:` in the
code and says so in slice 1, so the two do not silently disagree; `AGENTS.md`'s must-stay-red list has a
`run_polling losing drop_pending_updates` row, which this change makes obsolete — flagged for the agent
to replace rather than delete, and it is in the mutation demand. References — the two line numbers are
given as approximate with an instruction to re-grep, because they have drifted twice today. Wrong mode —
n/a. Border — a first run with an empty backlog behaves identically, which is why the check pins the
argument rather than any observed delivery. Abuse — "the smallest order" could be read as licence to skip
the comment rewrite, so law 5 makes the stale comment a defect. Rot — no counts pinned; the 7-update
measurement is dated and attributed.*
