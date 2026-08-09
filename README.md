# the-bot — a Telegram meme bot

A private Telegram bot for one group of friends. Someone pastes a YouTube / Instagram / Facebook
link in the group; the bot replies with the **actual video or image, playable inline**, so nobody
has to leave the chat.

**Scale is the design input: one group, roughly 20 links a week.** Every decision in this repo
follows from that number and from nothing else. If you are about to add a queue, a database, a
process manager or a second file, re-read that sentence first.

Everything lives in one file, `bot.py`. Its self-check lives in the same file and runs with
`python bot.py --self-check`.

**Here to host the bot for a while, not to work on it?** Read `EMPEZAR-ACA.md` — it is in Spanish
and it is the only file you need.

---

## 1. How it works

Long-polling client, not a server. No inbound port, no webhook, no TLS to manage.

```
Telegram update
  └─ on_message                    filters: real messages only, text or caption
       ├─ find_urls(text)          regex, http(s):// required
       ├─ is_supported(url)        host allow-list; everything else ignored (and logged)
       └─ _deliver(url)
            ├─ temp_workspace()          mkdtemp, removed in a finally
            ├─ download_into()           yt-dlp, format MEDIA_FORMAT, merged by ffmpeg if needed
            │     └─ on DownloadError → _image_fallback()   is the post merely video-less?
            │            ├─ is_image_post()          no formats + thumbnails + not a carousel
            │            └─ _download_best_thumbnail()  fetch all, keep the largest file
            ├─ media_kind()              photo | animation | video, from suffix + presence of audio
            ├─ delivery_decision()       real bytes on disk vs the per-kind ceiling
            │     ├─ "file" → _send()        reply_video / reply_photo / reply_animation
            │     └─ "link" → oversize_reply()  a Spanish message carrying the direct URL
            └─ except → _apologise()     FAILURE_REPLY, with its own timeouts, never re-raises
```

The split that matters: **everything above `_deliver` is pure and testable without a network or a
token.** That is what makes the self-check possible.

### The pure layer (no I/O, all covered by asserts)

| Function | Contract |
|---|---|
| `find_urls(text)` | every `http(s)://` URL in order, de-duplicated, trailing punctuation stripped |
| `is_supported(url)` | host is in `SUPPORTED_HOSTS` or a subdomain of one |
| `media_kind(path, has_audio)` | `photo` by suffix · `animation` for `.gif` **or a silent clip** · else `video` |
| `reply_method_name(kind)` | the `telegram.Message` method that renders that kind inline |
| `upload_ceiling(kind)` | 10 MiB for photos, 50 MiB for everything else |
| `delivery_decision(bytes, kind)` | `"file"` or `"link"` |
| `video_kwargs(media)` | `supports_streaming` plus width/height/duration **when known**, never zeros |
| `telegram_renders_inline(container, codec)` | mp4 container and not AV1 |
| `is_image_post(info)` | the post has **no** video formats, **has** thumbnails, and is not a carousel |
| `oversize_reply(bytes, link)` | the Spanish fallback message |
| `conflict_action(now, started, last)` | `announce` · `quiet` · `give-up` for a poll conflict — §2.1 |

---

## 2. Run it

Requires **Python 3.11+** and **ffmpeg on `PATH`** (`brew install ffmpeg` / `apt install ffmpeg`).
ffmpeg is not optional: 720p on YouTube is a merge of a separate video and audio stream.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# the token lives in .env, which is gitignored and never committed
printf 'TELEGRAM_BOT_TOKEN=<your-token>\n' > .env && chmod 600 .env

set -a; . ./.env; set +a
.venv/bin/python bot.py
```

The bot runs for as long as that terminal is open. To survive closing it:

```sh
nohup .venv/bin/python bot.py > ~/the-bot.log 2>&1 &
```

`tmux` or `screen` work equally well. **There is deliberately no `systemd` unit and no `launchd`
plist in this repo** — see §6.

### 2.1 The baton pass

The owner cannot keep his laptop on all the time, so hosting rotates: whoever is around
double-clicks a launcher and hosts the bot until they close the window. `run-bot.command` is the
macOS one, `run-bot.cmd` the Windows one (**untested on real Windows** — see `docs/updating.md`).
Each one checks for updates, Python 3.11+, ffmpeg, the venv, the token and whether anybody else is
already polling, and prints one Spanish sentence per failure. The friend-facing instructions are
`EMPEZAR-ACA.md`, in Spanish, because the audience is.

Three things follow from the design and are not obvious:

- **Only one person can host at a time.** Telegram allows exactly one poller per token (§4.9), so
  this is a baton pass, not parallelism. The launcher asks before taking over.
- **Distribution is `git clone`, never a downloaded zip**, and that is also the update channel: each
  launcher runs `git pull --ff-only` on startup, so the owner pushes and every friend gets the
  change on their next double-click. It also side-steps Gatekeeper entirely — a file written by git
  carries no `com.apple.quarantine` attribute, a downloaded one does. Verified 2026-08-07: a
  quarantined `.command` is refused by LaunchServices with `userCanceledErr` and never runs, while
  the same file cloned runs on a double-click.
- **Whatever was posted while nobody was hosting is dropped**, not replayed — `run_polling` is
  called with `drop_pending_updates=True`. Telegram holds updates for ~24 h and a handover always
  follows a gap, so the default would dump the whole gap into the group at once. Measured with
  nobody running: 7 updates queued, 2 of them reels. The cost is that a link posted while the bot
  was off never arrives.

The owner's workflow is `docs/updating.md`. Nothing else here changes: `python bot.py` above is
still the way the owner runs it, and the launcher is a convenience wrapped around exactly that.

### Self-check

```sh
.venv/bin/python bot.py --self-check
```

~25 seconds. 14 assertion groups, then **four real downloads** — YouTube, an Instagram reel, an
Instagram image post and Facebook — each probed with `ffprobe` to confirm what Telegram will
actually receive, and each asserted to come back as the *kind* it should be. It exits non-zero on
the first failure. Run it before every commit, together with `python -m py_compile bot.py`.

---

## 3. BotFather setup — the bot does nothing without this

New bots run in **privacy mode**: they only receive messages addressed to them, so a plain link
pasted in a group is invisible. There are two ways out and **the second is the one most people
actually need.**

**Option A — make the bot a group administrator.** Administrators receive every message regardless
of privacy mode. No BotFather step, no removing anyone. **Measured, not quoted:** on 2026-08-07 a
bot sitting silently in a group started receiving plain links the moment it was promoted;
`getChatMember` went from `status: member` to `status: administrator`. This is the only option
available to someone who is not the group's admin — they can ask an admin to promote the bot.

**Option B — disable privacy mode in BotFather.** `/setprivacy` → pick the bot → **Disable**, and
then **remove the bot from the group and add it again.** The setting only applies to groups joined
*after* the change. This was also measured: a bot added at 14:44 with privacy still on received
nothing but service messages, while one added at 15:03 after the toggle received plain text
immediately.

Verify which state you are in without guessing:

```sh
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe" | grep -o '"can_read_all_group_messages":[a-z]*'
```

`true` means privacy mode is off at the bot level. It does **not** guarantee the bot receives text
in a group it joined earlier — that is what Option A or the re-add fixes.

---

## 4. Measured facts — do not re-derive these wrong

Everything below was measured on **2026-08-07** on macOS 15.1.1 arm64, Python 3.11.7,
yt-dlp 2026.07.04, python-telegram-bot 22.8, from a residential IP. Method is stated so the blind
spots are visible. **Re-verify before relying on any of it — platform behaviour drifts weekly.**

### 4.1 What Telegram renders inline

Four files uploaded to a real group with `sendVideo`, classified by reading the API's own response:

| Container / codec | Size | Telegram returned |
|---|---|---|
| mp4, **h264** + aac | 1.79 MB | `video` |
| mp4, **vp9** + aac | 1.25 MB | `video` |
| mp4, **h264** + aac | 28.58 MB | `video` |
| **webm, av01 + opus** | 20.03 MB | **`document`** — a grey file row, no playback |

**The rule is "mp4 container, not AV1" — it is NOT "h264 only".** vp9 played inline. That is why
`telegram_renders_inline()` is written the way it is; tightening it to h264 would reject files that
work. This is the single most load-bearing fact in the repo, and `MEDIA_FORMAT` exists to satisfy it.

### 4.2 Format selection

```
MEDIA_FORMAT = "bv*[height<=720][vcodec^=avc1]+ba[acodec^=mp4a]/"   # 1. YouTube: merged h264+aac
               "b[ext=mp4][height<=?720]/"                          # 2. IG/FB: their own ready mp4
               "bv*[height<=720]+ba/"                               # 3. safety net
               "b/bv*+ba"                                           # 4. anything at all
```

- Branch 1 costs about **9 MB** on YouTube versus the codec-agnostic alternative (29 MB h264 vs
  21 MB AV1). That is the price of inline playback and it is worth paying against a 50 MB ceiling.
- Branch 2's `height<=?720` — note the `?` — lets an **unknown** height through. Instagram and
  Facebook report every field as `unknown` on their single-file mp4; without the `?` neither site
  matches any branch and selection falls through to the unmetered `b`.
- **A height cap is not a size guarantee.** Facebook serves portrait DASH (720x900, 1080x1350,
  1440x1800), all of which have a height above 720 while being small files. The only real size
  guard is the byte count of the finished file.
- Raising the cap to 1080p is **not** free at this codec preference: the same YouTube video measures
  **~84 MB** in h264 at 1080p, over the ceiling. The 34 MB figure that circulated during development
  was the AV1 number and does not apply.

### 4.3 Sizes and timings actually observed

| Source | File | Upload |
|---|---|---|
| Instagram reel, 30 s | 1.27 MB, 772x720 h264 | 4 s |
| Facebook reel, 27 s | 0.44–1.88 MB, 720x900 h264 | 3 s |
| YouTube, 19 s | 0.63 MB, 320x240 h264 | — |
| YouTube, 3.5 min | 17.6–30.0 MB, 1280x720 h264 | 53 s – 216 s |

Upload throughput varied roughly threefold between two networks on the same day. `UPLOAD_TIMEOUT`
is sized against the slow end.

### 4.4 Telegram's ceilings

**50 MB** for video, animation, audio and documents; **10 MB** for photos
(`core.telegram.org/bots/api#sending-files`, checked 2026-08-07). Encoded per kind in
`upload_ceiling()`.

### 4.5 Timeouts — the four that matter

| Constant | Value | Why |
|---|---|---|
| `CONNECT_TIMEOUT` | 30 s | python-telegram-bot defaults it to **5.0 s** and only substitutes its own default when the caller passes nothing. A 1.2 MB upload failed twice in production with `telegram.error.TimedOut` **five seconds** after starting — the handshake gave up before `UPLOAD_TIMEOUT` was ever consulted. |
| `UPLOAD_TIMEOUT` | 600 s | `write_timeout` is a **whole-upload deadline**, not per-chunk: PTB loads the file into `bytes`, httpx renders it as one chunk, httpcore applies the timeout per chunk. 600 s covers 50 MiB down to ~87 kB/s. |
| `TEXT_REPLY_TIMEOUT` | 60 s | the failure reply gets its own budget — see §4.6 |
| `SOCKET_TIMEOUT` | 20 s | yt-dlp's network knob. Note `timeout(1)` does not exist on macOS; this is the real one. |

### 4.6 The failure path is the one that bites

In production, an upload timed out **and the apology timed out too**, escaped `_deliver`, and the
group received nothing at all — no video and no "no pude bajar ese link". A network bad enough to
fail a 1.2 MB upload is frequently bad enough to fail a text reply, so `_apologise()` is a separate,
protected attempt with its own timeouts that logs and never re-raises. **It is the last line of
defence and it is the one place in this file where swallowing an exception is correct.**

### 4.7 Anonymous extraction

All three sites extract **without cookies**. `PLAN.md` claimed Instagram needed them and Facebook
was broken; both claims were wrong, and so was a 2026 web search asserting Instagram has required
authentication since 2024. What actually fails is the Instagram **profile page** URL shape
(`instagram.com/<user>/`), which yt-dlp marks broken upstream — that is what the original probe
tested. Reels and `share/v/` · `share/r/` links work fine, and so do image-only `/p/` posts — see
§4.8, which is a different mechanism entirely.

`YTDLP_COOKIES` exists as insurance, not a requirement: point it at a Netscape-format cookie file
and yt-dlp gets it; leave it unset and nothing changes. If it ever becomes necessary, use a
**throwaway account** — the ban risk is real and the account is the price.

### 4.8 Instagram image posts

An Instagram `/p/` post with no video in it fails extraction with `There is no video in this post`.
That is not a broken extractor — the post is simply an image, and the image is reachable
anonymously as a thumbnail. Measured 2026-08-07 on `instagram.com/p/DbvWPFQxPkI/`:

| | |
|---|---|
| delivered | `1072x1197` JPEG, **191,815 B**, `image2/mjpeg` → `reply_photo` |
| ceiling | 10 MiB for photos, so ~1.9% of it |
| cookies | none needed |

Three things here are counter-intuitive and cost time if you re-derive them wrong:

- **`ignore_no_formats_error` does nothing on the download path.** yt-dlp's `dl()` calls
  `raise_no_formats(info, forced=True)`, and the `forced` arm raises regardless of the flag. It
  works **only** while extracting with `download=False`. This is why the fallback probes separately
  instead of folding the flag into `_ydl_options` — which also keeps the video path untouched.
- **The thumbnail list is not sorted worst-to-best, and an image post's thumbnails carry no
  dimensions at all** — only `id` and `url`, unlike a reel's, which do have width/height. The 13
  entries of the reference post run 1149k pixels at index 0, 22k at index 1, climbing to 1283k at
  index 12: two interleaved ladders, square crops then aspect-correct. So there is nothing to sort
  by and no reliable "last is best". All thumbnails are downloaded and **the largest file wins**;
  bytes track pixels for one image re-encoded at one quality.
- **`duration` and `title` discriminate nothing.** `duration` is `None` for an image post *and* for
  a working reel, and `title` is `"Video by <author>"` even for an image — Instagram's generic
  caption. `formats` is the only usable signal.

**A video whose formats fail must stay an error, never its poster frame.** Two halves, both
verified: `is_image_post()` checks `formats` first and refuses anything that has them; and upstream,
an auth-walled reel and a bogus shortcode both still raise `DownloadError` even with the flag set,
so a genuinely broken extraction never reaches the fallback.

**Carousels are refused, not guessed.** yt-dlp models a carousel as a playlist of entries and its
handling of mixed photo/video ones is an open upstream problem (yt-dlp #7569, #11792). No public
carousel was available to measure, so a multi-entry info dict returns `False` and the group gets the
apology. The single-image reference post reports `entries: 0`, so nothing measured is affected.

### 4.9 One poller per token

Telegram allows exactly one `getUpdates` poller per token, and this is the constraint the whole
baton pass is built around. Measured 2026-08-07:

| What was done | What happened |
|---|---|
| two concurrent `getUpdates` on one token | **both** got HTTP **409**, `Conflict: terminated by other getUpdates request; make sure that only one bot instance is running` |
| one competing `getUpdates` against the running bot | the bot **did not exit**; it kept retrying and logged **6 conflict lines and 3 tracebacks in ten seconds** |

The second row is why `on_error` exists: that wall of text was unreadable to the friend whose window
it was, for an event that is completely normal here. It is also why a conflict is *tolerated* rather
than fatal — the launcher's own "is anybody running?" probe is a competing `getUpdates` call by
construction, so merely asking costs the running instance one conflict. Exiting on the first one
would turn the question into a remote kill switch. `CONFLICT_GRACE` (60 s) is six times the measured
blip; `CONFLICT_EPISODE_GAP` (45 s) is above python-telegram-bot's own retry backoff, which grows
1.5× per failure and is **capped at 30 s** (`telegram/ext/_utils/networkloop.py`).

Not verified without a second live instance: that python-telegram-bot delivers the `Conflict` to the
registered error handler at all. The path is asserted from `conflict_action` up to `on_error`, and
the library's own code says it does (`Application.run_polling` passes an `error_callback` that feeds
`process_error`), but nobody has watched it happen.

---

## 5. Operations — what actually breaks

**The application logic will not rot. The extractors will.** YouTube, Instagram and Facebook change
their pages without warning, and that is this project's real failure mode.

1. **Videos stop downloading → update yt-dlp first.** `.venv/bin/pip install -U yt-dlp`. This fixes
   the large majority of breakages and nothing else is worth trying before it.
2. **The bot ignores a link → read the log.** `on_message` says which of the two happened:
   - `message N: no URL recognised, nothing to do` — the text had no `http(s)://` URL. **Known
     limitation:** Telegram renders bare `youtu.be/xyz` as a clickable link, but the raw text has no
     scheme and `URL_PATTERN` requires one. The upgrade path is reading `message.entities` and
     letting Telegram decide what a link is.
   - `message N: 2 URL(s) found, none on a supported host -- rejected: …` — the URLs are printed
     because they are the entire diagnosis. Add the host to `SUPPORTED_HOSTS` if it belongs there.
3. **The bot stops seeing anything in a group** → it was probably demoted from administrator. See §3.
4. **YouTube fails with missing formats** → the first suspect is the warning yt-dlp prints on every
   extraction: *"No supported JavaScript runtime could be found… extraction without a JS runtime has
   been deprecated."* It is harmless today (2160p and merged 720p both resolve without one) and no
   runtime is installed on purpose — this project is meant to be copied onto an old Linux box. The
   escape hatch is `--js-runtimes node` or installing `deno`.
5. **A video arrives as a grey file row instead of playing** → the format string picked AV1 or a
   webm. §4.1 and §4.2. The self-check catches this; run it.
6. **The log says `another instance has taken the poll`** → somebody else opened a launcher. One
   line means somebody merely *asked* whether the bot was running and this instance recovered;
   `the conflict lasted 60 s` means the handover was real and this instance stopped on purpose.
   §2.1 and §4.9.

### 5.1 The rejected-links ledger

Every supported link that does **not** end in delivered media appends one JSON line to
`rejected.jsonl`, next to `bot.py`. Gitignored — it is the group's content.

```sh
.venv/bin/python bot.py --rejected
```

It groups by error class first, then by host, and lists every URL underneath. That order is the
diagnosis: the class says what *kind* of thing is going wrong — one rotted extractor looks nothing
like a run of files over the ceiling — and the host says where, which is usually the fix.

Two things about the records:

- **The message body is never written**, only the URL. Same rule as the ignore-logging in item 2
  above — this is a private group and the URL is the whole diagnosis.
- **`error` is the exception class name**, except for a file too big to upload: nothing failed
  there, so that record carries `OversizeForTelegram` and the byte count. It is deliberately in the
  ledger because a link reply is not the media, and because that path has still never run against
  Telegram (§6).

**The ledger fragments across hosts, and that is accepted, not overlooked.** Each friend's machine
records only the bounces it saw and nothing merges them. At ~20 links a week the owner reading his
own file, and asking a friend to send theirs when a week is missing, costs less than any sync would.
The format is append-only lines, so `cat` is the merge. Do not build syncing for this (§6).

A failure to write the ledger is swallowed and logged: it is diagnostics bolted onto the failure
path, and it may never cost the group its apology.

---

## 6. Deliberately not built

Each of these is a decision with a reason, not an oversight. Re-opening one needs new evidence, not
a preference.

| Not built | Why |
|---|---|
| Database, job queue, web framework, Docker | 20 links a week. Cost with no benefit. |
| `systemd` unit, `launchd` plist, any process manager | Belongs to the port onto the spare Linux machine, not here. Adding it now ties the repo to macOS. |
| Auto-start for the launcher — a LaunchAgent, a Startup shortcut | The baton pass is manual on purpose. Two friends who each installed one would quietly re-create the 409 problem every morning, with nobody at the keyboard to answer the question the launcher asks. |
| A cross-platform launcher, a Python bootstrapper, a shared config for the two scripts | Two ~100-line scripts that each read plainly in their own idiom beat one clever thing neither platform's user can debug. |
| A lock file to answer "is anybody hosting?" | It would live on the wrong machine and go stale. Telegram's own 409 is the only authority, and asking costs one conflict (§4.9). |
| A JS runtime (`deno`, wiring `node`) | Extraction works without one today, measured. The port target argues against a new runtime dependency added to silence a warning. |
| A local Telegram Bot API server for 2 GB uploads | Compiling tdlib for a ceiling meme-length clips rarely reach. |
| TikTok support | Not requested, and the IP was blocked when it was probed. |
| An Instagram throwaway account and cookies | Unnecessary — anonymous extraction works (§4.7). |
| Syncing the rejected-links ledger between hosts | It fragments by design (§5.1). A server or a shared database for ~20 links a week is exactly the cost this repo refuses; `cat` merges the files when the owner actually wants them merged. |
| Playlists, channels, a dashboard, accounts, rate limiting | Out of scope, permanently. |
| A retry loop or a global PTB error handler | The failure path is local and small on purpose (§4.6). |
| A second Python file | See DESIGN LAW 1 in `AGENTS.md`. |

---

## 7. Repository layout

```
bot.py                    the whole application, plus its self-check
run-bot.command           the macOS launcher. Committed 100755 or it does not double-click.
run-bot.cmd               the Windows launcher. Untested on real Windows.
requirements.txt          pinned: yt-dlp[default,curl-cffi]==2026.7.4, python-telegram-bot==22.8
README.md                 this file
EMPEZAR-ACA.md            the friend-facing quickstart, in Spanish. Product copy, not docs.
AGENTS.md                 the rules an agent must not violate, plus routing
docs/updating.md          how the owner ships a change and how a new friend gets set up
docs/history.md           how the project got here: measurements, killed premises, decisions
docs/RUN-STATE.md         the full run log of the 2026-08-07 build session
docs/archive/             the original plan and prompt-order, superseded, kept for provenance
.env                      the token. gitignored. never commit it.
rejected.jsonl            the bounce ledger this machine wrote. gitignored. §5.1
.venv/                    gitignored, and it also holds the launcher's dependency stamp
```

Nothing in this repo is generated. Everything tracked is either the application, its pins, or a
document a human or an agent is expected to read.
