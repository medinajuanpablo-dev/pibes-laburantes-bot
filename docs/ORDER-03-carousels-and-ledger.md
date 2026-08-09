# PROMPT-ORDER 03 — Instagram carousels, and a ledger of everything that bounced

> Self-contained. Written 2026-08-07 while the bot was live in production.
> **Re-verify every factual claim below against the live environment before relying on it.** Every
> order on this project so far has contained at least one claim the implementing agent correctly
> refuted, and saying so plainly is the outcome I want.

## CONTEXT

The bot is shipped, running, and documented. **Read `AGENTS.md` first, then `README.md` §1, §4.8 and
§5** — they are accurate and they will stop you re-deriving things wrong.

Two pieces of work, from one real incident. The owner pasted this in the group and the bot answered
`no pude bajar ese link`:

```
https://www.instagram.com/p/DbcsX-BlkZX/?img_index=9&igsh=ZzV3NTdqcWFxMTJi
```

### What I measured before writing this

**It is a carousel, and it is refused by design.** `AGENTS.md` and `README.md` §4.8 already say
carousels are refused rather than guessed at, because no public example was available to measure.
**One is available now — that URL — so the placeholder can become a real decision.**

| Probe | Result |
|---|---|
| `is_supported(url)` | `True` — detection is fine, nothing to fix there |
| plain `yt-dlp --simulate` | `ERROR: [Instagram] …: No video formats found!` (repeated per slide) |
| `--ignore-no-formats-error --dump-single-json` | `_type: playlist`, `formats: 0`, `thumbnails: 0`, **`entries: 10`** |
| each of the 10 entries | `formats: 0`, **`thumbnails: 13`** — i.e. every slide is an image, reachable exactly like the single image post in §4.8 |
| `--write-thumbnail -o '%(playlist_index)s.%(ext)s'` | **all 10 written**, 97 KB – 266 KB each, 1074x1074 to 1179x1179, ~1.7 MB total |

**And I verified the delivery end of it live, against the real group:** I sent those 10 files to
Telegram in one `sendMediaGroup` call. Response `ok: true`, **10 messages in the album**, first item
classified as `photo`. So the whole approach is proven before you start — what is left is wiring it.

`sendMediaGroup` accepts **at most 10 items**. This carousel has exactly 10, which is a coincidence
you must not build on: verify the limit yourself and handle a longer carousel deliberately.

## WHY IT MATTERS

Two different things, and the second one outlives the first.

1. A carousel is a completely ordinary thing to paste in a group of friends, and right now it is a
   flat "no pude bajar ese link".
2. **The bot has no memory of what it failed on.** Every bounce so far has been found by a human
   noticing and telling the owner, who then asks someone to dig through a log that only exists on
   whichever friend's laptop happened to be hosting. The owner wants to be able to ask, later,
   *"analizá todos los rebotados y arreglalos"* — and that is only possible if the bot writes them
   down.

## TERRITORY

You own the repo at `/Users/juampidev/Documents/theMatrix/Projects/the-bot`, in your own worktree.

- **`bot.py`** — the code changes.
- **`.gitignore`** — append the ledger file. Never remove or reorder existing lines.
- **`README.md`, `AGENTS.md`** — the documentation.
- **Do not touch** `EMPEZAR-ACA.md` unless a friend genuinely needs to know about this (argue it if
  you think so), `docs/history.md`, `docs/RUN-STATE.md`, `docs/archive/**`, the two launchers, or
  `requirements.txt` — **no new dependency for either of these features.**

**`.env` holds the real token, is gitignored, and will not exist in your worktree. That is correct.**
Never create one, never ask for a token, never write one anywhere.

**Escape hatch:** if a change you need falls outside this, STOP and report it. Never satisfy the
boundary by weakening something — no swallowed error, no silent default.

## READ FIRST

- `AGENTS.md` in full, especially *Non-obvious things you cannot derive from the code* and the list
  of mutations that must stay red. **Do not break any of them.**
- `README.md` §4.8 (the image-post path you are extending) and §5 (the operational voice).
- `is_image_post`, `_image_fallback`, `_download_best_thumbnail`, `_deliver`, `_send` in `bot.py`.

## THE WORK

Two slices. Commit each separately, explicit paths.

### Slice 1 — the rejected-links ledger

**Do this one first.** It is independent of carousels, it is the thing the owner actually asked for,
and it is what will diagnose the *next* surprise without a round trip.

Every time a link the bot accepted as supported does not end in delivered media, append one record
to a **`rejected.jsonl`** in the bot's own directory, gitignored. One JSON object per line: an ISO
timestamp, the chat id, the message id, the URL, the error class name, and the error message
(truncated to something sane — a yt-dlp error can be enormous).

- Append-only, one line at a time, flushed. It must survive the process being killed by closing a
  Terminal window, which is how this bot normally stops.
- **A failure to write the ledger must never break delivery or the apology.** It is diagnostics.
- **Never log the message body** — private group, same rule as the existing ignore-logging.
- Add `bot.py --rejected` to print what is in it, grouped so a pattern is visible at a glance
  (by error class, then by host). This is the command the owner will run when he asks for the
  analysis, so make its output readable by itself, with no explanation needed.

**Name the limitation in a `ponytail:` and in the README, do not paper over it:** with a rotating
host the ledger fragments — each friend's machine records only its own bounces. Do not build
syncing, a server, or a database for this; at ~20 links a week the owner reading his own file plus
asking a friend to send theirs is the right cost. State the upgrade path.

*Check:* asserts over the record-building and the reading/grouping, driven by plain dicts and a
temp file. No network.
*Commit.*

### Slice 2 — carousels as an album

An Instagram post whose entries are **all images** should come back as a Telegram **album**, not an
apology.

- Reuse the existing thumbnail selection — best-by-downloaded-size, per slide. Do not invent a
  second mechanism; §4.8 explains why size and not reported dimensions.
- Send with `sendMediaGroup`. **Verify its item limit yourself** rather than trusting my "10", and
  decide deliberately what a longer carousel does. Whatever you choose, the group must not silently
  receive a truncated post — if slides are dropped, say so in the chat, in Spanish.
- **Mixed photo/video carousels are still unmeasured and I could not find one.** Do not guess: if
  you can find a public example, measure it and handle it; if you cannot, keep refusing that case
  explicitly, with the reason in a `ponytail:`, and make sure a mixed carousel gets the apology
  rather than a half-post.
- **`img_index` in the URL: ignore it, deliberately.** The owner's link carried `img_index=9`, but
  Instagram adds that based on which slide the sharer happened to be looking at, so treating it as
  an instruction is a guess. The album is the safe superset — the slide they meant is in it. Record
  that as a `ponytail:` with the upgrade path, so the decision is visible and reversible.
- **Do not regress the two existing paths.** A single image post must still arrive as one photo, and
  a video post must still be a video — `is_image_post`'s "has formats ⇒ not an image" guard is a
  must-stay-red mutation in `AGENTS.md` and it protects a real failure mode: a video whose formats
  failed must never degrade into its poster frame.

*Check:* extend `SELF_CHECK_URLS` with the carousel URL above and assert it comes back as the album
kind, with the slide count. The existing entries are `(url, expected_kind)` — extend that shape
rather than special-casing.
*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`. The ledger is a data file, not a module.
2. **Nothing OS-specific in `bot.py`.** It gets copied onto Linux. Use `pathlib`, not shell.
3. **No database, no queue, no new dependency.** A JSONL file is the whole ledger.
4. **Secrets never enter git**, and the ledger is gitignored because it is the group's content.
5. **Code, comments, commits and docs in English; anything the group reads is Spanish.**
6. **Mark deliberate shortcuts with `ponytail:`** naming the ceiling *and* the upgrade path.

## STANDING RULES

- Ship the check with the change. `python -m py_compile bot.py` and `python bot.py --self-check`
  pass before every commit.
- **Mutation-test both slices** and re-run enough of `AGENTS.md`'s must-stay-red list to prove you
  broke nothing. Ship the table. On this project mutation testing has caught **four** holes that a
  passing run did not — three of them in the implementing agent's own fresh work.
- `git status` before every commit; explicit paths; never `git add .` or `-A`.
- You have no token. You cannot test the Telegram send path. *"I could not test this live"* is the
  correct note — the CEO runs the live layer.

## CHECKPOINT

Stop after slice 2 and report:

1. What you ran, with output — especially the mutation table.
2. `sendMediaGroup`'s real item limit as **you** verified it, and what a longer carousel does.
3. Whether you found a mixed photo/video carousel, and what you measured if so.
4. Anything in this order that turned out to be wrong on fact.
5. Every `ponytail:` you left, and everything you could not verify without a token.
