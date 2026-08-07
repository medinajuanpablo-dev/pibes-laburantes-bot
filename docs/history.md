# How this project got here — 2026-08-07

> Load when you need to know **why** something is the way it is, or when you are about to re-open a
> decision. Ignore for day-to-day work — `README.md` and `AGENTS.md` carry everything you need to
> change the code safely.

The whole bot was specified, built, audited and shipped in one afternoon: a CEO/director agent
holding the map and the evidence bar, and one implementing agent doing every line of code. The
director never touched application code. The full run log is `RUN-STATE.md`.

## The premises that turned out to be false

This is the most useful section in the file. Every item below was written down as fact by someone
confident, and then killed by a measurement. If you find yourself reasoning from any of them again,
stop and measure.

| Claimed | Reality |
|---|---|
| *"Instagram fails without cookies even for public content"* — `PLAN.md` | **False for reels.** `instagram.com/reel/…` extracts anonymously. The original probe tested a **profile page** (`instagram.com/<user>/`), a URL shape yt-dlp marks broken upstream. Wrong URL shape, right-sounding conclusion. |
| *"Facebook's extractor is broken"* — `PLAN.md` (which honestly flagged the cause was never isolated) | **False.** `share/v/` and `share/r/` links both extract anonymously. The original test URL was simply dead. |
| *"Anonymous Instagram extraction is unreliable in 2026, auth required since 2024"* — a web search the director ran and repeated into an order | **False for reels**, and it was secondary evidence presented alongside primary. It was recorded as unverified and then refuted by an actual probe. |
| *"720p at `height<=720` is the quality cap"* | **Incomplete.** It selects AV1 on YouTube, which Telegram delivers as a **document**. And it matches *nothing* on Facebook's portrait DASH streams, silently falling through to an unmetered fallback. |
| *"1080p would cost ~34 MB"* | **Wrong number.** 34 MB is the AV1 figure. At the h264 preference this project needs, the same video is **~84 MB** — over the ceiling. Caught by the implementing agent, not the director. |
| *"The merged mp4 isn't faststart, that's why Telegram misreads it"* — a director hypothesis | **Refuted.** All three files are `ftyp moov free mdat`. The real fix was passing width/height/duration explicitly. The hypothesis was dropped after one cycle instead of being chased. |
| *"You don't need to remove and re-add the bot"* — the director, answering the owner | **Wrong, and corrected within the same session.** Measured: a bot added before the privacy toggle received only service messages; one added after received plain text. The owner could not remove it (not an admin), which is how the **administrator** path — the better answer — got found and measured. |
| *"A Facebook top-level/file dimension mismatch justifies this rule"* — the implementing agent | **Fabricated.** Both values are `null` under this project's format string. The agent measured it, found no such mismatch, reported the error against itself, and kept the rule on its actual merit. |

Two of these were the director's own, one was the agent's, and the agent self-reported it. That
ratio is the point: **the measurement decided, not the seniority of whoever said it.**

## What mutation testing caught that green runs did not

Three times, a passing self-check was proven to assert nothing about the thing it was named for:

1. **The extraction check was vacuous on the whole design.** Replacing `MEDIA_FORMAT` with the
   codec-agnostic string — the exact "optimisation" a future reader would make to save 9 MB — left
   the check **fully green** while producing the AV1 file that Telegram delivers as a grey row. The
   owner had, an hour earlier, seen exactly that file arrive and said *"no sirve"*.
2. **A fix shipped with no guard.** After the `connect_timeout` fix landed, deleting
   `connect_timeout=CONNECT_TIMEOUT` left the self-check green. A production defect had been fixed
   with nothing to stop it regressing. The agent found this by mutating its own work.
3. **A privacy assert covered one branch of two.** The check forbidding message bodies in the log
   only looked at the rejected-URL line, so logging the body on the no-URL path slipped through.

The lesson encoded in `AGENTS.md`: prove the check dies both when the feature is **absent** and when
it is **present but wrong**.

## Defects found by running the real thing

None of these were visible in the code, in review, or in a green gate. All came from the bot running
against a live group.

- **AV1/webm arrives as a document.** Found by uploading four container/codec combinations and
  reading the API's own classification of each. This is why `MEDIA_FORMAT` prefers h264/aac and why
  `telegram_renders_inline()` exists.
- **`reply_video` without dimensions produces a 320x320, duration-0 video.** Found by a control arm:
  the same file sent twice, with and without explicit metadata.
- **Uploads gave up after 5 s.** `telegram.error.TimedOut` on a 1.2 MB file, twice, three minutes
  apart, while a 17.6 MB file succeeded in the same session. `connect_timeout` was never passed and
  defaults to 5.0 s, so the 600 s upload budget was never consulted.
- **The apology itself failed, and the group got nothing.** The `reply_text` inside the except block
  timed out too and escaped `_deliver` — a silent drop, the one outcome the original plan explicitly
  forbade. The owner never noticed; it was found in the log.
- **The bot ignored a link and left no trace of why.** Not a crash — an observability hole. Two
  separate incidents were reduced to guesswork before `on_message` was made to log its decision.

## Decisions worth not re-litigating

- **Territory.** `PLAN.md` targeted `small-shit/telegram-meme-bot/` in a shared repo. The project
  lives in its own repo instead: the shared-repo constraints disappear and the port stays a file copy.
- **h264 over AV1, at +9 MB on YouTube.** Inline playback is the product. A smaller file that arrives
  as a grey row is worth nothing.
- **720p, not 1080p.** 1080p in h264 is ~84 MB, over the ceiling. 720p is ~29 MB.
- **No JS runtime.** yt-dlp warns on every extraction that running without one is deprecated.
  Extraction works anyway, measured at 2160p and merged 720p. The port target is an old Linux box, so
  a new runtime dependency added to silence a warning is a bad trade. Documented instead, with
  `--js-runtimes node` as the escape hatch.
- **`YTDLP_COOKIES` kept despite being unnecessary.** Three lines, and it turns "Instagram tightened
  access" from a code change into a config change.
- **Raise the timeout rather than lower the size cap.** Lowering the cap would reject files Telegram
  accepts.
- **The self-check downloads real videos, and keeps them short.** Extraction rot is the real failure
  mode, so a mocked download would test nothing; a 30 MB download on every run was the biggest
  recurring cost in the project and bought nothing the codec assertion needed.

## What was never verified

- **The oversize → direct-link path has never executed against Telegram.** Ten real deliveries, none
  over 50 MB. Asserts and a source read only. Proving it needs one YouTube video long enough to
  exceed 50 MB at 720p h264.
- **Whether the one duplicated Instagram delivery was a double paste or an edit event.** The
  edited-message leak was real and is fixed, but it was fixed on a source read, not on this
  observation — Facebook and YouTube each delivered exactly once in the same session, which does not
  fit the edit theory. Left unresolved rather than explained away.
- **Why one friend's link was ignored.** The bot was alive and polling; there was simply no log line.
  That gap is now closed, so the next occurrence answers itself.
