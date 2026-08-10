# PROMPT-ORDER 13 — the two ways one message can hang the bot forever

> Self-contained. Written 2026-08-10 17:10 -03, in an AUTO run with a **1 h time box closing 18:01**.
> **Re-verify every claim below against the live repo.** Reports are discovery-grade and they drift.
> Every order on this project has been wrong about something, and the agent catching it is this line
> working — not the agent being difficult.

## CONTEXT

Read `AGENTS.md`, then `README.md` §1, §4 and §5.

Branch from current `main` (`0651e21`, order 11 merged: a transport failure buys one retry, and an
unsupported media host now gets an answer). **No other track is in flight** — you own `bot.py` for
this order.

The owner asked for *"más controles, asegurar funcionalidad, safeties"*. I went looking for places
where **one message can cost unbounded work**, because that is the only class of defect here that
takes the bot down rather than making it answer badly. I found two. What follows is my discovery
pass, with its method and its blind spot stated so you can see past it.

### Finding 1 — nothing in this file has ever heard of a live stream

```
$ grep -c "is_live" bot.py        →  0
$ grep -c "is_image_post" bot.py  →  22     # control arm: the grep does work
```

`_ydl_options` sets `format`, `outtmpl`, `merge_output_format`, `noplaylist`, `playlist_items`,
`socket_timeout`, `retries`, `quiet`, `noprogress`, `no_warnings`, `logger` — and **no bound of any
kind on what comes back**: no `max_filesize`, no `match_filter`, no duration cap. Read it yourself and
confirm the list; I read one function and it is the only place I looked.

`download_into` calls `_extract(url, target_dir)`, which goes straight to
`ydl.extract_info(url, download=True)`. There is a metadata-only path in this file
(`_simulate_options`, `ignore_no_formats_error` + `simulate`) but it is only reached by the image-post
fallback, **not before a normal download**.

So: paste a YouTube (or Twitch, now that order 11 added those hosts to the *unsupported* reply — check
which list actually applies) **live** URL and yt-dlp starts writing the stream to a temp dir and does
not stop. Not slowly — a live stream has no end. On a friend's laptop hosting for the afternoon that
is a disk filling up and a bot that answers nothing else, because delivery here is serial.

**I have not reproduced this.** I refused to start an unbounded download on the machine that is
currently hosting production. That makes it a *sized risk from a read*, not a measured defect, and
**your first job is to decide whether it is real** — see the slice.

### Finding 2 — one message, unbounded downloads

In `_handle_links`:

```python
supported = [url for url in urls if is_supported(url)]
...
for url in supported:      # no cap, nowhere
```

Measured earlier in this run: **three links in one message took 26 s, serial.** Twenty links is
therefore minutes of a bot that answers nothing else — including the insult reply and `/instalar`.
Forwarding a message full of links is an ordinary thing for this group to do.

## WHY IT MATTERS

Every other failure in this project makes the bot *say the wrong thing*. These two make it **stop
being a bot**, and the owner cannot see why, because the one cause the bot can never report is its own
silence. A hard bound costs a few lines and removes the whole class.

## TERRITORY

Your own worktree, branched from `main`. You may change **`bot.py`**, `README.md`, `AGENTS.md`.

**Do not touch:** `run-bot.command`, `run-bot.cmd`, `instalar-bot.cmd`, `requirements.txt` — **no new
dependency** — `.gitignore`, `EMPEZAR-ACA.md`, `docs/**` other than this file's own checkbox notes.

**Escape hatch:** if the change you need genuinely lands outside that list, **STOP and report it.**
Never satisfy this boundary by weakening something else — no swallowed exception, no default that
hides the gap, no required thing made optional.

**The bot is LIVE and I am hosting it from the main tree.** Consequences that bind you absolutely:
**never call `getUpdates`, never start a second bot instance** — Telegram allows one poller and the
live one is mine. `.env` does not exist in your worktree and you do not need it. You get **no live
surface**: deterministic checks only. The live layer is mine, after the merge.

## THE WORK

### Slice 1 — a live stream never starts downloading

**First, settle whether the risk is real, cheaply and without downloading anything.** `--simulate` (or
`extract_info(..., download=False)`) on a currently-live URL tells you what the metadata says, at zero
bytes of media. Finding a live URL is on you; YouTube's own live listings are the obvious source, and
whatever you use, **paste the URL and the observed field values into your report** — a claim about
`is_live` with no observation behind it is worth nothing to me.

Three outcomes, all acceptable, and I want whichever one the measurement supports:

- **The metadata marks it** (`is_live`, `live_status`, or whatever the field actually turns out to be —
  do not trust my names). Then bound it: yt-dlp already has `match_filter`, which runs during
  extraction and rejects **before** the download starts, with no extra round trip. Prefer that over a
  hand-rolled pre-flight pass for exactly that reason. Verify it actually fires on the download path
  with the formats `MEDIA_FORMAT` selects — do not assume it, because this file already has one scar
  from a format field being unknown where you would expect it (`height` on Instagram, hence the `?` in
  `MEDIA_FORMAT`; see §4.1).
- **The metadata does not mark it**, and the only bound available is a size or duration ceiling. Then
  say so and take that instead — a ceiling is a worse instrument (it also fires on a long ordinary
  video) so it needs its own decision about what happens at the boundary.
- **The risk is not real** — yt-dlp already refuses, or terminates on its own. Then **write that down
  with the evidence and build nothing.** That is a complete and valuable outcome for this slice, and I
  would rather have it than a guard against a fiction. Go straight to slice 2.

If you do build the guard: the refusal is a **named failure**, not a generic apology. Spanish, in the
existing voice, no jargon — the friend needs to know their link was not a thing this bot can send, not
what `is_live` means. Follow how `FAILURE_SIGNATURES` and the unsupported-host reply already word
theirs; do not invent a fifth tone.

*Check:* deterministic, driven by a **fake** — an info dict / a fake ydl that reports live, and a
control that reports not-live and must still be accepted. **Do not write a check that needs a real
live URL**: it goes red the day that stream ends, and this project already refused that trade once
(README §5.2). **Do not add a seventh real download to `--self-check`.**
*Commit.*

### Slice 2 — a message costs bounded work

Cap how many links one message can spend. Pick the number yourself and defend it in one line: the
group averages ~20 links a **week**, three links measured 26 s, and the cap exists to stop a forwarded
wall of links, not to punish someone pasting two.

- **The group must be told**, once, what was not done and why — same one-reply-per-message discipline
  `_handle_links` already uses for the unsupported-host case (read that comment; it explains why one
  reply and not one per link). Spanish, in voice.
- **Order matters: deliver the first N**, do not refuse the whole message. A friend who pasted six
  links wants the first ones, not a lecture.
- The links you dropped are the kind of thing the ledger exists for. Decide whether they earn a
  record, keep it cheap, and **never put a message body in any file** — that rule is absolute here.

*Check:* asserts the cap holds at the boundary — N delivered, N+1 dropped, exactly one reply for the
message. Driven by fakes; no network.
*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **Nothing OS-specific in `bot.py`.**
3. **No new dependency.**
4. **Secrets never enter git; message bodies never enter any file.**
5. **Code, comments and docs in English; everything the group reads in Spanish.**
6. **`ponytail:` on every deliberate shortcut**, naming its ceiling and the upgrade path.
7. **A confident wrong message is worse than the generic one** — the standing rule from order 04. If a
   signal cannot tell two causes apart, hedge like the Facebook row does.

## STANDING RULES

- `python -m py_compile bot.py` and `python bot.py --self-check` **both pass before every commit.**
- **Commit each slice the moment it is green.** The order-11 agent died to a stall watchdog with its
  slice complete and **uncommitted**; I salvaged it by hand. An uncommitted slice dies with you.
- **Mutation-test both slices and ship the table.** For slice 1 the mutation that matters is the guard
  always firing — an ordinary video must not be refused as live. For slice 2: the cap off by one in
  both directions. Re-run any must-stay-red entry in `AGENTS.md` you touched.
- Explicit paths only. Never `git add .` or `-A`.
- **Write your foundation delta for `AGENTS.md` as a small diff in your report**; `README.md` you may
  edit directly (§4 measured facts, §5.2 named failures).

## CHECKPOINT

Stop and report after slice 2. Include:

1. What you ran, and the mutation table.
2. **The live-stream measurement**: the URL, the fields you observed, and which of the three outcomes
   you took. If you built no guard, say why in a way I can check.
3. The cap you chose and the one-line defence.
4. **Anything in this order that is wrong on fact.** Both findings are reads, not reproductions;
   finding 1 is the one I most expect to be wrong.
5. Every `ponytail:` left, and anything that needs my live layer.
