# CEO loop-state — Telegram meme bot

## Run
- **Mode:** GOAL · **started:** 2026-08-07 14:33 -03
- **Deadline:** — (no time box) · **aterrizaje:** no iniciado
- **Objective:** execute `PLAN.md` (Telegram meme bot, phase 1). Definition locked at kickoff — see below.
- **Docs loaded:** modes ✓ · continuation ✓ · run-loop ✓ · loop-state-template ✓ — stamped round 0 / 14:35
- **Standing re-read triggers**
  - after a compaction or a gap → `~/.claude/skills/ceo/references/run-loop.md` (Cold resume) + `continuation.md`
  - before the next verdict → `audit.md` · before the next order → `dispatch.md`
- **Heartbeat armed:** — (no agent in flight yet; kickoff round pending)
- **Exercise contract** (living):

| Qué | Cómo |
|---|---|
| Run / dev command · canonical port | `python bot.py` with `TELEGRAM_BOT_TOKEN` in env. No port — long-polling client, not a server. |
| **Live-surface INSTRUMENT** | The real Telegram client + a real bot token. **Structurally cannot measure without a token** — no token ⇒ slices 1 and 3 have no live layer at all. `yt-dlp` CLI covers the extraction half independently. |
| Gates (exact commands) | `python -m py_compile bot.py`; each slice's own `__main__` self-check |
| Creds & roles | Bot token (BotFather) — **not present in env, user-owned**. Test group — user-owned. |
| How to reset state | Stateless. Downloads go to a temp dir wiped per call. |
| **MINE — disposable material** | scratchpad probe venv, the project dir, any test group *I* am given, temp download dirs |
| **THEIRS — hard-stop** | the user's own Telegram account/groups, the `small-shit` repo outside `telegram-meme-bot/`, their Instagram account |
| **State the hunt writes to** | temp download dirs only + messages into a designated test group |
| Primary flow | paste a YouTube link in the group → bot replies with a playable video |
| **State-forcing recipes** | Extraction half is testable with zero Telegram: `yt-dlp --simulate -f "<fmt>" <url>` |
| **Known traps** | `timeout(1)` does not exist on this Mac — use `--socket-timeout`. yt-dlp warns **no JS runtime**; `deno` MISSING, `node`/`bun` present. |
- **Project hard-stops:** no repo-root `.gitignore` edits in `small-shit`; never `git add .`/`-A`; secrets never staged; no `CLAUDE.md`/`AGENTS.md` edits; do not use the user's own Instagram account.

*(Finite mode)*
- **Locked definition / acceptance criteria:** PENDING — kickoff round in flight.
- **Re-plans of that definition:** —

## Iteration checkpoint — overwrite each cycle
- **Altitude(s) now:** single track → *feature* (kickoff)
- [x] This iteration I **dispatched, audited, or ran the product** — ran the yt-dlp probes myself
- [x] A waker is armed, OR I'm continuing in-turn, OR the terminal condition is met — kickoff = sanctioned user round
- [x] The **hunt log grew** this round
- [x] No queue item has sat 3+ rounds without a stated reason
- [x] Every landing below has all four evidence layers — no landings yet

## Tracks
| Track | Layer | Altitude | Status | Agent / worktree | Territory |
|---|---|---|---|---|---|
| A — bot | app | feature | not started (kickoff pending) | — | TBD by kickoff Q1 |

## Contended resources
- **Browser pane + canonical port:** N/A (no web surface). **Lease holder:** — none
- **Port ledger:** —
- **Lock table:**

| File | This round | Pending changes to sequence |
|---|---|---|
| `small-shit/.gitignore` (repo root) | **nobody — barred by PLAN.md TERRITORY** | — |

- **Merge queue:** —

## Landings
| # | What landed | Verdict | Gates | Diff faithful | Live | Adversarial |
|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — |

- **Deferred live layers owed:** —
- **Rescues:** —

## Hunt log
| Round | What I launched & did | **What I PREDICTED** | What I observed | Findings → where they went |
|---|---|---|---|---|
| 0 (entry audit) | Installed `yt-dlp[default]` 2026.07.04 in a scratchpad venv; ran 3 `--simulate` probes against `youtube.com/watch?v=dQw4w9WgXcQ` | PLAN.md's table replicates: best ≈2160p/243MB, progressive `b` cap = 360p. Merged `bv*[height<=720]+ba` untested by the plan — predicted it would resolve and land well under 50 MB. | **All three confirmed.** best `2160p 243768398 213s` — byte-identical to the plan's number. `b[filesize<45M]/b[height<=720]/b` → `360p mp4 11832459`. `bv*[height<=720]+ba/b[height<=720]` → **`720p 21026831`** (~20 MB, ffmpeg merge, comfortably under 50 MB). | YouTube premise **holds**. 720p-merged is viable → Decision #1. |
| 0 (entry audit) | Environment probe: `python3`, `ffmpeg`, `yt-dlp`, `docker`, `deno`/`node`/`bun`, `TELEGRAM*` env vars | PLAN.md's environment claims all hold | macOS 15.1.1 arm64 ✓, Python 3.11.7 ✓, ffmpeg at `/opt/homebrew/bin/ffmpeg` ✓, Docker ✓. **`yt-dlp` NOT installed system-wide** (plan implies it was available). **No `TELEGRAM_BOT_TOKEN` in env.** `deno` MISSING, `node`+`bun` present. | → Sub-task #1 (JS runtime), Parked #1 (token) |
| 0 (entry audit) | `git status --porcelain` in `small-shit`; `ls` of `the-bot` and `small-shit/telegram-meme-bot` | Plan's territory (`small-shit/telegram-meme-bot/`) matches where PLAN.md actually lives | **MISMATCH.** PLAN.md lives in `Projects/the-bot/` (**not a git repo**, contains only PLAN.md). The territory it names, `small-shit/telegram-meme-bot/`, exists inside the `small-shit` git repo but is **empty** (`.DS_Store` only) and shows as `?? telegram-meme-bot/`. Rest of `small-shit` is clean. | → kickoff Q1 |

## Queues
### Sub-tasks (required)
| # | What | Where | Why it's required | Round found |
|---|---|---|---|---|
| 1 | yt-dlp warns *"No supported JavaScript runtime… extraction without a JS runtime has been deprecated, and some formats may be missing"*. Works today anyway (2160p + 720p merge both resolved). `node` is present but yt-dlp only auto-enables `deno`. | README + a `ponytail:` comment near the yt-dlp call | This is the plan's own thesis — *what kills this bot in six months is operational drift*. An undocumented deprecation warning is exactly that. Cheap now (one README paragraph naming `--js-runtimes node` as the escape hatch), expensive when the group is yelling. **Not** a dependency today: adding a JS runtime to the port target would violate DESIGN LAW 2's spirit. | 0 |
| 2 | Verify Telegram's actual bot-upload ceiling (plan asserts 50 MB, says verify it) and the send-by-type split (video/photo/animation) | slice 2 format cap + slice 3 send logic | The 50 MB number is the only thing sizing the quality cap | 0 |

### Ideas (deferred)
| # | Shaped idea | Leverage | Round found |
|---|---|---|---|
| — | — (GOAL mode: ideas are mapped, never attacked) | — | — |

## Decisions made autonomously
| # | Decision | Why | Reversible? | Round |
|---|---|---|---|---|
| 1 | Quality cap = `bv*[height<=720]+ba/b[height<=720]/b` (720p merged via ffmpeg), **not** the plan's implied 360p-progressive | Measured: 720p merged = ~20 MB on a 3.5-min video, under half the ceiling. The plan itself flags 360p as "a format-selection consequence, not a limitation" and tells me to verify. 360p in 2026 looks broken to a group of friends. | Yes — one format string | 0 |
| 2 | Do **not** add a JS runtime dependency now | Works without it today (verified). Adding `deno` to a project whose stated destiny is an old Linux box contradicts DESIGN LAW 2 + 4 (no unneeded infra). Documented as sub-task #1 instead. | Yes | 0 |

## Parked for the user
| # | Barred action or question | What I need | What I did instead (pivot) |
|---|---|---|---|
| 1 | Cannot create a Telegram bot or obtain a token — that is the user's account and BotFather is interactive | `TELEGRAM_BOT_TOKEN` + a test group the bot is in, privacy mode disabled | Asked in the kickoff round. Extraction half (slice 2) is fully verifiable without it, so slices 1–2 proceed regardless. |
| 2 | The plan's slice 4 requires a **throwaway Instagram account** — creating one means signing up to an external service as the user | The user's call: create one, or drop Instagram from scope | Asked in the kickoff round (Q3). |

## Plan changes
| Finding | Verified how | Size | Re-planned | Deliberately preserved |
|---|---|---|---|---|
| — | — | — | — | — |

## Close-out
| Check | State |
|---|---|
| Live agents | none |
| Worktrees | none created |
| Unmerged branches | n/a |
| Gate on the tip | n/a — nothing built yet |
| Deferred live layers | — |
| The user's data | untouched — only read `git status`, never wrote to `small-shit` |
| Tree | `small-shit` clean except the pre-existing `?? telegram-meme-bot/` |

## Stop evidence — fill ONLY when invoking the self-stop bar
- —
