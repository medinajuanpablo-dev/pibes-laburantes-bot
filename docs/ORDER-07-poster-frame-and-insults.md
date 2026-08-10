# PROMPT-ORDER 07 — a blocked video comes back as a still, and the bot learns to apologise

> Self-contained. Written 2026-08-09 from a live defect the owner reported.
> **Re-verify every claim below.** Every order on this project has been wrong about something and the
> agent caught it; that is the outcome I want.

## CONTEXT

Read `AGENTS.md` first, then `README.md` §4.8 (the image-post path) and §5.2 (named failures).

### The defect the owner hit

He pasted `https://youtube.com/shorts/5kC43KL_mBE?si=…` and **the bot replied with a still image
instead of the video.**

Measured, with the bot's own options:

```
plain extraction  -> DownloadError: [youtube] 5kC43KL_mBE:
                     "Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies…"
fallback probe    -> formats=0  thumbnails=38  is_image_post=True   <-- delivers the poster frame
```

**It is not about Shorts.** As of tonight this IP is challenged for **every** YouTube URL — including
`jNQXAC9IVRw` and `tPEE9ZwTmy0`, both of which extracted fine an hour earlier. I almost certainly
caused it myself with dozens of extractions while hunting, and it is probably temporary. **That does
not matter for this order**: whatever the cause, an extraction failure must never come back as a
still frame, and today it does — for every YouTube link, for as long as the block lasts.

**I also refuted my own first hypothesis.** I assumed the long-standing "no JavaScript runtime"
warning had finally started costing us formats. Forcing `--js-runtimes node` produces the **identical**
error, so the JS runtime is not involved. Do not chase it.

### Why the existing guards do not catch this

`is_image_post()` refuses a post that *has* formats — the guard that stops a video whose formats
failed from degrading. Here extraction never got far enough to report formats at all, so `formats` is
genuinely `[]` and the guard cannot fire. The later fix — `_download_best_thumbnail` returning `None`
when nothing comes down — does not help either, because a thumbnail **does** come down.

The discrimination that is actually available, and that nothing in the code uses:
**only Instagram has image posts at all.** `SUPPORTED_HOSTS` is YouTube, Instagram and Facebook. A
YouTube or Facebook video that fails to extract is a failure, full stop — it can never be an image
post, so the fallback should never run for it. That kills the whole class rather than patching one
symptom.

## WHY IT MATTERS

A still frame is worse than an apology. The friend sees *something*, assumes the bot worked, and
nobody learns that YouTube is blocked — while the ledger records a success-shaped nothing. Right now
**every** YouTube link in the group behaves this way.

## TERRITORY

Your own worktree, branched from current `main`. You may change:

- **`bot.py`** — both slices.
- **`README.md`, `AGENTS.md`** — the documentation.

**Do not touch:** the launchers, `docs/**` beyond routing, `requirements.txt` — **no new
dependency** — `.gitignore`, `EMPEZAR-ACA.md` (argue it if slice 2 changes what a friend should know).

**The bot is live from the main tree and I am hosting it.** Do not start an instance. **Never call
`getUpdates`.** `.env` does not exist in your worktree and must not.

**One extra restriction this time: go easy on YouTube.** The IP is already challenged and more
extractions make it worse for the group. The self-check hits YouTube once per run — that is fine —
but do not loop it, and prefer Instagram or Facebook for any ad-hoc probing.

**Escape hatch:** if a needed change falls outside this, STOP and report it.

## READ FIRST

- `AGENTS.md` in full, especially the must-stay-red list — several entries are in this code.
- `is_image_post`, `_image_fallback`, `_download_best_thumbnail`, `carousel_slides`, `_deliver`,
  `on_message`, `FAILURE_SIGNATURES`, `record_rejection` in `bot.py`.

## THE WORK

### Slice 1 — an extraction failure is never a still frame

Restrict the image fallback (single image **and** carousel) to the only site that has image posts.

- Derive the site from something reliable. The URL's host and yt-dlp's `extractor` key are both
  candidates and they can disagree — a Facebook `share/v/` link redirects to another host internally.
  **Pick one, say why in the commit message, and make sure a `share/r/` reel and an `instagr.am`
  short-form link both still land on the right side.**
- The original extraction error must survive to the group and to the ledger, so the named-failure
  table can name it.
- **`Sign in to confirm you're not a bot` deserves its own row in `FAILURE_SIGNATURES`** — it is
  measured, it is live right now, and it is the one failure with an operator action attached: the
  `YTDLP_COOKIES` hook already exists and this is exactly what it is for. The Spanish line should
  tell the group plainly that YouTube is blocking, without jargon and without blaming them.
- **Do not weaken any existing guard.** The `formats` guard stays. The carousel guards stay.

*Check:* asserts that a YouTube-shaped failure never reaches the image path while an Instagram image
post and an Instagram carousel still do, and that the bot-check signature maps to its own Spanish
line. Dict-driven, no network.
*Commit.*

### Slice 2 — the bot answers to being called stupid

The owner asked for this in his words: *"cada vez que lea 'bot estupido' en cualquier mensaje (o
cualquier variante como 'estupido bot' o 'vot estupido' o letras faltantes) debe registrarlo y
responder 'Lo lamento, hago lo que puedo'."*

So: when a message insults the bot in that shape, reply **exactly** `Lo lamento, hago lo que puedo`
and record it.

- **Both word orders**, accents optional (`estúpido`/`estupido`), any case, and **tolerant of typos**:
  he explicitly named `vot estupido` and missing letters. A plain substring match will not do.
- Do the tolerance with the **standard library** — `difflib` is already available and
  `unicodedata` handles the accents. **No new dependency, no fuzzy-matching library, no regex zoo.**
- **The false-positive side is the one that matters.** This fires on ordinary chat, in a group of
  friends who talk all day. Decide a similarity threshold from evidence, not taste: write down a set
  of phrases that must fire and a set of ordinary Spanish sentences that must not, and tune against
  both. Include near-misses that must **not** fire — a message about a *person* being stupid,
  "estúpido" alone with no bot, "bot" alone. Ship both lists as the check.
- Record each hit. **It is not a bounced link**, so decide deliberately where it belongs: its own
  file, or the existing ledger with its own class. Argue the choice in the commit message; the owner
  reads `--rejected`, and burying insults among broken links serves nobody. Whatever you choose,
  **the privacy rule holds: no message bodies.** The matched phrase is not the body — decide whether
  even that is worth storing and say why.
- This is the first thing the bot reacts to that is **not a link**. `on_message` currently returns
  early when there is no URL; that path now has a second reason to exist. Do not let it change what
  happens to links.

*Check:* the fire/don't-fire corpora above, plus one assert that a message containing both an insult
and a supported link still delivers the link.
*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **Nothing OS-specific in `bot.py`.**
3. **No new dependency.** Standard library only.
4. **Secrets never enter git**; no message bodies in any file.
5. **Code, comments, commits and docs in English; everything the group reads is Spanish.**
6. **Mark deliberate shortcuts with `ponytail:`** naming the ceiling and the upgrade path.

## STANDING RULES

- Ship the check with the change. `python -m py_compile bot.py` and `python bot.py --self-check`
  pass before every commit.
- **Mutation-test both slices** and re-run the must-stay-red entries that live in the code you
  touched. Ship the table.
- `git status` before every commit; explicit paths; never `git add .` or `-A`.
- You have no token; the live layer is mine.

## CHECKPOINT

Stop after slice 2 and report:

1. What you ran, with output — especially the mutation table and the fire/don't-fire corpora.
2. Which site signal you chose in slice 1, and how a Facebook `share/v/` link behaves under it.
3. The similarity threshold you chose, what it does to your near-miss list, and what would make you
   move it.
4. Anything in this order that turned out to be wrong on fact.
5. Every `ponytail:` left, and everything that needs my live layer.
