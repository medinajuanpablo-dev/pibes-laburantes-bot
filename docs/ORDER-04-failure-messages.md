# PROMPT-ORDER 04 — say *why* a link bounced, and stop writing terminal colour codes into the ledger

> Self-contained. Written 2026-08-09 from the ledger of a bot that is live right now.
> **Re-verify every claim below against the live environment.** Every order on this project has
> contained at least one claim the implementing agent correctly refuted; that is the outcome I want.

## CONTEXT

Read `AGENTS.md` first, then `README.md` §1, §5 and §5.1. They are accurate.

The rejected-links ledger shipped an hour ago and has already earned its place: it caught a **real
bounce in the real group**, unprompted, that nobody would otherwise have diagnosed.

```json
{"when":"2026-08-09T17:17:45-03:00","chat_id":-1002462983768,"message_id":69732,
 "url":"https://www.instagram.com/reel/DbpG4CuSKoG/?igsh=...","error":"ExtractionError",
 "detail":"yt-dlp could not download …: [0;31mERROR:[0m [Instagram] DbpG4CuSKoG:
           This content isn't available to everyone: It can't be seen by certain audiences."}
```

Two things are wrong there, and one of them is invisible until you look at the raw JSON.

**The group was told `no pude bajar ese link` and nothing else.** But this failure has a precise,
diagnosable cause: Instagram will not serve that post anonymously. The friend who posted it has no
way to know whether the bot is broken, the link is bad, or the post is restricted — and the honest
answer is the third. A generic apology for a failure the bot *can name* is a missed opportunity, and
it is what makes a working bot look broken.

**The ledger is recording ANSI terminal colour codes.** `[0;31mERROR:[0m` is yt-dlp's
red-text escape sequence leaking into a JSON file that exists to be read and grepped later.

### The failure signatures I measured, just now, against the live sites

Every one of these is a real `yt-dlp --simulate` run from this machine on 2026-08-09:

| Case | The URL I used | The signature |
|---|---|---|
| Instagram, audience-restricted | `instagram.com/reel/DbpG4CuSKoG/` | `This content isn't available to everyone: It can't be seen by certain audiences.` |
| Instagram, no such post | `instagram.com/reel/AAAAAAAAAAA/` | `Instagram sent an empty media response. Check if this post is accessible in your br…` |
| Instagram, a **profile** URL not a post | `instagram.com/nasa/` | `[instagram:user] nasa: Unable to extract data` |
| YouTube, deleted or private | `watch?v=AAAAAAAAAAA` and `watch?v=ZZZZZZZZZZZ` | `Video unavailable` (**identical for both** — you cannot tell them apart) |
| Facebook, dead post | `watch/?v=999999999999999` | `Cannot parse data` |

**One of these is a trap and you must not paper over it.** An earlier agent on this project recorded
a flake where a *perfectly good* Facebook URL also failed with **`Cannot parse data`** after the
self-check had run five times in 25 minutes — i.e. under rate limiting. So on Facebook that string
means *"dead post **or** Facebook is throttling me"*, and the reply must not claim to know which.
Same shape on YouTube: deleted and private are indistinguishable, so do not claim to know which.

## WHY IT MATTERS

The audience is a group of non-technical friends and the bot's only voice is one line of Spanish.
Right now every failure sounds the same, so every failure looks like the bot's fault. Naming the
cause turns "this thing is broken" into "ah, ese post es privado" — and it costs a lookup table.

The ANSI codes matter for the opposite reason: the ledger is the owner's diagnostic instrument, and
an instrument that writes control characters into its own output is one that will be misread.

## TERRITORY

Your own worktree, branched from current `main`. You may change:

- **`bot.py`** — both items.
- **`README.md`, `AGENTS.md`** — the documentation of what you add.

**Do not touch:** `EMPEZAR-ACA.md` (unless you argue a friend genuinely needs it), `docs/**` other
than through the README/AGENTS routing, the two launchers, `requirements.txt` — **no new dependency
for either item** — and `.gitignore`.

**`.env` holds the real token, is gitignored, and will not exist in your worktree.** Never create
one, never ask for a token. **The bot is running in production from the main tree right now** — do
not start a second instance, and do not call `getUpdates`: it would steal the poll from the live bot.

**Escape hatch:** if a change you need falls outside this, STOP and report it. Never satisfy the
boundary by weakening something.

## READ FIRST

- `AGENTS.md` in full — especially the must-stay-red mutation list. Do not break any of them.
- `README.md` §5 (diagnosing) and §5.1 (the ledger).
- `_deliver`, `_apologise`, `record_rejection`, `FAILURE_REPLY` in `bot.py`.

## THE WORK

Two slices, smallest first. Commit each separately, explicit paths.

### Slice 1 — the ledger stores text, not terminal escapes

Strip ANSI escape sequences from the detail before it is recorded. Nothing else about the record
changes.

*Check:* an assert that a detail containing a real escape sequence lands in the ledger clean, and
that ordinary text is untouched. Use the actual bytes from the record above, not a hand-typed
approximation.
*Commit.*

### Slice 2 — name the cause when the bot can

Map a failure to a **specific Spanish reply** when its signature is one you can recognise, and fall
back to the existing `FAILURE_REPLY` when it is not.

Rules, and they are the whole difficulty of this slice:

- **Only map signatures that are in the table above** — the ones actually measured. Do not invent
  entries for failures nobody has seen. If you measure a new one yourself, add it and say so.
- **Never claim more certainty than the signature carries.** Facebook's `Cannot parse data` means
  dead-or-throttled: the reply must offer both and suggest trying again later. YouTube's
  `Video unavailable` means deleted-or-private: same treatment. Writing "ese video no existe" for a
  string that also fires on a private video is a lie the bot would tell confidently.
- **Keep the unknown case exactly as it is.** An unrecognised failure must still produce
  `no pude bajar ese link`. This slice adds precision where precision exists; it must not turn an
  unknown into a guess.
- **The matching must be resilient to yt-dlp changing its wording.** These strings are upstream
  prose and they will drift. Say in a `ponytail:` how you expect that to fail — and make sure it
  fails *back to the generic message*, never into a wrong specific one.
- **The mapping is data, the matching is logic.** Keep them separable so the next person adds a row
  without touching the matcher.
- Every mapped reply is **Spanish**, in the same voice as `no pude bajar ese link` — short, lower
  case, no jargon, no "error", no codes. A friend reads this.
- The ledger keeps recording the **raw** detail regardless of which reply was sent; the friendly
  message is for the chat, the detail is for the owner.

*Check:* asserts driving the classifier with each signature from the table (verbatim) plus an
unrecognised one, proving each maps to its own reply and the unknown falls through to
`FAILURE_REPLY`. Plain strings, no network.
*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **Nothing OS-specific in `bot.py`.**
3. **No new dependency.** A dict and a loop.
4. **Secrets never enter git.**
5. **Code, comments, commits and docs in English; everything the group reads is Spanish.**
6. **Mark deliberate shortcuts with `ponytail:`** naming the ceiling and the upgrade path.

## STANDING RULES

- Ship the check with the change. `python -m py_compile bot.py` and `python bot.py --self-check`
  pass before every commit.
- **Mutation-test both slices**, and re-run enough of `AGENTS.md`'s must-stay-red list to prove you
  broke nothing. Ship the table. On this project mutation testing has caught **five** holes a passing
  run did not, four of them in the implementing agent's own fresh work.
- `git status` before every commit; explicit paths; never `git add .` or `-A`.
- The self-check already does six real downloads and takes about a minute. **Do not add a seventh**
  for this work — the signatures are strings, and a check that needs a broken remote post is a check
  that breaks when someone deletes it.
- You have no token and the live bot is not yours to touch. *"I could not test this live"* is correct.

## CHECKPOINT

Stop after slice 2 and report:

1. What you ran, with output — especially the mutation table.
2. Any signature in my table you could **not** reproduce, and what you got instead.
3. Any signature you found that I missed and think is worth mapping.
4. Anything in this order that turned out to be wrong on fact.
5. Every `ponytail:` you left, and everything unverifiable without a token.
