# PROMPT-ORDER 15 — a live stream must never start downloading

> Self-contained. Written 2026-08-10 18:0x as item 1 of a three-item GOAL.
> **Re-verify every claim below against the live repo.** Reports are discovery-grade and they drift.
> Five separate facts in this project's last two orders were wrong and **the agent caught every one** —
> that is this line working, not the agent being difficult. Contradict me where I am wrong.

## CONTEXT

Read `AGENTS.md`, then `README.md` §1, §4 and §5.2.

**Your worktree may not be at `main`.** The last two agents here were handed worktrees **7 commits
behind** while their orders said "branch from current `main`". **Run `git merge --ff-only main` first and
report what it moved.** `main` should be at `28cf6a6` or later.

**This work was already started and then lost.** A previous agent measured the whole problem, designed
the fix, and was killed by an OS-level permission lockout with **22 additive lines uncommitted**. That
worktree is preserved on purpose:

```
git -C .claude/worktrees/agent-a34638c67b88f2b8a diff
```

**Read that diff.** It is a `LIVE_STREAM_REPLY` constant and a `class LiveStreamError(ExtractionError)`,
both with real comments explaining the design. Reuse it, improve it, or discard it with a reason — your
call, but do not re-derive it blind. It is uncommitted and never gate-verified, so **it is a draft, not a
landing.**

### The defect, measured — not a read

The previous agent drove the unguarded `download_into` against a real live stream in a child process
with a hard 20 s `SIGKILL`:

```
url               = youtube.com/watch?v=X4VbdwhkE10   (a permanent lofi radio stream)
exited on its own = False
bytes on disk     = 2,097,152   (X4VbdwhkE10.mp4.part)
growth            = monotonic, ~104 kB/s  =  ~6.3 MB/min  =  ~375 MB/h  on an audio-dominant stream
```

Mechanism, from the installed yt-dlp: `downloader/hls.py`'s `can_download` yields `not is_live`, so the
native downloader refuses and `downloader/__init__.py` routes live to `FFmpegFD`, which writes until
something stops it.

**And it is worse than an unbounded file.** `temp_workspace`'s cleanup is a `finally` that only runs when
`download_into` **returns**. On a live link it never returns — so the growing `.part` is never cleaned
while the process lives, and because delivery is serial, **that one message is the entire bot** until
somebody kills it. Confirm both halves yourself.

`MEDIA_FORMAT` **does** select a live format (`95`, `m3u8_native`, `avc1.4D401F` + `mp4a.40.2`, height
720, format-level `is_live=True`, no filesize), so the guard has to survive format selection.

### The trap — this is the way this change silently goes wrong

| URL | `is_live` | `live_status` | `was_live` | `duration` | must be |
|---|---|---|---|---|---|
| `watch?v=X4VbdwhkE10` (live now) | `True` | `is_live` | `False` | `None` | **refused** |
| `watch?v=jNQXAC9IVRw` (ordinary) | `False` | `not_live` | `False` | 19 | accepted |
| `watch?v=zo5oewEQbsE` (**finished** stream) | `False` | `was_live` | **`True`** | 1146 | **accepted** |

**`was_live` is `True` for exactly the case that must NOT be refused.** A finished stream is an ordinary
bounded video — that one is ~178 MB, so it leaves as the oversize link reply. A guard keyed on `was_live`
silently rejects every stream replay the group pastes, and no check that only tries a live URL would ever
notice.

**A size or duration ceiling is not an alternative** — do not reach for one. On a live stream `duration`,
`filesize` and `filesize_approx` are **all `None`**, at the info-dict level *and* on the selected format.
A ceiling cannot fire on a live stream at all, and it would false-positive on long ordinary videos.

### What is NOT measured

`match_filter` firing **before any bytes** is read from source, never run: `_match_entry` is consulted
from `process_video_result` (yt-dlp 2026.7.4, ~line 3042), after `formats` is populated and **before**
format selection and `process_info`. **I own that measurement** — the bounded live re-run expecting 0
bytes is my live layer after the merge, not yours.

## WHY IT MATTERS

Every other failure in this project makes the bot answer badly. This one makes it **stop being a bot**,
on a friend's laptop, filling their disk, with no way for them to know why. It is the last unbounded-work
hole and it is the first item of the owner's three.

## TERRITORY

Your own worktree. You may change **`bot.py`**, `README.md`, `AGENTS.md`.

**Do not touch:** the launchers, `instalar-bot.cmd`, `requirements.txt` (**no new dependency**),
`.gitignore`, `EMPEZAR-ACA.md`, `docs/**`, and **the preserved worktree above — read it, never write to
it.**

**Escape hatch:** if the fix needs something outside that list, **STOP and report.** Never buy compliance
by weakening an invariant — no bare `except`, no default that hides the gap, no required thing made
optional.

**Hosting is changing hands right now**, so treat it as live either way: **never call `getUpdates`, never
start a second bot instance.** No `.env` in your worktree and you do not need one. **You get no live
surface** — deterministic checks only. Note a fact the last agent established and I had wrong: **nothing
in `--self-check` reaches the Telegram API** (every `build_application` there uses a shaped fake token and
is replaced before any poll), so do not expect it to catch anything about the client.

## THE WORK

### Slice 1 — the pure question, answered purely

A pure `is_live_stream(info)` — my recommendation is `bool(info.get("is_live")) or
info.get("live_status") == "is_live"`, and **nothing else**; false on `None` and on `{}`. Verify the field
names against a real `--simulate` yourself rather than trusting the table above; if reality disagrees,
reality wins and you say so.

Add it to README §1's pure-layer table.

*Check:* the three rows of the trap table as three assertions, **including the finished stream returning
False**, plus the empty dict and `None`. Pure, no network.
*Commit.*

### Slice 2 — the refusal happens before a byte is written

Wire it as `_ydl_options["match_filter"]`, and raise a distinct exception so the ledger and the reply can
both tell this apart from a generic bounce.

- `match_filter` is called as `match_filter(info_dict, incomplete=...)` — the callable **must accept
  `incomplete` as a keyword**.
- **Never return yt-dlp's `NO_DEFAULT` sentinel.** It makes yt-dlp prompt on `input()`, and there is
  nobody at the terminal — the bot would hang forever, which is the same failure in a new costume.
- Raise the refusal in `download_into` **after extraction and BEFORE the `path is None` check**, or a
  correct refusal gets misreported as *"yt-dlp reported success but left no file"*.
- The ledger must record it under its own error class with the real detail. Message bodies never go in
  any file; the URL may.

*Check:* driven by a fake ydl / fake info — a live info dict is refused with the distinct exception and
**no download is attempted**, and an ordinary one is not. Assert the *call*, not only the outcome. No
network, and **do not add a seventh real download to `--self-check`.**
*Commit.*

### Slice 3 — the group is told, in the project's voice

The friend gets a specific Spanish line, not the generic apology.

- **Chosen from the exception CLASS, never by matching a string** — the same discrimination
  `is_transport_failure` already uses. This refusal is decided by the bot off the info dict; it never asks
  the site anything, so keying a `FAILURE_SIGNATURES` row on the bot's own sentence would re-create the
  exact defect README §5.2 records the YouTube row being fixed of.
- Keep the protected send single — do not add a fifth swallow site.
- Spanish, in the existing voice, no jargon: no "live_status", no "stream", no yt-dlp. The draft in the
  preserved worktree has a candidate line; use it or better it.
- The two `live_status` values you deliberately do **not** refuse — `is_upcoming` (a premiere) and
  `post_live` (ended, still processing, bounded by definition) — get a `ponytail:` naming them and the
  upgrade path, not a guess.

*Check:* the live exception maps to its own line, and an unrecognised failure still falls through to
`FAILURE_REPLY`.
*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** Still `bot.py`.
2. **Nothing OS-specific in `bot.py`.**
3. **No new dependency.**
4. **Secrets never enter git; message bodies never enter any file.**
5. **Code and comments in English; everything the group reads in Spanish.**
6. **`ponytail:` on every deliberate shortcut**, naming its ceiling and upgrade path.
7. **A confident wrong message is worse than the generic one.**

## STANDING RULES

- `python -m py_compile bot.py` and `python bot.py --self-check` **both pass before every commit.**
- **Commit each slice the moment it is green.** Two agents on this project have died with finished work
  uncommitted — one to a stall watchdog, one to the OS lockout. An uncommitted slice dies with you.
- **Mutation-test every slice and ship the table.** The entries that matter, at minimum: the guard
  reading `was_live` (a finished stream must still be delivered) · `is_live_stream` pinned constant in
  **both** directions · `match_filter` dropped from `_ydl_options` · the refusal raised **after** the
  `path is None` check · the reply chosen from the message instead of the class. Re-run any must-stay-red
  entry in `AGENTS.md` you touched.
- Explicit paths only. Never `git add .` or `-A`.
- **Write your `AGENTS.md` must-stay-red additions as a diff in your report**; `README.md` you may edit
  directly.
- A known trap that cost a predecessor its whole life: **an OS-level EPERM lockout hit this repo on
  2026-08-10** — `stat` worked while every `open()` failed, sandbox on or off. If it recurs, do not fight
  it: report immediately, and say what you had done and whether it was committed.

## CHECKPOINT

Stop and report after slice 3. Include:

1. What you ran, and the mutation table.
2. **What `git merge --ff-only main` moved**, if anything.
3. What you did with the preserved draft — reused, improved, or discarded, and why.
4. Whether the field names in the trap table survived your own `--simulate`.
5. **Anything in this order wrong on fact.** I expect something to be.
6. Every `ponytail:` left, and what needs my live layer.

---
*Self-audit 6/6 on this order, before sending: contradiction — none against `AGENTS.md`'s must-stay-red
list, which has no live-stream row yet. References — the worktree path, both YouTube URLs and the
`bot.py` structures were re-checked against the repo this turn. Wrong mode — n/a, this is a single
implementation track. Border — the "no live surface" rule holds even though hosting is mid-handover,
which is why it is stated as binding either way. Abuse — the "reality wins" clause in slice 1 could be
read as licence to skip the trap table, so slice 1's check names the finished-stream assertion
explicitly. Rot — no counts or statuses pinned; the `main` SHA is given as "or later".*
