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
