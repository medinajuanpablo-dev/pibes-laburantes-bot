# PROMPT-ORDER 11 — a DNS hiccup should not cost the group a link

> Self-contained. Written 2026-08-10 from two real bounces in production, minutes old.
> **Re-verify every claim below.** Every order on this project has been wrong about something.

## CONTEXT

Read `AGENTS.md`, then `README.md` §5 and §5.2.

The owner reported two links bouncing. The ledger — the instrument he asked for, doing exactly its
job — named the cause without anybody guessing:

```
2026-08-10 14:30  instagram.com/reel/DbqocqEsbVs/
    ERROR: [Instagram] …: Unable to download webpage: Failed to perform,
    curl: (28) Resolving timed out after 20001 milliseconds
2026-08-10 14:33  instagram.com/reel/DbtjNozpqBy/
    …same, 20000 milliseconds
```

**`curl: (28) Resolving timed out` is DNS failing on the host machine.** Not Instagram, not the
links, not the bot's logic. Confirmed: the *same* reel extracts fine now —
`Downloading 1 format(s): dash-…v+dash-…a`. Both links were good the whole time.

This machine has form: an agent working here yesterday reported the DNS dropping for about eight
minutes mid-task. A rotating host on someone's home wifi will see this more, not less.

### Why the existing retries did not save it

Measured on `_ydl_options`:

```
retries              3
extractor_retries    <not set>
fragment_retries     <not set>
socket_timeout       20
```

Whatever `retries: 3` covers, it did not cover this: the failure came back after a single ~20 s
timeout and went straight to the apology. **Verify what each of those knobs actually governs** —
`retries` is documented for downloads, `--extractor-retries` for *known extractor errors* — and say
which one, if any, reaches a transport failure during metadata extraction. If one of them does and
the real problem is that it is unset, that is a smaller fix than the one below and you should prefer
it. **Measure before choosing.**

## WHY IT MATTERS

Two good links, two apologies, and the group has no idea it was a five-second network blip on one
person's laptop. This is the single most likely recurring failure for a bot that lives on rotating
home connections, and it is the one where the right answer is *try again*, not *explain*.

## TERRITORY

Your own worktree, branched from current `main`. You may change **`bot.py`**, `README.md`,
`AGENTS.md`.

**Do not touch:** the launchers, `instalar-bot.cmd`, `requirements.txt` — **no new dependency** —
`.gitignore`, `docs/**` beyond routing, `EMPEZAR-ACA.md`.

**The bot is live and I am hosting it**: no second instance, never `getUpdates`, go easy on YouTube
(this group is Instagram-only in practice, so prefer Instagram for probing anyway).
`.env` does not exist in your worktree.

## THE WORK

### Slice 1 — try again before apologising

A **transport-level** failure — DNS, connection reset, a timeout reaching the site — should cost one
more attempt, not the link.

- **Only transport failures.** A restricted post, a dead video, an unsupported host: those are
  answers, and retrying them wastes the group's time and the site's patience. Get the discrimination
  from the exception, not from string-matching the message if you can avoid it — and if you cannot,
  say so and key on the narrowest string that works.
- **One extra attempt, with a short pause.** Not a loop, not exponential backoff, no jitter, no
  retry framework. The evidence is a blip that cleared in under three minutes; one retry is either
  enough or it was not a blip.
- **Say what it costs.** With `socket_timeout: 20`, a real outage now makes the group wait roughly
  twice as long for the apology. Name that in a `ponytail:` with the number.
- **The ledger must still record the failure if the retry also fails**, with the real error — and it
  should be possible to tell from the record that a retry happened. Decide how, and keep it cheap.

*Check:* asserts that a transport error is retried and a non-transport one is not, driven by fakes
that raise. No network.
*Commit.*

### Slice 2 — name it when it still fails

Add the signature to `FAILURE_SIGNATURES`. This one is unusual and it is the reason it is worth a
line: **it is the only failure so far where the right advice is "just send it again"**, and the
friend can act on that immediately.

- Spanish, in the same voice, no jargon — no "DNS", no "timeout", no curl code.
- **Do not claim to know whose network it is.** The friend's, the host's, or the site's are
  indistinguishable from here. Hedge like the Facebook row does.
- Key it on the narrowest string that actually appears. The full text is in the ledger record above;
  **re-read it there rather than retyping it from this order.**

*Check:* the signature maps to its own line, and an unrecognised failure still falls through to
`FAILURE_REPLY`.
*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **Nothing OS-specific in `bot.py`.**
3. **No new dependency. No retry library, no backoff library.**
4. **Secrets never enter git, message bodies never enter any file.**
5. **Code and docs in English; everything the group reads in Spanish.**
6. **`ponytail:` on deliberate shortcuts**, with the ceiling and the upgrade path.

## STANDING RULES

- `python -m py_compile bot.py` and `python bot.py --self-check` pass before every commit.
- **Mutation-test both slices** — especially that a non-transport error is *not* retried, which is
  the way this change can quietly go wrong. Re-run the must-stay-red entries you touch. Ship the
  table.
- Explicit paths; never `git add .` or `-A`.
- The self-check already does six real downloads. **Do not add a seventh**, and do not add a check
  that needs a broken network.

## CHECKPOINT

Report:

1. What you ran, and the mutation table.
2. What `retries`, `extractor_retries` and `fragment_retries` each actually govern, and whether the
   smaller fix (setting one of them) would have caught this. If it would, say why you did or did not
   take it.
3. What a real outage now costs the group in seconds, measured or derived.
4. Anything in this order that is wrong on fact.
5. Every `ponytail:` left, and anything needing my live layer.
