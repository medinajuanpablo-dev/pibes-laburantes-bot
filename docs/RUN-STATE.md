# CEO loop-state — Telegram meme bot

## Run
- **Mode:** **AUTO con caja de tiempo — 1 h**, re-invoked **2026-08-10 17:01 -03**, no `ask`.
  **`Deadline: 2026-08-10 18:01 -03`** — measured once with `date`, never re-estimated. Crossing it
  triggers *el aterrizaje* (5 steps), not a cut. Owner's direction this round: *"mejoras netas de valor
  para el bot: más controles, asegurar funcionalidad, safeties."*
  Previously AUTO open-ended since 2026-08-09 17:15; GOAL from 2026-08-07 14:33.
- **Docs loaded (re-stamp after compaction, 2026-08-10 17:01):** modes ✓ · continuation ✓ ·
  run-loop ✓ · dispatch ✓ — all four re-read from disk this round, after a compaction. `audit.md`
  is re-read before the verdict, per the interrupt table.
- **Reconciled doc vs repo at entry:** the doc ended at rounds 3-7 while `main` had advanced through
  orders 09, 10 and 11. Fixed below rather than acted on stale.
- **I am the live host.** The user went AFK at 17:15 and handed hosting to me: the bot runs from the
  **main tree** on the merged tip. Consequences that bind every agent from here on: **no second
  instance, no `getUpdates` from anywhere** (it steals the live poll), and the main tree is a live
  surface, not just a working copy.

### AUTO round 1 — closed the last unproven acceptance criterion
**Criterion 4 (oversize → direct link) is now PROVEN LIVE.** I drove the project's own `_deliver`
against a real 73 MB YouTube video (`eRsGyueVLvQ`, 888 s) with a `Message` from the real group:
`_deliver` returned without raising and the ledger recorded
`OversizeForTelegram · 73564500 bytes as video, over the 52428800-byte ceiling`. Prediction was
written before the run and matched on all four points. **Every acceptance criterion of the original
GOAL is now verified live.**

**And I corrected an error of my own from the previous round.** I had called the album path "proven,
just wire it" on the strength of a `curl sendMediaGroup`. The implementing agent showed that curl
bypasses python-telegram-bot, where the actual defect lived (`InputMediaPhoto(Path(...))` serialises
to `file:///…` and uploads nothing). I then drove the project's real `_send_album` against the live
group with 10 slides — it returned clean against the cloud API, which rejects `file:///` with no
attachments, so the path is now genuinely exercised. **My curl proved Telegram accepts an album; it
never proved the bot sends one. Different claims.**


## AUTO round 1 — hunt log
| What I ran | Predicted | Observed | Where it went |
|---|---|---|---|
| Drove `_send_album` (the project's own function, not curl) with 10 carousel slides against the live group | The agent says PTB serialises a `Path` to a `file://` URL and uploads nothing -- so either it raises, or my earlier "proven" claim was hollow | Returned clean against the **cloud** API, which rejects a `file://` media with no attachments, so real uploads happened and the path is exercised | Closed the gap the agent found in my own claim |
| Drove `_deliver` with `youtube.com/watch?v=eRsGyueVLvQ` (888 s, ~73 MB at this format) | Downloads it all, `delivery_decision` returns `"link"`, Spanish oversize reply, one ledger record | All four confirmed | **Acceptance criterion 4 closed** |
| Read the ledger after ~5 min of real hosting | Empty -- I only just took over | **A real field bounce I did not cause**: an Instagram reel posted by the group at 17:17 failed with *"This content isn't available to everyone"* | Order 04 |
| Probed 6 failure classes across the three sites with `--simulate` (restricted / nonexistent / profile-URL / deleted / private / dead) | Each class has a distinct signature worth mapping to a specific reply | 5 distinct signatures, and **two collisions**: YouTube deleted and private are *both* `Video unavailable`; Facebook dead is `Cannot parse data`, which an earlier agent also saw under **rate limiting** | Order 04, with an explicit instruction not to claim certainty the signature does not carry |
| Read the raw ledger JSON | Clean text | **ANSI colour escape sequences** from yt-dlp leaking into the ledger detail | Order 04 slice 1 |

## AUTO round 1 — decisions
| # | Decision | Why | Reversible? |
|---|---|---|---|
| 5 | Next frontier is **naming the cause of a bounce**, not more hardening | Portfolio check: the last four landings were all capability (image posts, carousels, launchers, ledger), so the run is not drifting into hardening. This one is capability too -- it changes what the product *tells* a friend, which is the whole UX at 20 links a week. | Yes |
| 6 | Map **only measured signatures**, and hedge the two ambiguous ones | `Video unavailable` fires on deleted *and* private; `Cannot parse data` fires on dead *and* throttled. A confident wrong message is worse than the generic one. | Yes |
| 7 | Did **not** add a seventh self-check download for the new failure paths | The signatures are strings. A check that needs a broken remote post breaks when someone deletes it. | Yes |

## Standing sections (current, not history)

### Exercise contract — rewritten 2026-08-09, the old one was false
| Qué | Cómo |
|---|---|
| Run command | `set -a; . ./.env; set +a; .venv/bin/python bot.py` from the main tree. No port -- long-polling client. |
| **Live surface** | The real bot in the real Telegram groups. **I am hosting it**, so the main tree is a live surface. |
| **The instrument for a path with no human poster** | Import `bot`, build a `Message` with `app.bot.send_message(chat, ...)`, then call the project's own function (`_deliver`, `_send_album`). This is how criterion 4 and the album path were finally proven. **It is the only way to exercise delivery without a human**, because a bot cannot see its own or another bot's messages. |
| **What that instrument CANNOT reach** | Anything that starts from a *human* paste: schemeless links, `message.entities`, the 409 hand-over between two hosts, and whether an album *renders* as an album. Those wait for the user. |
| Gates | `python -m py_compile bot.py` and `python bot.py --self-check` (six real downloads, ~1 min) |
| **MINE — disposable** | group `Pruebabot` (-5493469970), temp download dirs, the scratchpad, worktrees, `rejected.jsonl` |
| **THEIRS — hard-stop** | the real group `Sindicato de Pibes que Laburan` (-1002462983768) for *probes*; the user's Telegram account; their gh account |
| **Hard bar for every agent from now on** | **Never call `getUpdates` and never start a second bot** -- Telegram allows one poller and the live one is mine. |
| Known traps | `timeout(1)` absent on this Mac. `python3` on PATH is Anaconda 3.11.7; `/usr/bin/python3` is 3.9.6. yt-dlp warns about a missing JS runtime and works anyway. Facebook rate-limits after ~5 self-check runs in 25 min and the symptom is `Cannot parse data`, indistinguishable from a dead post. |
| **Trap measured 2026-08-10 17:15** | **macOS TCC can revoke the whole project mid-run** — `stat` keeps working while every `open()`/`listdir()` returns `EPERM`, for the CEO *and* every agent, **and it reproduces with the sandbox disabled** so it is not the Claude sandbox. Owner clears it with Full Disk Access. **`/private/tmp` stays writable**: write the handoff there first, then copy it in. The live bot is unaffected — its handles predate the lockout. |
| **Trap measured 2026-08-10** | **A live link is an unbounded download**: ~6 MB/min forever, and `temp_workspace`'s cleanup never runs because it is a `finally` on a `download_into` that never returns. Probe it only in a child process with a hard `SIGKILL`. |
| **Trap in the field names** | `was_live` is `True` for a **finished** stream, which is an ordinary bounded video and must still be delivered. A live guard keyed on it silently rejects every replay. Use `is_live` or `live_status == 'is_live'`. |
| **The bot`s log** | `/private/tmp/bot-live.log` (my redirect). **Clean since order 14 slice 0** — 0 token lines. The pre-fix log is kept at `/private/tmp/bot-live-pre-fix.log`, mode 600, and DOES contain the token. **Liveness is now `pgrep` + `lsof -p <pid> -i -a | grep -c ESTABLISHED` (expect 2)** — counting `getUpdates` lines no longer works, they were the httpx log. |

### Acceptance criteria of the original GOAL — all seven verified
Criteria 1-3 and 5-7 were closed during the GOAL session (`run-history/01-goal-session.md`).
**Criterion 4 (oversize -> direct link) closed 2026-08-09 in AUTO round 1**, the last one outstanding.

### Queues
| Kind | Item | Trigger that would promote it |
|---|---|---|
| Idea, deferred | Parallelise multiple links in one message | **Measured 26 s for three links** -- not a defect at 20 links/week, and concurrency costs real complexity in a one-file bot. Promote if the group complains about the wait. |
| Idea, deferred | Honour `img_index` on a carousel | Ambiguous whether Instagram means it as a pointer. Promote if someone reports getting 10 photos when they wanted 1. |
| Sub-task | Mixed photo/video carousels | Refused, never measured -- the only public example cited upstream is dead. Promote when a live one appears. |
| Sub-task | Windows launcher unverified | No Windows machine exists in this run. Promote when a friend runs it. |
| Idea, **offered and awaiting a call** | Keep Telegram's own backlog instead of dropping it, filtered by message age (~15-20 min), so a link posted while nobody hosted still arrives | The owner cancelled the in-memory queue because it dies with the process -- correctly. This is the version that survives that, and it needs no queue and no dependency. It re-opens a measured decision (`drop_pending_updates=True`, 7 updates queued in one real gap), so it needs his call, not mine. |
| Sub-task | Ledger fragments across rotating hosts | Accepted at this volume; `cat` is the merge. Promote if hosts multiply. |
| **Sub-task, next front** | **Order 13 slice 1 — the live-stream guard.** Everything expensive is done: the risk is measured, the `was_live` trap is found, the design is written and 22 additive lines already exist. Continue the agent **in its own worktree** (`agent-a34638c67b88f2b8a`) to keep the WIP. **Promote first**; it is the only unbounded-work hole left. |
| **Sub-task** | **Order 13 slice 2 — `MAX_LINKS_PER_MESSAGE = 5`.** Proposed, unmeasured, unbuilt. Cheap. |
| **Sub-task** | **Order 14 slices 1-3 — `is_supported` must never raise.** Confirmed defect with a passing control arm; a malformed URL from a Telegram entity costs the whole message. Order written; only slice 0 was dispatched. |
| Idea | Verify `match_filter` empirically — the same live URL *with* the guard, expecting **0 bytes**. Currently read from yt-dlp's source, never run. | Do it the moment slice 1 lands; it is the non-vacuity proof for that guard. |

### Standing re-read triggers
- after a compaction or a gap -> `~/.claude/skills/ceo/references/run-loop.md` (Cold resume) + `continuation.md`
- before the next verdict -> `audit.md` · before the next order -> `dispatch.md`
- closed history -> `docs/run-history/01-goal-session.md`

## AUTO round 2 — the baton pass was broken, and the fix is verified end to end

**The defect.** Nobody had ever run two instances at once; the conflict logic was only ever asserted
as a pure function. Ran the real experiment: **both hosts quit and the group was left with no bot**,
each telling its user the other one had it. Cause is structural -- `getUpdates` designates no winner,
so two identical instances see identical evidence and reach the identical wrong conclusion. The
asymmetry had to be injected from outside, and it already existed in the product: the launcher asks
*"¿se lo saco y lo prendo yo?"* and threw the answer away.

**The fix, verified by me on the real token, with a valid instrument:** incumbent yields and stops
with a hedged line; the taker (`--take-over`) stays alive, 19 getUpdates and climbing, zero give-up
lines; exactly one process left. The old build left zero.

### Instrument error I caught against myself
I first read `getUpdates` returning `ok:true` as *"nobody is polling"*. **It is not.** My own probe
terminates the running bot's long-poll and wins the race, so `ok:true` is the *expected* answer
whenever exactly one bot is polling and I am the second caller. The probe cannot distinguish "nobody"
from "one bot I just displaced". The earlier "nobody is polling" reading was true only because
`pgrep` independently showed zero processes. **Standing rule: liveness is `pgrep` plus the bot's own
polling counter, never my curl.**

### Other things this round measured
| What | Result |
|---|---|
| YouTube Shorts, a playlist URL, a video with `&list=` attached | All three handled. `noplaylist` + `playlist_items` cap a playlist to one item; no flood. |
| A pasted playlist that "bounced" | **Not a defect and my first reading was wrong** -- it was the oversize path firing correctly on a 120 MB item, a second independent live confirmation of criterion 4. |
| Three links in one message | 26 s, serial. Predicted 40-70 s; **my prediction was wrong in the optimistic direction.** Not promoted -- see the queue. |

### Claims of mine the agents refuted this round
- *"the 60 s grace was honoured exactly"* -- **false.** The log shows 74 s and 65 s: `CONFLICT_GRACE`
  is a floor, and PTB's backoff (capped at 30 s) decides which retry crosses it. A product line said
  "hasta un minuto" and now says "un minuto o dos".
- *"slice 0 is the smallest of the three"* -- false; it forced a rewrite of the YouTube signature row.
- *"one fix closes both the lost diagnosis and the 38 requests"* -- false; only the diagnosis.
- My rate-limit warning named Facebook; what actually failed was **YouTube, HTTP 403**.

## AUTO rounds 3-7 — what 12 hours of unattended production actually showed

I hosted the bot from 2026-08-09 23:27 to 2026-08-10 11:14. **Nobody was watching it and it did not
need anybody.**

| | |
|---|---|
| deliveries | **14** -- 11 video, 3 photo |
| real failures | **0** |
| the 3 ERROR lines in the log | all `There is no video in this post`: yt-dlp's logger on **image posts the fallback then delivered correctly**. Not failures. |
| insults answered | 2, both matching cleanly on `['bot', 'estupido']` -- no fuzzy edge case in the field |
| **site breakdown** | **instagram 14 · youtube 0 · facebook 0** |

### The decision-relevant fact of the whole run
**This is an Instagram group.** Fourteen of fourteen. In 12 hours nobody pasted a YouTube or a
Facebook link at all. Every hour spent on YouTube last night -- the format string, the bot-check
signature, the poster-frame defect -- protects a path this group did not use once. It was still worth
fixing (a poster frame instead of a video is a silent lie), but **it changes the priority of any
further YouTube work to near zero**, and it says the next capability question is "what else does
Instagram do that we do not handle", not "what other site should we add".

Recorded here rather than acted on: the ledger's `UnsupportedHost` class is what will answer the
second half, and it has no data yet.

### Landings in these rounds
| What | Verdict | Live layer |
|---|---|---|
| Named failure messages + the de-ANSI'd ledger | APPROVED | RUN -- drove `_deliver` on the real restricted reel; verified the exact Spanish for all six signatures |
| The ledger's three blind spots (YouTube diagnosis, unsupported hosts, `message.entities`) | APPROVED | RUN -- a dead YouTube link now records yt-dlp's own error |
| The baton pass fix (`--take-over`) | APPROVED | **RUN -- the two-instance experiment, the whole point.** Incumbent yields, taker survives, exactly one process left. The old build left zero. |
| Poster frame + the insult reply | APPROVED, **then a false positive I shipped** | RUN. See below. |
| `/instalar` | APPROVED | RUN -- all three variants sent to the real group, `&&` renders literally, token absent |
| `/instalar` shrunk 22 lines -> 5 | APPROVED | RUN -- re-sent live |

### My own errors in these rounds
1. **I merged a false positive into production and hosted it for ~40 minutes.** `"que estupido, bro"`
   -- an ordinary sentence -- fired. **My 26-phrase adversarial corpus contained no local slang.** The
   agent caught it after its own slice was green, and the reason is instructive: difflib scores `bro`
   and `vot` **identically** against `bot`, so no threshold can separate them. The fix is an
   enumerated exclusion list, not a number.
2. **I read `getUpdates -> ok` as "nobody is polling".** It is not: my own probe terminates the
   running bot's poll and wins. Liveness is `pgrep` plus the bot's own counter, never my curl.
3. **I ordered a file attachment for `/instalar` and the agent refused, correctly.** A double-clicked
   `.command` needs the exec bit **and** no quarantine; a download has neither, and right-click ->
   Open cannot add an exec bit. Same wall for a GitHub link -- confirmed against browser-downloaded
   files already on this Mac. Windows is the exception because a `.cmd` needs no exec bit.
4. **I overstated the install feature at ~540 lines; it was 463**, and the line ranges I cited in the
   order were wrong by ~295 lines.
5. **I chained a destructive cleanup behind a check twice**, and both times the check failed and the
   cleanup ran anyway. The second time it removed a worktree holding an agent's uncommitted docs --
   which I read before touching, and which survived. Luck, not process, twice.

## AUTO rounds 8-10 — reconciliation of what landed while the doc was stale

| Landing | Verdict | Live layer |
|---|---|---|
| Order 09 — `/instalar` shrunk to a launcher link, after the owner said my version was over-built | APPROVED | RUN — all three variants sent to the real group |
| Order 10 — Windows installs without git (`instalar-bot.cmd` bootstrap, `System32` full paths) | APPROVED | **UNVERIFIABLE** — no Windows machine exists in this run. Queued. |
| Order 11 — a transport failure buys one retry; the network failure is named; an unsupported media host gets an answer | APPROVED, **after a salvage** | RUN — forced a transport failure with a dead proxy driving `download_into` alone: exactly two attempts, 3 s pause, 3.2 s total |

**Order 12 was retired unbuilt.** I designed a deferred re-delivery queue; the owner killed it on the
right ground — an in-memory queue dies with the process, so it cannot accumulate while the bot is off,
which was the entire point. The surviving version (filter Telegram's own ~24 h backlog by message age)
is in the queues **awaiting his call**, because it re-opens a measured decision.

### The salvage, and three instrument errors of mine in one audit
The order-11 agent died to a stall watchdog with slice 3 complete and **uncommitted**. I audited the
dirty worktree on all four layers, committed it myself without authoring a character, rebased, merged.

My own errors that round, all three in my *measurement*, not in the code:
1. Forced the transport failure with a proxy that also killed Telegram, so `build_application` failed
   before the path under test ran. Fixed by driving `download_into` alone.
2. Called `is_transport_failure` on the wrapping exception instead of the inner one.
3. Filtered the log for `"again"` when the line says `"once more"` — nearly concluded the retry never
   fired. **I now read the log unfiltered before believing a negative.**

### Consultation (not a landing): free hosting
Owner asked for a free place to host. **I recommended against every cloud option**, and the reason is
not resources — measured the live process at **30 MB RSS, 0.0% CPU idle**. It is the IP: Instagram
blocks datacenter ASNs before rate-limiting, and this group is **14 of 14 Instagram**. Paying does not
fix it either, since a residential proxy costs more than the server. Verified the current free tiers
rather than reciting them: Render's free services sleep at 15 min and its background worker is paid,
Fly has no free tier for new accounts, Railway's credit is hours. Left him a 5-minute `yt-dlp -F` test
on a free Oracle VM that settles it with data instead of my reading.

## AUTO round 11 — caja de 1 h, "más controles, safeties"

**Frontier chosen: the only defect class here that takes the bot DOWN rather than making it answer
badly — one message costing unbounded work.** Everything shipped since the baton pass makes the bot
*say* the right thing; nothing bounds what one link can spend.

| # | Decision | Why | Reversible? |
|---|---|---|---|
| 8 | Order 13 = live-stream guard + per-message link cap, one agent, two slices | Both are unbounded-work holes found by reading `_ydl_options` and `_handle_links`. Fits the box; a third slice would not. | Yes |
| 9 | Ordered slice 1 to be **allowed to build nothing** if the risk is not real | I did not reproduce it — I refused to start an unbounded download on the machine hosting production. A guard against a fiction is worse than no guard. | Yes |
| 10 | Did **not** order a pre-flight size check | The oversize path already behaves correctly, just wastefully (downloads 73 MB then refuses). And a `filesize_approx` filter may be vacuous on Instagram for the same reason `height` is — unmeasured. Queued with the measurement it needs. | Yes |
| 11 | Followed the owner's hardening direction rather than re-running the portfolio argument | He named the direction explicitly this round. The portfolio is not skewed: the last landings were capability (launchers, `/instalar`, named failures, unsupported hosts). | Yes |

### AUTO round 11 — hunt log (predictions written before each measurement)
| What I ran | Predicted | Observed | Where it went |
|---|---|---|---|
| `pgrep` + orphaned temp dirs + ledger size | A crash leaks a part-downloaded video, so repeated crashes fill a friend's disk | **Wrong, in the direction that suited me.** One orphan exists, from my own kill at 11:26, and it is **0 B** — the dir is created before the download and cleaned in `finally`, so a hard kill leaves an empty directory entry, not media. Ledgers are 6 and 4 lines. | Nothing. Not a defect. |
| `is_supported` against 7 hostile hosts (fullwidth stop, dotless i, broken punycode, 300-char host, zero-width space, malformed IPv6), with an ordinary-URL control arm | The queued row says it raises on "exotic NFKC hosts" | **The queued row was wrong about the trigger.** Every unicode candidate returned `False` cleanly. The only raiser is `https://[::1/x` → `ValueError: Invalid IPv6 URL`, which is `urlsplit`, nothing to do with NFKC. | Queue row corrected; see below |
| Whether a plausible human message can even produce that URL — `message_urls` on 4 realistic sentences | It can, so the defect is reachable | **Not through the regex.** All four returned `[]` or only the good link. But reading `message_urls` showed the second source: `entity_urls` appends whatever Telegram marks as a URL **with no validation** — only junk-stripping and a scheme prefix. | Narrowed the defect to the entity path |
| The full scenario: a fake `Message` with two URL entities, one good reel and one malformed-IPv6, run through exactly what `_handle_links` does — **control arm: a well-formed entity alone must sail through** | If the entity path is unvalidated, the good reel dies with the bad URL | **CONFIRMED, control arm passed.** `message_urls` returns both, `is_supported` raises, and `_handle_links` has no `ValueError` guard. **The good reel in that message is never delivered and the group gets silence.** | **Order 14, next front** |
| Insult matcher vs 21 phrases: 7 true positives + 14 innocent rioplatense ones, including the `bro` case that shipped and the `botas`/`bote`/`boto` family | The long-word family false-positives: raw difflib scores them 0.75, 0.857, 0.857 — all above the 0.66 threshold | **Wrong. 21/21 correct.** A guard I had not read stops them: `if len(token) > len(word): continue` — a typo of "bot" is never *longer* than "bot". Already documented in `AGENTS.md` and `README.md` §4.12 **with the same scores I re-derived.** | Nothing. My candidate was already known and already handled. |

**Three of five sweeps refuted a hypothesis of mine, two of them by finding the project already
right.** The one that survived is the entity path, and it survived a control arm.

### Order 14 — mapped, ready to dispatch (blocked only on `bot.py` territory)
A URL that reaches `is_supported` from `entity_urls` can raise, and the raise costs the whole message.
The fix is a guard, not a parser: **`is_supported` must never raise** — an unparseable URL is simply not
supported. Value does not depend on measuring whether Telegram's own parser emits this shape (it
cannot be measured without a human paste, per the exercise contract), because the guard covers *any*
unparseable URL from *any* source. Also worth a must-stay-red entry: the list has several `entity_urls`
rows and **none** for a malformed URL from an entity crashing the handler.

### The find of the round — the bot prints its own token, ~360 times an hour
Found while checking that the process I host is healthy (it is: 203 polls, **zero** ERROR or WARNING
lines, gates green on the tip). The health check is what surfaced it.

| | |
|---|---|
| Measured | **208 lines containing the live token** in `/private/tmp/bot-live.log` between 16:39:25 and 17:14:11 — 34m46s, so **~360/hour, ~8600/day** |
| Mechanism | `logging.basicConfig(level=INFO)` sets INFO on the **root** logger → `httpx`'s request log turns on → httpx logs full URLs → every Telegram URL embeds the token. `grep httpx bot.py` finds only comments; nothing silences it. |
| Control arm | `git grep 8688204214` → **empty**. The token is not in git, so the existing protections work. **The log is a third channel nobody covered.** |
| Why it is severe on a friend's machine | The launcher does **not** redirect stdout, so the token prints into the visible Terminal window ~6×/minute. The likeliest support request this project will ever get is *"mirá, no anda"* plus a screenshot or paste of that window — which is full control of the bot. |
| My own share of it, stated | The **file path is mine** (I redirected stdout when I took over hosting) and it was world-readable, `-rw-r--r--` inside `drwxrwxrwt /private/tmp`, until I chmodded it 600 at 17:14. The leak is the bot writing the token to stdout; my redirect only made it durable. |
| Where it went | **Order 14 slice 0**, ahead of the URL work, with the fix shaped as silencing the logger (fails closed) rather than a redaction filter (has to be right about every future URL format, and is silent when wrong). |

### Order 13 — returned with NO code, and its measurement is the valuable half
The agent was killed by the same OS lockout that hit me (below). **Nothing shipped.** Its worktree
`.claude/worktrees/agent-a34638c67b88f2b8a` (branch at `db65ec1`) is **preserved deliberately**, holding
**22 additive uncommitted lines** I read before deciding: `LIVE_STREAM_REPLY` plus
`class LiveStreamError(ExtractionError)`, both well-commented, both unwired. The agent recommended
discarding them; **I kept them instead**, because they are the design's foundation and a successor
continued in that same worktree keeps the WIP (`dispatch.md` → Agent death, item 3).

**Slice 1's risk is now a measured fact, not my read.** It drove the unguarded `download_into` against
`youtube.com/watch?v=X4VbdwhkE10` in a child process with a hard 20 s `SIGKILL`:

```
exited on its own = False
bytes on disk     = 2,097,152 (X4VbdwhkE10.mp4.part)
growth            = monotonic, ~104 kB/s = ~6.3 MB/min = ~375 MB/h on an audio-dominant stream
```

Mechanism from the installed yt-dlp: `hls.py`'s `can_download` yields `not is_live`, so live routes to
`FFmpegFD`, which writes until stopped. **Worse than I had it:** `temp_workspace`'s cleanup is a
`finally` that only runs when `download_into` returns, and on a live link it never returns — so the
`.part` grows unbounded for the life of the process, and delivery being serial, that one message is the
whole bot. It disclosed spending a bounded ~2 MB on the production host to get this; I had refused to.

**The trap it caught, which would have shipped a silent regression:**

| URL | `is_live` | `live_status` | `was_live` | `duration` | must be |
|---|---|---|---|---|---|
| `watch?v=X4VbdwhkE10` (live) | `True` | `is_live` | `False` | `None` | **refused** |
| `watch?v=jNQXAC9IVRw` (ordinary) | `False` | `not_live` | `False` | 19 | accepted |
| `watch?v=zo5oewEQbsE` (**finished** stream) | `False` | `was_live` | **`True`** | 1146 | **accepted** |

`was_live` is `True` for exactly the case that must NOT be refused. Correct read: `is_live` truthy **or**
`live_status == 'is_live'`, nothing else.

**Design it left, so the expensive part is never re-derived:** `is_live_stream(info)` pure;
`_refuse_live(info, *, incomplete=False)` wired as `_ydl_options["match_filter"]` — **never return
yt-dlp's `NO_DEFAULT` sentinel, it makes yt-dlp prompt on `input()`** and nobody is at the terminal;
raise `LiveStreamError` **before** the `path is None` check or the refusal misreports as "left no file";
`_deliver` picks the reply from the **exception class**, never a string, so no `FAILURE_SIGNATURES` row
is keyed on the bot's own sentence. `match_filter` fires before any bytes — `_match_entry` is consulted
from `process_video_result` (~line 3042) before format selection — **read from source, never run.** The
first thing to do when this resumes is that bounded re-run expecting 0 bytes.

**Slice 2 unbuilt, proposed:** `MAX_LINKS_PER_MESSAGE = 5`, first 5 delivered, one Spanish line for the
message. At ~8.7 s/link that is ~43 s, the same order as the 43 s a transport failure already costs.

### Four things my order 13 got wrong on fact — all four caught by the agent
1. *"Committed as `db65ec1`, so it is in your worktree"* — **false**, the worktree branched from
   `0651e21`. Same root cause as the fast-forward problem already logged: **I commit the order to `main`
   after the branch point.** This has now cost two orders. Fix: state the branch point as the SHA the
   worktree will actually have, or tell the agent to `merge --ff-only main` first.
2. *"If the metadata does not mark it, use a size or duration ceiling instead"* — **that branch was
   never viable.** On a live stream `duration`, `filesize` and `filesize_approx` are all `None`. A
   ceiling cannot fire on a live stream at all; it would have shipped a guard that never runs plus false
   positives on long ordinary videos.
3. *"`_simulate_options`"* — does not exist; the metadata-only path is `_no_formats_ok`.
4. *"Twitch, now that order 11 added those hosts"* — wrong list. `twitch.tv` is in
   `MEDIA_PLATFORM_HOSTS`, asserted disjoint from `SUPPORTED_HOSTS`. Live exposure is **YouTube,
   Instagram and Facebook only**, and only YouTube is measured.

My field-name hedge was right to hedge — but the trap was `was_live`, not the names I doubted.

### The blocker that ate 20 minutes of the box
**macOS revoked read access to the whole project at ~17:15**, for me and for the agent: `stat` worked,
every `open()`/`listdir()` returned `EPERM`, **and it reproduced with the sandbox disabled**, so it was
not the Claude sandbox — TCC on `~/Documents`. Cleared by the owner at ~17:35 (Full Disk Access).

What survived and why: **the live bot never noticed** (pid 48057, its file handles predate the
lockout — 203+ polls, zero ERROR lines), and everything of mine was already committed and pushed. The
one thing I could still write was `/private/tmp`, so the agent's measurements went into a handoff there
before this section existed. **Standing note for any future run: `/private/tmp` stays writable when the
project does not — write the handoff there first, then copy it in.**

### Baseline recorded BEFORE order 14 slice 0, so the live test is a real before/after
| | Pre-fix, measured 17:39 on the live process |
|---|---|
| lines containing the token | **350** |
| the bot's own lines that **must survive** | `polling; privacy mode must be OFF…`, `only one instance can poll this token…`, `Application started`, and two real field lines: `message 69930: no URL recognised, nothing to do` / `message 69931: …` (16:41) |
| the right instrument | `grep -vE "HTTP Request:"` — everything that is not httpx. **My first grep looked for the literal `the-bot` and returned 0**, which would have read as "the bot logs nothing"; the format is `%(asctime)s %(levelname)s %(message)s` with no logger name. Caught it on the next line. |

**Post-merge assertion:** token occurrences → **0**, and all five of those own-lines still present.

### Field state during the lockout
No traffic. 345 polls, **zero** ERROR/WARNING/Traceback lines, last poll 17:38:54, ledgers unchanged at
6 and 4 lines. **Nothing was missed while I was locked out** — the group was quiet, and the bot never
noticed the lockout at all.

### One hygiene gap found, deliberately NOT fixed by me
`.claude/` is **untracked and not in `.gitignore`** (`.env` and `.venv/` are). It holds the agent
worktrees, so a blind `git add -A` would commit them. `.gitignore` is config, which the role counts as
code, so it is an order, not mine — and it is small enough to ride along with the next one rather than
earn its own.

### Landing — order 14 slice 0: the log stops printing the token. **APPROVED**, four layers.
`main` = `18713e9`. Merged by rebase, so the commit SHA changed — **verified by the `bot.py` blob**
(`34538076…`, identical to what I gated), not by the SHA.

| Layer | Evidence |
|---|---|
| **Gates** | `py_compile` OK and `--self-check` **passed** in the agent's worktree *before* the trunk, and again on the trunk after the merge. Still exactly six downloads. `git status --short` clean apart from untracked `.claude/`. |
| **Diff faithful** | The commit touches **only** `bot.py`, `README.md`, `AGENTS.md` — its territory. (`git diff main..branch` also showed my doc commits as deletions; that is a branch-point artifact, confirmed by diffing the commit itself.) |
| **Live — RUN** | Killed the old host, restarted on the merged tip with a fresh log. **Token occurrences: 350 before → 0 after.** All the bot's own lines still there (`polling; privacy mode…`, `only one instance can poll…`, `Application started`), and 2 established connections to Telegram prove it is really polling. |
| **Adversarial** | The agent shipped 6 mutations, all red. **I re-ran two myself, one in each direction:** the `setLevel` deleted → *"a request URL reached the log with the token in it"* with the leaked line quoted; and the level widened onto the root logger → *"the bot's own INFO lines no longer reach the log"*. The present-but-wrong half — the half `audit.md` says gets skipped — is genuinely covered. |

**Why the check is not vacuous:** it drives a **real `httpx.Client` over `MockTransport`** with a fake
token in the URL and asserts both halves in one capture. A level-number assertion would keep passing the
day the request log moves to a name nothing silences — the agent proved that with a mutation renaming
`REQUEST_URL_LOGGER` to `httpcore`.

### A consequence I did not anticipate: the fix killed my own liveness instrument
The old check was *"count `getUpdates` lines in the log"*. Those lines **were** the httpx log, so they are
gone — the new log is 3 lines and stays that way. **New instrument, measured:** `pgrep -f bot.py` plus
`lsof -p <pid> -i -a | grep -c ESTABLISHED` → **2** established connections. Recorded in the contract.
Nothing was wrong with the landing; my instrument was downstream of the leak.

### The trap that hit BOTH orders this round
**A fresh agent worktree does not branch from current `main`.** Order 13's sat at `0651e21`, order 14's at
`0651e21` too — **7 commits behind** — while both orders said *"branch from current `main`"*. The delta was
docs-only both times, so nothing was built on a stale base, but I asserted it twice and was wrong twice.
**Every future order gets: *"your worktree may be behind — run `git merge --ff-only main` first and say
what it moved."*** Both agents worked it out themselves; that is luck, not process.

### My fifth error of the round, caught by the agent
I wrote *"the self-check exercises the real API, so a mis-scoped `setLevel` that breaks the client will
show up there."* **False twice over:** nothing in `--self-check` reaches the Telegram API (every
`build_application` there uses a shaped fake token and is replaced before any poll), and a `setLevel`
cannot break an HTTP client — it only decides whether a record is emitted. The real confirmation is inside
the agent's own check: the request completes with the new config in force.

---

## GOAL — "los 3 que valen la pena" · locked 2026-08-10 18:0x
- **Mode:** **GOAL**, no `ask`. Ceiling *feature*. Ends when the criteria below are **verified**.
  Previously AUTO with a 1 h box, closed 17:53 with the ⏱ banner.
- **Docs loaded:** modes ✓ · continuation ✓ · run-loop ✓ · dispatch ✓ · audit ✓ — all read from disk
  this session, no compaction since.
- **Serial by construction.** Everything here lands in `bot.py`, so it is **one track at a time**
  (`dispatch.md`: a project whose work serializes through one module runs ONE track; a second only buys
  collisions). Order: 1 → 2 → 3.

### Kickoff answers, and what they changed
| Question | Answer | What it locked |
|---|---|---|
| How far back to recover | **Everything Telegram keeps (~24 h)** | **No age filter.** `drop_pending_updates=False` and nothing else — one line instead of a filter. The `ponytail:` at `bot.py:2479` proposed "a few minutes"; the owner went generous, and **the evidence backs him**: the flood that justified dropping everything was measured at **7 updates**, 2 of them reels. |
| Announce start/stop | **Neither** | Item 3 is link recovery only. No new chat noise. Kills the "bot caído" announcement I had offered twice — correctly: an unclean shutdown never sends it, so the silence stays ambiguous anyway. |
| Who hosts for the live layer | **Owner brings his down, I host** | Item 3 gets a **real** live layer: I can force a gap, paste into it, and verify the recovery. Until his poller stops I cannot touch `getUpdates` at all. |

### Acceptance criteria — the terminal condition
1. **A live stream never starts downloading.** A pasted live link gets its own Spanish line and **zero
   bytes** of media hit disk. Proven by the bounded re-run of `youtube.com/watch?v=X4VbdwhkE10` with the
   guard in place — the measurement that is currently *read from yt-dlp's source, never run*.
2. **A finished stream still delivers.** `watch?v=zo5oewEQbsE` (`was_live: True`, duration 1146) is an
   ordinary video and must NOT be refused. This is the regression the guard can silently cause.
3. **`is_supported` never raises.** A malformed URL from a Telegram entity returns `False`, and a good
   reel in the same message is still delivered. Control arm: a real reel still returns `True`.
4. **A link posted while nobody hosted arrives when someone starts.** Verified live: force a real gap,
   paste a reel into it, start the bot, watch it deliver.
5. Gates green, and every mutation in the must-stay-red list still red.

### The risk to criterion 4, unmeasured and load-bearing
**The launcher runs `getUpdates?timeout=0` before starting.** If that probe *consumes* the backlog, the
whole feature is silently dead — a friend's launcher would eat the pending updates before the bot ever
sees them. Bare `getUpdates` without an `offset` should not confirm anything (confirmation needs
`offset = last_update_id + 1`), so my read is that it is safe — **but it is a read, and this is exactly
the shape of premise that has been wrong before.** It cannot be measured without touching `getUpdates`,
which is barred while the owner's poller is up. **First action once I have hosting.**

### GOAL hunt log — what I ran while the order-15 agent works
| What I ran | Predicted | Observed | Where it went |
|---|---|---|---|
| Read the launcher's probe verbatim, both platforms | It confirms the backlog and eats it | **`getUpdates?timeout=0&limit=1` on mac, `?timeout=0` on Windows — neither passes `offset`.** Telegram only confirms updates when `offset > update_id`, so a bare probe returns the oldest pending update and **removes nothing.** My read is that criterion 4 is safe. | **Still a read, not a measurement** — see the plan below |
| Drove the project's own `_deliver` on a real reel into **my** test group, capturing every log record, with `configure_logging()` exactly as `main()` runs it | The slice-0 fix might have silenced delivery lines too — my earlier live test only proved the *startup* lines survived | **`sending …/reel/DbGNFqVKnB-/ as video (1272833 bytes)` survived, and 0 records contained the token.** The video arrived. | **Closed a real hole in my own slice-0 audit**: I had proven the token's absence and the startup lines, never a delivery's lines — and those are what a friend reads when a link misbehaves |

### The one test that proves the premise AND criterion 4 in a single shot
It needs one human action, because **a bot cannot see its own or another bot's messages** — there is no
way to create a pending update without a person posting. Sequence, to run once item 3 is merged:

1. I stop my bot → a real gap exists.
2. **The owner pastes one reel in the group.** (The only step I cannot do; it is physical, not a
   preference.)
3. I run the launcher's exact probe, `getUpdates?timeout=0&limit=1` — **the thing under test.**
4. I start the bot on the merged tip.
5. **The reel arrives** ⇒ the probe did not eat the backlog *and* criterion 4 is proven. It does not
   arrive ⇒ the probe consumes, the feature is dead as designed, and item 3 needs the launcher changed
   instead of `bot.py` — which would be a plan change, logged before anything is re-dispatched.

Deliberately **not** requested yet: asking for the paste before the code is merged wastes it.

### Landing — item 1, the live-stream guard. **APPROVED**, four layers. `main` = `64d8a2f`
| Layer | Evidence |
|---|---|
| **Gates** | `py_compile` OK and `--self-check` **exit 0** in the agent's worktree before the trunk, and again after the merge. Four new live-stream checks pass, including *"a live stream is refused before a byte; a finished one is still delivered"*. Still six real downloads. |
| **Diff faithful** | `bot.py` +378 and `README.md` +104, nothing else. Of the 378, **89 are comment/docstring and 49 are asserts or prints** — the product code is a small fraction, which is the right shape for a guard whose whole risk is a field name. It left `AGENTS.md` alone and handed me the must-stay-red delta, per the standing rule. |
| **Live — RUN, and it is the measurement that was missing** | Drove the real `download_into` against `youtube.com/watch?v=X4VbdwhkE10`: **`LiveStreamError` in 3.4 s and 0 bytes on disk.** Unguarded, the same call wrote **2,097,152 bytes in 20 s** and had to be killed. Then the control arm on the real metadata of the finished stream `zo5oewEQbsE`: **accepted**, `was_live=True`, `live_status=was_live`, duration 1146, `is_live_stream()` → `False`, 0 bytes. **Criteria 1 and 2 both closed live.** Prediction was written before the run and matched on both. |
| **Adversarial** | The agent shipped its own table; **I re-ran the two that matter.** Keying the guard on `was_live` → `AssertionError: a FINISHED stream is an ordinary video`. Choosing the reply from the text instead of the exception class → red. Both in the direction that would ship a silent regression. |

**Why it is not vacuous:** the refusal is `is_live` **or** `live_status == "is_live"`, and `was_live` is
deliberately not read — with a check asserting the finished stream survives, which is the only thing that
catches the silent version. A second check asserts `LIVE_STREAM_REPLY` is **not** in
`FAILURE_SIGNATURES`, which pins the "chosen from the class, never the text" rule the order demanded.

### Two process errors of mine on this landing
1. **I merged at three commits because my waker counted commits — and the agent was still working.** It
   then produced a fourth: *"drop a vacuous assert from the live-stream copy check"*, where it caught
   **its own** check asserting the Spanish line contains no `"vivos"` — a word no Spanish reply here
   would ever contain, so the assertion could never fire. Fake rigor, removed by the agent, not by me.
   **A commit count is a heartbeat, not a completion signal; the report is.** The tail commit is parked
   and merges when it reports.
2. **I rebased its branch while it was still working in that worktree.** That rewrites commits under a
   live agent's feet. It survived — it committed cleanly on top of the rebased branch — but that is luck,
   not process. **Rebase after the report, never during.**

### Criterion 1 widened from n=1 to n=6, and an instrument error of mine on the way
Found six independent live URLs off a channel's streams tab (metadata only) and drove the **real**
`download_into` against every one:

```
LTiqKDrjqr4 refused 0 bytes 1.5s   GSfT7H87zq4 refused 0 bytes 1.6s
VAlMDl00mYY refused 0 bytes 1.1s   qGohtGC5Rtk refused 0 bytes 1.2s
X4VbdwhkE10 refused 0 bytes 1.5s   CwPCy1GLS38 refused 0 bytes 1.5s
CONTROL jNQXAC9IVRw -> delivered jNQXAC9IVRw.mp4, 629172 bytes
```

**6/6 refused, zero bytes each, and the control arm still delivers** — so the sweep is not vacuous and
criterion 1 no longer rests on a single URL.

**My instrument error, caught against myself:** my first two attempts at this used `timeout 40 …` and
returned nothing, which I could have written down as *"no premieres found"*. They never ran — **`timeout`
does not exist on this Mac, and it is already in the exercise contract's known traps** — and `2>/dev/null`
hid the `command not found`. I used the trap my own foundation warned me about, and the silent-hiding redirect
turned it into a plausible result. Rerun without it, the probe returned six rows immediately.

**Still unmeasured, and honestly so:** `is_upcoming` (a premiere). I looked and the channels I checked had
none right now, so the agent's `ponytail:` stands as written — both values fall through rather than being
guessed at. Not a gap in the landing; a gap in the world's supply of premieres at 20:00 on a Monday.

### Landing — item 2, an unparseable URL no longer costs the whole message. **APPROVED**, four layers
| Layer | Evidence |
|---|---|
| **Gates** | `--self-check` **EXIT=0**, 51 `ok` lines, in the worktree before the trunk and again after the merge. Six real downloads unchanged. |
| **Diff faithful** | `bot.py` +122, `README.md` +35, nothing else. Slice 0 untouched (0 hits for `configure_logging`). It handed me the `AGENTS.md` delta rather than editing it, per the standing rule. |
| **Live — RUN** | Drove the real `_handle_links` on four messages into **my** test group: **poison + good reel → the reel was delivered** (`sending … as video (1272833 bytes)`) and the handler returned cleanly; poison-only → clean, recorded as *"1 URL(s) found, none on a supported host"*; IPv6-only → clean; control reel → delivered. **Before the fix, the first case killed the whole message.** |
| **Adversarial** | The agent shipped 7 mutations. **I re-ran the load-bearing one myself** — the guard removed — and it is RED: `ValueError: Invalid IPv6 URL` escapes and the check dies, EXIT=1. My mutation script asserted the file actually changed, which caught my *first* attempt being a no-op. |

### The agent refuted my design, and it was right
My order recommended guarding `is_supported`, "so every present and future caller inherits it".
**It does not inherit.** `_handle_links` also asks `_host_is_one_of` about `MEDIA_PLATFORM_HOSTS`
**directly, on the raw URL list, after** `is_supported` has filtered. So my fix would have delivered the
good reel and still killed a message whose only URL was unparseable. The guard went into
`_host_is_one_of`: one host rule, three call sites, 4 functional lines.

### And the defect was bigger than I measured — my own hunt was wrong
I had recorded *"not reachable through the regex, only through a Telegram entity"*, with the entity half
unmeasurable. **False.** `URL_PATTERN` accepts any non-space character, so a host containing a **fullwidth
`#`, `/`, `@` or `:`** comes straight out of plain message text and raises
`netloc … contains invalid characters under NFKC normalization`. **I verified all four shapes myself
end-to-end.** My earlier sample tested fullwidth *full stop*, dotless i, punycode, a 300-char host and a
zero-width space — none of which make an invalid netloc. **I sampled seven candidates and missed the
family that matters: fullwidth versions of the characters `urlparse` uses as delimiters.** So the defect
needs no entity, it needs a phone keyboard.

### Slice 2 — the outcome I wanted if it was true: nothing built
`_deliver` already wraps its whole body in one `try`/`except Exception` that logs, records under its own
class and apologises, and both calls on the way out are non-raising by contract. The agent drove three
links with the middle one raising: **the other two delivered, one ledger record, one apology, loop
unbroken.** A second `try` would add no isolation and only make a failure quieter.

### Three instrument errors of mine on this landing, all the same family
1. **I read a false RED on the gate.** `EXIT=1` came from piping the self-check through `head -5`, which
   closes the pipe and kills python with SIGPIPE — reproduced: with `head`, `EXIT=120`; without it,
   `EXIT=0`. `audit.md` warns that trimming can *hide* a failure; it can also *manufacture* one.
2. **Two probes that never ran**, earlier the same round: `timeout 40 …` with `2>/dev/null`, where
   `timeout` does not exist on this Mac — a trap already written in my own exercise contract.
3. **A vacuous mutation**, caught only because I asserted the file changed.
**All three were truncation or a missing binary corrupting the measurement. The standing fix: read exit
status directly, never through a pipe that can close early, and never hide stderr.**

### What the agent found wrong in my order, beyond the design
`§4.6` is not the link-list section (`§4.11` is), silence-by-default is documented in `§5.4` not `§4.6`,
`is_supported` calls `urlparse` not `urlsplit`, slice 3 said *"Commit"* while the dispatch said the
`AGENTS.md` delta must arrive as a diff — and **the CHECKPOINT was still self-contradicting**: its
"slices 1-3" arm says *"the above, plus…"*, which demands slice 0's evidence from an agent forbidden to
touch slice 0. **My self-audit fixed the mirror image of that bug and left this one**, which is exactly
the failure mode `audit.md` describes for editing a shipped order.
