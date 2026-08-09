# PROMPT-ORDER 06 — the baton pass drops the baton

> Self-contained. Written 2026-08-09 from a live experiment against the production bot.
> **Re-verify every claim below.** Every order on this project has contained at least one claim the
> implementing agent correctly refuted, and the last one contained four.

## CONTEXT

Read `AGENTS.md` first, then `README.md` §2.1 (the baton pass) and §4.9 (the one-poller constraint).

The launchers, the conflict handling and the whole hand-over UX exist so that friends can take turns
hosting the bot. **That feature does not work.** Nobody had ever run two instances at once; the
conflict logic was verified only as a pure function. I ran the real experiment tonight against the
real token, and here is the whole of it:

```
18:23:54  host B starts                      (the friend taking the baton)
18:23:55  A: "another instance has taken the poll; this one is receiving nothing meanwhile"
18:23:59  B: "another instance has taken the poll; this one is receiving nothing meanwhile"
18:25:04  B stops: "El bot ahora lo tiene otra persona. Podés cerrar esta ventana."
18:25:09  A stops: "El bot ahora lo tiene otra persona. Podés cerrar esta ventana."
          -> getUpdates confirms NOBODY is polling. The group has no bot.
```

**Both instances concluded they had lost, and both were right about the conflict and wrong about the
conclusion.** Each told the person at the window that somebody else now has the bot. Nobody did.

Everything else in that experiment behaved exactly as designed and **must not regress**: one warning
per episode, silence through the retries, **zero tracebacks in either process**, the 60 s grace
honoured to the second, and a Spanish line at the window. The machinery is right. The **decision** is
wrong.

### Why it happens

`conflict_action` is symmetric, and so is Telegram. `getUpdates` does not designate a winner: it
terminates whichever long-poll is outstanding when a new one arrives, so two instances simply keep
knocking each other off and **both** observe a sustained conflict. Same code, same inputs, same
conclusion — mutual give-up is the only possible outcome.

The asymmetry has to come from outside the conflict itself, and **it already exists in the product**:
the launcher asks *"Justo ahora lo tiene prendido otra persona … ¿Se lo saco y lo prendo yo? [s/n]"*.
A person who answered **s** has declared they are taking over. That intent is thrown away today — the
launcher just runs `bot.py` the same way either way.

## WHY IT MATTERS

This is the feature the owner asked for by name, the reason both launchers exist, and the thing his
friends were handed. Its failure mode is the worst available: **the group silently loses the bot**,
and the two people involved are each told the other one has it, so neither investigates.

## TERRITORY

Your own worktree, branched from current `main`. You may change:

- **`bot.py`** — the decision and its plumbing.
- **`run-bot.command`** and **`run-bot.cmd`** — passing the intent through. Keep them mirror images;
  **the Windows one is untested on real Windows and stays that way** — do not pretend otherwise.
- **`README.md`, `AGENTS.md`, `EMPEZAR-ACA.md`** — this one genuinely reaches the friend-facing
  guide, since it changes what they are told at the window. Spanish there, English in the others.

**Do not touch:** `docs/**` beyond routing, `requirements.txt` — **no new dependency** — `.gitignore`.

**The bot is running in production from the main tree and I am hosting it.** Do not start an
instance. **Never call `getUpdates`.** `.env` does not exist in your worktree and must not.

**Escape hatch:** if a needed change falls outside this, STOP and report it.

## READ FIRST

- `AGENTS.md` in full, especially the must-stay-red list — `CONFLICT_GRACE = 0`, the `quiet` branch,
  the episode reset and the Spanish line are all on it and all still apply.
- `conflict_action`, `on_error`, `main` in `bot.py`; the "one at a time" step in both launchers.
- `README.md` §2.1 and §4.9, which describe the behaviour you are changing.

## THE WORK

### Slice 1 — carry the taking-over intent from the launcher into the bot

The launcher already knows. Pass it through, on both platforms, without inventing a config file: an
environment variable or a command-line flag, your call, argued in the commit message.

The launcher sets it **only** when the person was actually asked and answered yes. A normal start,
with nobody else running, must be indistinguishable from today.

*Check:* an assert that the bot reads the intent correctly when present, absent, and set to junk.
*Commit.*

### Slice 2 — make the decision asymmetric

An instance that was **told to take over** must win: it persists through the conflict rather than
giving up. An instance that was **not** must yield, as it does today.

- Keep `conflict_action` a pure function; add the intent as an input rather than reaching for global
  state. Its three outcomes today are `announce` / `quiet` / `give-up`; decide whether the taking-over
  side needs a fourth and say why in the commit message.
- **The yielding side's message is currently a lie in the failing case** — it says somebody else has
  the bot. After this change it will be true in the normal hand-over, but **it is still a claim about
  another process you cannot observe.** Make the wording survive being wrong.
- **The taking-over side must not persist forever.** If it is still conflicting long after the
  incumbent should have yielded, something unmodelled is happening — two people both answering yes,
  say. Decide what it does then, and make sure the outcome is not "both run forever fighting".
  Whatever you choose, **no path may end with nobody polling**, which is exactly today's bug.
- **Do not break the single-instance case.** A lone bot that hits a transient conflict — my own
  earlier measurement showed the launcher's own probe causes exactly one — must still behave as it
  does today.

*Check:* drive `conflict_action` through both roles over a simulated timeline: incumbent yields,
taker persists, taker's own long-conflict outcome, and the transient single-instance blip. Pure
function, no network, no second process.
*Commit.*

### Slice 3 — say the true thing in all three docs

`README.md` §2.1 and §4.9 describe a hand-over that did not work; `EMPEZAR-ACA.md` tells a friend
what they will see. Update all three to the measured behaviour, and record the experiment above in
`AGENTS.md` as the non-obvious fact it is: **two instances of the old build both quit; asymmetry has
to be injected from outside because Telegram provides none.**

*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`, plus the two launchers that already exist.
2. **Nothing OS-specific in `bot.py`** — the launchers are where that lives.
3. **No new dependency. No lock file, no coordination service, no shared state between machines** —
   the hosts are different laptops on different networks and nothing they could share is reliable.
   The only channel between them is Telegram itself, and it refuses to arbitrate.
4. **Secrets never enter git.**
5. **Code, comments, commits and docs in English; everything a friend reads is Spanish.**
6. **Mark deliberate shortcuts with `ponytail:`** naming the ceiling and the upgrade path.

## STANDING RULES

- Ship the check with the change. `python -m py_compile bot.py` and `python bot.py --self-check`
  pass before every commit.
- **Mutation-test every slice**, and re-run the conflict-related entries of `AGENTS.md`'s
  must-stay-red list — you are editing exactly that code. Ship the table.
- **You cannot run the two-instance experiment**; I will. Your checks are the pure-function timeline.
  Say plainly in your report that the end-to-end hand-over is unverified from your side.
- `shellcheck` the macOS launcher if available; say so if it is not.
- `git status` before every commit; explicit paths; never `git add .` or `-A`.

## CHECKPOINT

Stop after slice 3 and report:

1. What you ran, with output — especially the mutation table.
2. The mechanism you chose to carry the intent, and why that one.
3. What the taking-over side does when its own conflict does not end, and why that cannot end with
   nobody polling.
4. Anything in this order that turned out to be wrong on fact.
5. Every `ponytail:` left, and everything that needs my two-instance test to confirm.
