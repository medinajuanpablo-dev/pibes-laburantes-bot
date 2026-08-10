# PROMPT-ORDER 17 — a comment in `bot.py` asserts a cost that measurement refuted

> Self-contained, and deliberately tiny: **one comment, no logic.** Written 2026-08-10 19:35.
> **Re-verify the claim below against the live repo** before you change anything.

## CONTEXT

Order 16 landed `drop_pending_updates=False` (a link posted while nobody hosted now arrives). Its
implementing agent reasoned — carefully and plausibly — that this introduces a new cost: **a baton pass
would repeat the newest link**, because the yielding instance's shutdown `getUpdates` should meet the same
HTTP 409 that made it yield, leaving its last batch unacknowledged for the taker to re-deliver. It wrote
that into a comment near `app.run_polling(...)` (around `bot.py:2687` — re-grep, line numbers on this file
have drifted four times today) and hedged it as inference, not measurement, which was honest and correct.

**I then measured it, and it is false.**

```
# with a real taker instance polling the same token, verified alive before and after:
GET /getUpdates?timeout=0&offset=<last+1>   ->   HTTP 200,  ok:true,  no error_code
```

It does not 409. A `timeout=0` `getUpdates` **displaces** the other poller for an instant and wins — the
same mechanism that makes the launcher's own probe steal a live poll (`README.md` §4.9), which this
project already documents. So the yielding instance *can* acknowledge its last batch, and there is no
duplicate.

Two independent signals, not one: the direct call above returns 200, **and** the yielding side's log
carries no python-telegram-bot *"updates may be fetched again"* warning, which is the line PTB emits when
that cleanup fails. The control that makes it non-vacuous: the taker was confirmed still polling after
being displaced, so the call really did contend with a live poller.

## WHY IT MATTERS

This project's own design law says a comment documenting something the code does not do is a **defect,
not stale prose**. A future agent reading that comment would either build de-duplication nobody needs, or
tell the owner about a duplicate that does not happen. `AGENTS.md` and `README.md` §2.1 have already been
corrected by me; `bot.py` is the last place the refuted claim survives, and I may not touch it.

## TERRITORY

Your own worktree, branched from current `main`. **You may change `bot.py` only, and only the comment.**
**Run `git merge --ff-only main` first and report what it moved.**

**Do not touch:** any logic — not one executable line — `README.md`, `AGENTS.md` (both already corrected),
the launchers, `requirements.txt`, `docs/**`.

**Escape hatch:** if you conclude the comment is *right* and my measurement is wrong, **STOP and report
that instead.** That is a completely acceptable outcome and I would rather have it than a comment edited
to match a wrong CEO. Say what you measured.

**I am hosting the live bot from the main tree**: never call `getUpdates`, never start a second instance.
No `.env`, no live surface.

## THE WORK

### Slice 1 — the comment tells the truth

Rewrite the part of that comment which asserts the handover duplicate so that it records **what was
measured**: that the acknowledgement succeeds (HTTP 200 with a taker polling), why (a `timeout=0` poll
displaces and wins, the §4.9 mechanism), and that the plausible 409 argument was refuted. Keep it short —
this is a comment, not an essay, and the long version now lives in `README.md` §2.1 and `AGENTS.md`.

Keep everything else in that comment intact: the decision, the owner's choice, the 7-update measurement,
the age-of-arrivals cost. **Those are all still true.**

*Check:* none is possible or wanted — a comment has no behaviour. The gates below are the whole gate.
*Commit.*

## DESIGN LAWS

1. **A comment that documents something the code does not do is a defect.**
2. **English in the code.**
3. **Record the refutation, do not just delete the claim** — a future agent will re-derive the same
   plausible argument, and the note is what stops it.

## STANDING RULES

- `python -m py_compile bot.py` and `python bot.py --self-check` must pass. **Read the exit status
  directly — do not pipe the gate through `head`**, which closes the pipe and reports a false failure
  (it cost me a false red today).
- **Zero executable lines may change.** Prove it in your report with `git diff` evidence.
- Explicit paths only. Never `git add .` or `-A`.

## CHECKPOINT

Report: the diff; proof that no executable line changed; whether you agree with my measurement or
refute it; what `git merge --ff-only main` moved.

---
*Self-audit 6/6: contradiction — `AGENTS.md` and `README.md` §2.1 now carry the corrected version, and
this order points at both so the three cannot drift apart. References — the line number is given as
approximate with an instruction to re-grep, because it has moved four times today. Wrong mode — n/a.
Border — if the comment turns out not to contain the claim, the escape hatch covers it. Abuse — "comment
only" could be read as licence to tidy neighbouring prose, so the order names exactly what must stay
intact. Rot — the measurement is dated and its method stated, so a later reader can re-run it.*
