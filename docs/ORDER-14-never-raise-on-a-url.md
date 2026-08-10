# PROMPT-ORDER 14 — the bot prints its own token, and one bad URL costs a whole message

> **Slice 0 is the severe one and it was found after the rest of this order was written.** Do it first.

> Self-contained. Written 2026-08-10 17:15 -03. **Re-verify every claim against the live repo** — this
> order is built on a probe I wrote myself, and roughly half of this project's false findings have been
> my instrument rather than the code. The control arm is described below so you can re-run it.

## CONTEXT

Read `AGENTS.md`, then `README.md` §4.6 (how a message becomes a list of links) and §5.

**Branch point, corrected 17:36:** branch from current `main`. Order 13 **shipped nothing** — its agent
was killed by an OS-level permission lockout with two additive, un-gate-verified constants left
uncommitted in its own worktree, which is preserved and is not yours. So there is no in-flight track
and `main` is the branch point. The earlier "branch after order 13 merges" line was written when that
merge was expected.

**Scope history — read this before anything else:**

- **Slice 0 is DONE and merged** (the token no longer reaches the log). Do not redo it, do not touch
  `configure_logging` or `REQUEST_URL_LOGGER`. If you think slice 0 is wrong, say so and stop.
- **Scope for THIS dispatch: slices 1, 2 and 3.** Branch from current `main` — which is now well past
  the SHA below, because order 15 (the live-stream guard) landed in between. **Run
  `git merge --ff-only main` first and report what it moved**: every worktree handed out on this project
  so far has been several commits behind while its order said "current `main`".

### The defect, measured

I built a fake `Message` carrying two Telegram URL entities — a real reel and `https://[::1/x` — and
ran exactly what `_handle_links` runs:

```
CONTROL urls: ['https://www.instagram.com/reel/DbGNFqVKnB-/']      # control arm: fires
CONTROL supported: ['https://www.instagram.com/reel/DbGNFqVKnB-/']

SCENARIO urls: ['https://www.instagram.com/reel/DbGNFqVKnB-/', 'https://[::1/x']
SCENARIO RAISES ValueError: Invalid IPv6 URL
```

The control arm passed, so the probe fired and the finding is not vacuous. The chain:

1. `message_urls` unions `find_urls` (regex over the text) with `entity_urls` (whatever Telegram marked
   as a URL entity).
2. **`entity_urls` validates nothing** — it strips trailing junk and prefixes a scheme when `"://"` is
   absent. Read it and confirm.
3. `is_supported` calls into `urlsplit`, which raises `ValueError: Invalid IPv6 URL` on a malformed
   bracket literal.
4. `_handle_links` builds `supported = [url for url in urls if is_supported(url)]` **outside any
   `try`** — so the raise escapes to `on_error`, and **the good reel in that same message is never
   delivered.** The group gets silence, which is the one failure mode this project has spent two orders
   eliminating.

**What I could NOT measure:** whether Telegram's own client-side parser ever marks bracket-malformed
text as a URL entity. That needs a human pasting into the real group, and the exercise contract already
lists the entity path as out of reach without one. **Do not treat that gap as a reason to skip this** —
the guard below covers *any* unparseable URL from *any* source, so its value does not rest on that
question. But do not overclaim it in a comment either: say what was measured.

## WHY IT MATTERS

Everything shipped in orders 04, 09 and 11 was about the bot never going quiet on a link. This is a
path where it still does, and it takes an innocent link down with it.

## TERRITORY

Your own worktree. You may change **`bot.py`**, `README.md`, `AGENTS.md`.

**Do not touch:** the launchers, `instalar-bot.cmd`, `requirements.txt` (**no new dependency**),
`.gitignore`, `EMPEZAR-ACA.md`, `docs/**`.

**Escape hatch:** if the fix needs something outside that list, **STOP and report.** Never buy
compliance by weakening an invariant — no bare `except:`, no default that hides the gap.

**The bot is LIVE and the CEO hosts it from the main tree**: never call `getUpdates`, never start a
second instance. No `.env` in your worktree and you do not need one. **No live surface** — deterministic
checks only; the live layer is the CEO's after merge.

## THE WORK

### Slice 0 — the bot prints its own token about 360 times an hour

**Measured on the live process I am hosting right now**, `/private/tmp/bot-live.log`:

```
2026-08-10 16:39:25 INFO HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getMe "HTTP/1.1 200 OK"
...
208 lines containing the literal token, between 16:39:25 and 17:14:11  →  ~360/hour, ~8600/day
```

The mechanism, verified by grep — **re-verify it, this is my read**:

- `logging.basicConfig(level=logging.INFO, ...)` (near line 2417) sets **INFO on the root logger**, which
  switches on `httpx`'s own request log.
- httpx logs the **full URL**, and every Telegram API URL embeds the token:
  `https://api.telegram.org/bot<TOKEN>/getUpdates`.
- `grep -n "httpx" bot.py` finds only comments. **Nothing silences that logger.**

**Why this is the most serious thing in this order.** Design law 4 says secrets never enter git and
never enter a chat message. Both hold — I checked, the token is not in git. **The log is a third
channel nobody covered**, and it is the one a non-technical friend will hand over: the launcher does
not redirect stdout, so on their machine those lines go to the visible Terminal window, ~6 per minute
forever. The single most likely support request in this project's life is *"mirá, no anda"* with a
screenshot or a paste of that window. That paste is full control of the bot: read every message in the
group and post as the bot.

My own contribution, stated so you can discount it: **the file path is mine** — I redirected stdout
when I took over hosting, and it was world-readable (`-rw-r--r--` in `drwxrwxrwt /private/tmp`) until I
chmodded it to 600 at 17:14. The leak is not the redirect, it is the bot writing the token to stdout;
the redirect only made it durable. The friends' path leaks it to a screen instead of a file.

The work: **the token must not appear in the bot's output at all.**

- The cheapest correct fix is silencing the offending logger, not filtering strings. **Verify which
  logger actually emits it** (`httpx` is my read from the line format — confirm it) and set its level so
  the URL lines stop, **without silencing anything the bot itself logs.** The launcher's window is the
  only diagnostic a friend has: deliveries, failures, the conflict lines and the retry line must all
  still appear. Check that they do.
- **A redaction filter is the wrong shape here** and I do not want it: it has to be right about every
  future URL format, and being wrong is silent. Silencing a logger fails closed. If you disagree after
  measuring, say why rather than building it.
- Confirm the bot still *works* after the change — the self-check exercises the real API, so a
  mis-scoped `setLevel` that breaks the client will show up there.

*Check:* assert that the logger which prints request URLs is configured above INFO after the bot sets
logging up, **and** that `the-bot`'s own logger still emits at INFO. Both halves matter: a check on only
the first passes if you silenced everything. If you can drive one real API call in the existing
self-check harness and assert the token is absent from what it wrote, better — but do not add a seventh
download for it.

*Commit.* Then note in `README.md` §5 that the launcher window no longer shows the token, because
"is it safe to send you my window?" is a question a friend will actually ask.

### Slice 1 — `is_supported` answers the question instead of raising

`is_supported(url) -> bool` is asked *"is this a link I handle?"*. For a string no URL parser can
parse, the honest answer is **no**, not an exception. My recommendation is to make that true at the
source rather than wrapping every call site — one guard, and every present and future caller inherits
it. **Verify that recommendation before taking it**: find every caller (`grep`) and check none of them
depends on the raise to mean something. If one does, say so and put the guard where it belongs instead.

- The narrowest catch that covers the measured failure, not a bare `except`. Name the exception type.
- **Nothing gets logged at ERROR for this.** An unparseable URL is not a failure of the bot; it is a
  string that was never a link. Match how the existing "not my site" path stays quiet (§4.6 explains
  why silence is the default there).
- `ponytail:` comment naming what the guard does and does not cover.

*Check:* asserts `is_supported` returns `False` — not raises — for the malformed IPv6 literal, **and**
still returns `True` for a real reel and `False` for an ordinary news URL. Those last two are the
control arm; a check without them can pass while the function returns `False` for everything.
*Commit.*

### Slice 2 — decide whether one bad link may still cost the rest of the message

Slice 1 fixes the classification step. The question this slice answers is whether the *delivery* loop
has the same shape of exposure: `for url in supported:` — if one URL raises something unexpected mid
loop, do the remaining links still get their turn?

**Measure before building.** Read the loop and its existing `try`/`except` structure. Two acceptable
outcomes:

- **It is already isolated per URL** (each iteration's failure is caught and recorded) → **write that
  down with the line numbers and build nothing.** That is a complete answer and I want it if it is true.
- **It is not** → give it the isolation, and the failure must be **recorded in the ledger like any
  other**, never swallowed. A silent `except` here would be worse than the crash, because the crash at
  least shows up in the log.

*Check:* if you build anything — one bad URL in a list of three, and the other two still deliver, with
the failure recorded. Driven by fakes; no network.
*Commit.*

### Slice 3 — the mutation entry

`AGENTS.md`'s must-stay-red list has several `entity_urls` rows and **none** for this: an unparseable
URL from an entity crashing the handler. Add the entry for whatever you built, worded like its
neighbours. If slice 2 built nothing, only slice 1 gets an entry.

*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **No new dependency.**
3. **Secrets never enter git; message bodies never enter any file.** A rejected URL may be recorded; the
   text around it may not.
4. **Code and comments in English; everything the group reads in Spanish.**
5. **A confident wrong message is worse than the generic one.**
6. **Never hide a failure to satisfy a boundary.** This whole order is about an exception that was too
   loud; the wrong fix is one that makes real failures too quiet.

## STANDING RULES

- `python -m py_compile bot.py` and `python bot.py --self-check` **both pass before every commit.**
- **Commit each slice the moment it is green.** An earlier agent on this project died with a finished
  slice uncommitted and it had to be salvaged by hand.
- **Mutation-test slice 1 and ship the table.** The mutation that matters: the guard widened until
  `is_supported` returns `False` for a real reel — that passes a badly-written check and breaks the
  entire product. Your check must catch it.
- Do not add a seventh real download to `--self-check`.
- Explicit paths only. Never `git add .` or `-A`.

## CHECKPOINT

**This order has been dispatched twice with different scope — report against the slices you were
actually given, not the whole list.**

*If you were scoped to slice 0 only* (the 2026-08-10 17:36 dispatch): what you ran; the mutation table,
whose load-bearing entry is **the fix widened until it silences the bot's own logger too**; **which
logger actually emits the token line, verified rather than assumed**; proof that all five of the bot's
own startup and field lines still appear at INFO; anything in this order wrong on fact; every
`ponytail:` left.

*If you were given slices 1-3*: the above, plus every caller of `is_supported` and whether any relied on
the raise, and slice 2's measurement with which of its two outcomes you took.

---
*Self-audit 6/6 on this order, 17:41 — one finding, fixed here: the CHECKPOINT asked for slices 1-3
material after the dispatch was scoped to slice 0 only, so a slice-0 agent would have read its own
report as incomplete. The title and the scope line were already re-read and are consistent.*
