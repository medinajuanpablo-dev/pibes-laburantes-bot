# CEO loop-state — Telegram meme bot

## Run
- **Mode:** **AUTO** (open-ended, no time box, no `ask`) since **2026-08-09 17:15 -03**. Previously
  GOAL from 2026-08-07 14:33. Objective at *directivo*: I source it.
- **Docs loaded (AUTO re-stamp, round 0 / 2026-08-09 17:15):** modes ✓ · continuation ✓ · run-loop ✓ ·
  audit ✓ · dispatch ✓ · loop-state-template ✓ — all read in this same session, none through a compaction.
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
| Sub-task | Ledger fragments across rotating hosts | Accepted at this volume; `cat` is the merge. Promote if hosts multiply. |

### Standing re-read triggers
- after a compaction or a gap -> `~/.claude/skills/ceo/references/run-loop.md` (Cold resume) + `continuation.md`
- before the next verdict -> `audit.md` · before the next order -> `dispatch.md`
- closed history -> `docs/run-history/01-goal-session.md`
