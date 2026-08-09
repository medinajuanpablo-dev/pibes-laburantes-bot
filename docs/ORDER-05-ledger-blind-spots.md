# PROMPT-ORDER 05 — the two things the ledger cannot see

> Self-contained. Written 2026-08-09 against a bot running in production.
> **Re-verify every claim below.** Every order on this project has contained at least one claim the
> implementing agent correctly refuted; that is the outcome I want, not a problem.

## CONTEXT

Read `AGENTS.md` first, then `README.md` §1, §5 and §5.1.

The owner asked for this, in his words: *"cada vez que no pueda bajar un link lo mapee en algún lado
para que después te pueda pedir que analices todos los rebotados y arregles."* The ledger that
shipped for that request covers **less than he asked for**, and the gap is exactly the part that
would drive the roadmap.

### What I measured, running the bot's own helpers

```
un link de youtube normal        urls=1 supported=1  -> reaches _deliver, ledger covers it
link SIN esquema (youtu.be/x)    urls=0 supported=0  -> returns early, NOTHING in the ledger
host no soportado (tiktok.com)   urls=1 supported=0  -> returns early, NOTHING in the ledger
mensaje sin ningún link          urls=0 supported=0  -> returns early, and that is correct
```

`record_rejection` is called from exactly two places, both inside `_deliver` — so **only a link the
bot accepted and then failed on is ever written down.** Two classes are invisible:

1. **An unsupported host.** Somebody pastes TikTok, X, Reddit, Twitch. The bot ignores it and logs
   one INFO line to a runtime log that dies with the process. **This is the single most valuable
   signal the project could collect** — "the group pasted TikTok eight times this week" is what
   decides which site to support next, and right now that evidence evaporates every time a friend
   closes the window.
2. **A link with no `http(s)://`.** Telegram itself renders `youtu.be/xyz` as a clickable link;
   `URL_PATTERN` requires a scheme, so the bot never sees it. `AGENTS.md` already lists this under
   *Open, known* with `message.entities` as the upgrade path.

The fourth row is not a gap: a message with no link in it is not a bounced link, and recording those
would drown the ledger in ordinary chat.

### A third blind spot, found by the previous agent and confirmed by me

**Every failing YouTube link loses its real diagnosis before it reaches the ledger, and burns 38
pointless network requests on the way.** The mechanism, measured: an unavailable YouTube video comes
back from the fallback probe with **no formats and 38 thumbnails**, so `is_image_post()` returns
True, `_download_best_thumbnail` fetches all 38, downloads nothing usable, and **its own error
replaces yt-dlp's**. What lands in the ledger is the bot's sentence
(`has no video and no downloadable image either`) instead of Instagram-style prose naming the cause.

This also refuted a claim that was written in `bot.py`'s own docstring — *"a failed extraction never
reaches this function at all"* — which had been measured on Instagram only and does not generalise.
That docstring is already corrected on `main`; the behaviour is not.

**Fix it in this order, as slice 0, before the other two** — it is the smallest of the three and the
other two are worth less while the ledger is recording the wrong cause for a whole site. The guard
you need is a discrimination `is_image_post()` currently cannot make: *no video formats* is not the
same as *this is an image post*. Find a signal that separates them — 38 thumbnails and no image is
not an image post — and make sure the original extraction error survives to the ledger.

**Do not weaken the existing must-stay-red guard** (`formats` non-empty ⇒ not an image): that one
protects a video whose formats failed from degrading into its poster frame, and it stays.

## WHY IT MATTERS

The ledger is not a log, it is the input to a decision the owner will make later. A ledger that
records only the failures the bot already understands tells him nothing he did not know. The
failures it *cannot* see are the ones that would change the product.

## TERRITORY

Your own worktree, branched from current `main`. You may change:

- **`bot.py`** — both slices.
- **`README.md`, `AGENTS.md`** — the documentation, including moving the schemeless-link entry out
  of *Open, known* if slice 2 closes it.

**Do not touch:** `EMPEZAR-ACA.md` (argue it if you think a friend needs to know), `docs/**` beyond
the routing, the launchers, `requirements.txt` — **no new dependency** — or `.gitignore`.

**The bot is running in production from the main tree.** Do not start an instance. **Never call
`getUpdates`** — Telegram allows one poller per token and you would steal the live poll.
`.env` does not exist in your worktree and must not.

**Escape hatch:** if a needed change falls outside this, STOP and report. Never satisfy the boundary
by weakening something.

## READ FIRST

- `AGENTS.md` in full, especially the must-stay-red mutation list. Break none of them.
- `on_message`, `find_urls`, `is_supported`, `record_rejection`, `rejection_record`,
  `format_rejections` in `bot.py`.
- `README.md` §5.1 — the ledger's existing shape and its stated fragmentation limitation.

## THE WORK

Two slices. **They differ in how provable they are, and slice 1 is the one that is fully provable —
do it first and commit it before starting slice 2.**

### Slice 1 — record the links the bot declined to try

When a message contains one or more URLs and **none** of them is on a supported host, write that to
the ledger instead of only to the runtime log.

- Give it its own error class, distinct from a real download failure, so
  `format_rejections` groups it separately and the owner can see "these bounced" and "these were
  never attempted" as different piles. They lead to different actions.
- Record **every** rejected URL from that message, not just the first: which sites recur is the
  whole point.
- Keep the existing INFO log line as it is.
- **Do not record a message with no URL at all.** That is ordinary chat and it would bury the signal.
- The existing privacy rule holds without exception: **the message body never enters the ledger**,
  only the URLs.

*Check:* asserts over the record-building for a message with one unsupported URL, several, a mix of
supported and unsupported (which must NOT be recorded as unattempted, because the supported one was
attempted), and none at all. Plain values, no network.
*Commit.*

### Slice 2 — see the links Telegram sees

Use `message.entities` as an **additional** source of URLs, unioned with the existing regex, so a
schemeless `youtu.be/xyz` — which Telegram itself linkifies — is both delivered when supported and
recorded when not.

- **Union, never replace.** The regex works today; it stays. Adding a source must not change the
  result for any input that already works, and de-duplication has to survive the same link arriving
  from both sources.
- Telegram marks auto-detected links with an entity type of its own and explicitly-linked text with
  another; **read which types exist and pick deliberately**, and say in the commit message which you
  took and why. A `text_link` (hidden URL behind display text) is a different thing from a bare
  auto-detected `url` and you should decide whether the bot honours it — I have not decided for you.
- Entity offsets are in **UTF-16 code units**, not Python characters. A message containing an emoji
  before the link will slice wrong if you index the string naively. This is the trap in this slice.
  Prove you handled it.

**What you cannot verify, and must not pretend to:** I have no way to post a schemeless link into
Telegram as a human, so **nobody has confirmed end to end that Telegram actually sends the entity we
expect**. Your asserts can only drive constructed `Message` objects. Say so plainly in your report,
and mark it in `AGENTS.md` as verified-by-construction-only until a real paste confirms it. If you
find during the work that the entity does not carry what this order assumes, that is a finding —
report it rather than working around it.

*Check:* asserts over URL extraction from constructed messages: scheme-carrying only, schemeless
only, both (de-duplicated), and one with a multi-byte emoji before the link to prove the UTF-16
offset handling. No network.
*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **Nothing OS-specific in `bot.py`.**
3. **No new dependency.**
4. **Secrets never enter git**, and the message body never enters the ledger.
5. **Code, comments, commits and docs in English; everything the group reads is Spanish.**
6. **Mark deliberate shortcuts with `ponytail:`** naming the ceiling and the upgrade path.

## STANDING RULES

- Ship the check with the change. `python -m py_compile bot.py` and `python bot.py --self-check`
  pass before every commit.
- **Mutation-test both slices** and re-run enough of `AGENTS.md`'s must-stay-red list to prove you
  broke nothing — especially the ignore-logging ones, which slice 1 sits directly on top of. Ship
  the table.
- **Do not add a self-check download.** Both slices are string and object work.
- `git status` before every commit; explicit paths; never `git add .` or `-A`.
- You have no token and the live bot is not yours to touch.

## CHECKPOINT

Stop after slice 2 and report:

1. What you ran, with output — especially the mutation table.
2. Which entity type(s) you took, which you rejected, and why.
3. How you proved the UTF-16 offset handling, and what broke before you did.
4. Anything in this order that turned out to be wrong on fact.
5. Every `ponytail:` left, and everything unverifiable without a real human paste.
