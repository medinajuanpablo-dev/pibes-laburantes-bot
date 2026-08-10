# PROMPT-ORDER 12 — deliver it later, but only when later can work

> Self-contained. Written 2026-08-10.
> **Depends on Order 11 being merged first** — it shares that order's transient-vs-permanent
> discrimination and must not build a second one.
> **Re-verify every claim below.** Every order on this project has been wrong about something.

## CONTEXT

Read `AGENTS.md`, then `README.md` §5 and §5.1, and Order 11.

The owner believed the bot already accumulated failed links and re-sent them later. **It does not,
and never has.** Verified: `read_rejections` has exactly one non-test caller — `--rejected`, the
report he reads. There is no `JobQueue`, no `run_once`, no scheduler, nothing that re-delivers. The
ledger accumulates failures **for a human to analyse**, which is what he asked for originally; the
re-sending half was never built and he assumed it.

It is worth building, narrowly, and the real ledger says exactly how narrowly:

```
transient (a later attempt would likely succeed):  2
permanent (retrying forever is pure noise)      :  4
   ExtractionError      "This content isn't available to everyone"   <- never succeeds
   ExtractionError      (same post, same answer)
   OversizeForTelegram  73 MB over the ceiling                       <- never succeeds
   OversizeForTelegram  120 MB over the ceiling                      <- never succeeds
```

**Four of six can never succeed.** A naive "accumulate everything and resend" would retry those
forever and turn the group's chat into noise. The two that matter are the DNS blips from 14:30 and
14:33 today: both links were good, both cleared within three minutes, and both are lost.

**`JobQueue` is not available** — `apscheduler` is not installed, and installing it would break the
no-new-dependency law for a timer. `asyncio.create_task` plus `sleep` is stdlib and is what a bot
that already runs an event loop should use anyway. Confirm that yourself.

## WHY IT MATTERS

The failure this covers is the single most likely one for a bot living on rotating home wifi, and it
is the only class where waiting actually helps. Getting the link a few minutes later, threaded under
the message that asked for it, is the difference between "the bot is flaky" and "the bot is fine".

## TERRITORY

Your own worktree, branched from current `main` **after Order 11 has merged**. You may change
**`bot.py`**, `README.md`, `AGENTS.md`.

**Do not touch:** the launchers, `instalar-bot.cmd`, `requirements.txt` — **no new dependency, and
specifically not `apscheduler`** — `.gitignore`, `docs/**` beyond routing, `EMPEZAR-ACA.md`.

**The bot is live and I am hosting it**: no second instance, never `getUpdates`. This group is
Instagram-only in practice; prefer Instagram for probing.
`.env` does not exist in your worktree.

## THE WORK

### Slice 1 — a bounded, in-memory deferred retry

When a delivery fails with a **transient** error — the same discrimination Order 11 introduced,
reused, not re-invented — schedule one later attempt instead of forgetting the link.

Every one of these is a constraint, not a suggestion:

- **Transient only.** A restricted post, an oversized video, an unsupported host: those are answers.
  Never queue them. This is the whole reason the feature is worth having rather than harmful.
- **Bounded in time and in attempts.** A video that arrives six hours later is confusing, not
  helpful — the group has moved on. Pick a window measured in minutes, justify the number, and give
  up cleanly after it.
- **Bounded in size.** A twenty-minute outage must not accumulate an unbounded queue that then
  floods the chat when the network returns. Cap it and say what happens at the cap.
- **Reply to the original message**, so a video arriving later lands threaded under the link that
  asked for it and needs no explanation.
- **Do not apologise twice.** The group already got the apology when it first failed. A later
  success just delivers; a later failure says nothing at all.
- **It dies with the process, and that is correct.** Do not persist the queue to disk: the next host
  is a different machine, and a queue that survives a restart would re-deliver links whose context
  is long gone. **Say this in a `ponytail:`** — the owner should know that closing the window drops
  whatever was pending.
- The ledger keeps recording the original failure as it does today. If a deferred attempt eventually
  succeeds, decide whether that is worth recording and argue it.

*Check:* asserts that a transient failure is queued and a permanent one is not, that the cap holds,
that the window expires, and that a successful later attempt does not produce a second apology.
Driven by fakes and a controllable clock — **no real sleeping in the check**.
*Commit.*

### Slice 2 — say what is actually true

`README.md` and `AGENTS.md` currently describe a ledger that only records. Update them, and be
precise about the boundary a reader will otherwise get wrong: **what is retried, for how long, and
that a link which bounced while nobody was hosting is gone regardless** — `drop_pending_updates=True`
means the bot never sees it, and no queue in this process can help with that.

*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **Nothing OS-specific in `bot.py`.**
3. **No new dependency, no scheduler library, no persistence layer.** A task and a sleep.
4. **Secrets never enter git; message bodies never enter any file.**
5. **Code and docs in English; everything the group reads in Spanish.**
6. **`ponytail:` on deliberate shortcuts**, with the ceiling and the upgrade path.

## STANDING RULES

- `python -m py_compile bot.py` and `python bot.py --self-check` pass before every commit.
- **Mutation-test it**, especially that a *permanent* failure is never queued — that is how this
  change turns into chat spam. Re-run the must-stay-red entries you touch. Ship the table.
- **The check must not sleep for real.** A self-check that takes minutes is a check nobody runs.
- Explicit paths; never `git add .` or `-A`.

## CHECKPOINT

Report:

1. What you ran, and the mutation table.
2. Your window, your cap, and the reasoning for each number.
3. What happens to a link that bounces while nobody is hosting, stated plainly.
4. Anything in this order that is wrong on fact.
5. Every `ponytail:` left, and what needs my live layer — including how I could force a transient
   failure to watch a deferred delivery actually happen.
