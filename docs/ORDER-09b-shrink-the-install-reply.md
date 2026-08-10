# PROMPT-ORDER 09b — the complaint was right, the remedy was not

> Self-contained. Written 2026-08-09. **This order must make `bot.py` smaller.**

## CONTEXT

Read `AGENTS.md`, then `README.md` §2.2.

Order 09 told you to replace `/instalar`'s text reply with a file attachment. **The agent who got it
refused and proved why, on this machine:** a double-clicked `.command` needs the exec bit **and** no
`com.apple.quarantine`; a file delivered by Telegram is mode `644` **and** quarantined, so it fails
both. Right-click → Open answers only the quarantine half and cannot add an exec bit — the `chmod +x`
that would costs the friend exactly the Terminal that sending a file was meant to save. Four
combinations were measured; the table is in `README.md` §2.2. **Do not reopen that. The delivery
mechanism stays text.**

But the owner's complaint was not about the delivery mechanism. It was: *"pusiste algo re complejo,
solo tenías que pasar el launcher"* — and on that he is right. Measured just now:

| | |
|---|---|
| what a friend reads | 12 lines per platform, **22 for the bare case** |
| reply builders + handler | roughly lines 1481–1810 of `bot.py` |
| their self-check | roughly lines 4249–4460, **~211 lines** |
| `bot.py` today | **4477 lines** |

Something a friend could be told in four lines takes ~540 lines of code to produce and prove. That
is the defect.

## WHY IT MATTERS

The audience taps a button and does not read. A 22-line wall gets skimmed and the one line that
matters — that the token comes from the owner — gets lost in it. And a feature this small carrying
this much code makes every future change to it expensive.

## TERRITORY

Your own worktree, branched from current `main`. You may change **`bot.py`**, `README.md`,
`AGENTS.md`, `EMPEZAR-ACA.md`.

**Do not touch:** the launchers, `requirements.txt`, `.gitignore`, `docs/**` beyond routing.
**The bot is live and I am hosting it**: no second instance, never `getUpdates`, go easy on YouTube.
`.env` does not exist in your worktree.

## THE WORK

One slice.

**Cut the reply to what a friend actually needs**, and let the code and its check shrink with it.

- **A platform reply should be about four lines**: the command, and the two things that stop a
  friend. Decide which two from what is there now — one of them is that the token comes from the
  owner, separately. Anything a friend will not act on in the next sixty seconds is not one of them.
- **The bare case must not be the two platform replies glued together.** That is how it reached 22
  lines. Whatever you choose, it must fit the same budget.
- **Delete what the shrink makes redundant, including in the check.** A 211-line self-check for a
  four-line message is the same defect one layer down. **Do not delete the token invariance guard**
  — it is the only thing standing between this feature and a credential leak, it must still cover
  every string the feature can emit, and its mutations must still be red. Everything else in that
  block is negotiable.
- **Delete, never comment out.**

*Check:* the surviving asserts, plus the token invariance guard unchanged in strength.
*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **Nothing OS-specific in `bot.py`** — describing two platforms is text, not behaviour.
3. **No new dependency.**
4. **Secrets never enter git and never enter a chat message.**
5. **Code and docs in English; everything the group reads in Spanish.**
6. **`ponytail:` on deliberate shortcuts**, with the ceiling and the upgrade path.

## STANDING RULES

- `python -m py_compile bot.py` and `python bot.py --self-check` pass before the commit.
- **Re-run the token-guard mutations** — you are rewriting exactly what they guard. Ship the table.
- Explicit paths; never `git add .` or `-A`.
- **If your diff is not net negative in `bot.py`, stop and say so** rather than shipping it. That is
  the whole point of this order and it is the one way to fail it.

## CHECKPOINT

Report:

1. `bot.py` line count before and after, and the reply's line count per platform before and after.
2. The token-guard mutation table.
3. What you deleted from the check and why it was safe to lose.
4. Anything in this order that is wrong on fact.
5. Anything that needs my live layer.
