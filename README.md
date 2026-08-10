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
  ├─ /start                      what the bot does, and a pointer to /instalar
  ├─ /instalar [mac|windows]     how to host it, as a message — §2.2
  │   ├─ install_platform(text)      which platform was asked for; nothing sensible
  │   │                              recognised means both, never an error
  │   └─ install_reply(platform)     Spanish, Telegram HTML, one code block each
  │
  └─ on_message                  filters: real messages only, text or caption
     │
     ├─ _handle_links(message)   FIRST, because _deliver cannot raise
     │   ├─ message_urls(message)    the union of two sources, de-duplicated
     │   │    ├─ find_urls(text)         regex, http(s):// required
     │   │    └─ entity_urls(message)    Telegram's own `url` entities, so a
     │   │                               schemeless youtu.be/xyz is seen too
     │   ├─ is_supported(url)        host allow-list; everything else is logged AND
     │   │                           written to the ledger as UnsupportedHost
     │   └─ _deliver(url)
     │        ├─ temp_workspace()          mkdtemp, removed in a finally
     │        ├─ download_into()           yt-dlp, format MEDIA_FORMAT, merged by ffmpeg if needed
     │        │     ├─ is_live_stream()         match_filter, so a live stream is refused
     │        │     │                           before a byte is written — §4.13
     │        │     └─ on DownloadError → _image_fallback()   is the post merely video-less?
     │        │            ├─ has_image_posts(url)     Instagram, or no fallback at all
     │        │            ├─ is_image_post()          no formats + thumbnails + not a carousel
     │        │            │     └─ _download_best_thumbnail()  fetch all, keep the largest file
     │        │            └─ carousel_slides()        every entry an image, 2 or more
     │        │                  └─ _download_carousel_slides()  the same rule, per slide
     │        ├─ delivery_kind()           album when there are slides, else media_kind()
     │        ├─ delivery_decision()       real bytes on disk vs the per-kind ceiling
     │        │     ├─ "file" → _send()        reply_video / reply_photo / reply_animation
     │        │     │        → _send_album()   reply_media_group, open files, 2-10 slides
     │        │     └─ "link" → oversize_reply()  a Spanish message carrying the direct URL
     │        ├─ record_rejection()        anything that was not delivered lands in rejected.jsonl
     │        └─ except → _apologise()     failure_reply() names the cause when it can — the
     │                                     exception's CLASS first, then its text, else
     │                                     FAILURE_REPLY; own timeouts, never re-raises
     │
     └─ _handle_insult(message)  the one thing it answers that is not a link
         ├─ insult_words(text)       "bot estupido", either order, typos and all — §4.12
         ├─ record_insult()          insults.jsonl: when, chat, message, the two words
         └─ _reply_text()            "Lo lamento, hago lo que puedo"
```

The split that matters: **everything above `_deliver` is pure and testable without a network or a
token.** That is what makes the self-check possible.

### The pure layer (no I/O, all covered by asserts)

| Function | Contract |
|---|---|
| `find_urls(text)` | every `http(s)://` URL in order, de-duplicated, trailing punctuation stripped |
| `entity_urls(message)` | every `url` entity Telegram marked, with `https://` added when it has no scheme — §4.11 |
| `message_urls(message)` | the union of the two, regex first, de-duplicated |
| `is_supported(url)` | host is in `SUPPORTED_HOSTS` or a subdomain of one |
| `has_image_posts(url)` | host is Instagram, the only site that has image posts — §4.8 |
| `insult_tokens(text)` / `insult_words(text)` | the message as bare words, and the two that insulted the bot — §4.12 |
| `media_kind(path, has_audio)` | `photo` by suffix · `animation` for `.gif` **or a silent clip** · else `video` |
| `reply_method_name(kind)` | the `telegram.Message` method that renders that kind inline |
| `upload_ceiling(kind)` | 10 MiB for photos, 50 MiB for everything else |
| `delivery_decision(bytes, kind)` | `"file"` or `"link"` |
| `video_kwargs(media)` | `supports_streaming` plus width/height/duration **when known**, never zeros |
| `telegram_renders_inline(container, codec)` | mp4 container and not AV1 |
| `is_image_post(info)` | the post has **no** video formats, **has** thumbnails, and is not a carousel |
| `is_live_stream(info)` | the link is a stream happening **now**, so it has no end — `is_live` or `live_status`, never `was_live` — §4.13 |
| `carousel_slides(info)` | the entries of an all-image carousel of 2+ slides, else `[]` — §4.10 |
| `delivery_kind(media)` | `album` when it carries slides, else `media_kind` |
| `delivered_files(media)` | every file this will put in the chat: the slides, or the one file |
| `oversize_reply(bytes, link)` | the Spanish fallback message |
| `album_truncation_note(total, sent)` | the Spanish warning for a carousel longer than one album |
| `rejection_record(...)` / `format_rejections(records)` | one ledger line, and the grouped report — §5.1 |
| `install_platform(text)` / `install_reply(platform)` | which platform "/instalar algo" meant, and the instructions for it — §2.2 |
| `take_over_requested(argv)` | whether this instance was told to take the bot from somebody else — §4.9 |
| `conflict_action(now, started, last, take_over_until)` | `announce` · `take-over` · `quiet` · `stand-ground` · `give-up` for a poll conflict — §2.1 |

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

Add `--take-over` to that command to reclaim the bot from a friend who is hosting it: it is the same
declaration the launcher collects with its `[s/n]` question, and it is what stops this instance from
yielding the moment it meets the other one (§2.1, §4.9). Without it, starting a second instance is
how the group ends up with no bot at all.

### 2.1 The baton pass

The owner cannot keep his laptop on all the time, so hosting rotates: whoever is around
double-clicks a launcher and hosts the bot until they close the window. `run-bot.command` is the
macOS one, `run-bot.cmd` the Windows one (**untested on real Windows** — see `docs/updating.md`).
Each one checks for updates, Python 3.11+, ffmpeg, the venv, the token and whether anybody else is
already polling, and prints one Spanish sentence per failure. The friend-facing instructions are
`EMPEZAR-ACA.md`, in Spanish, because the audience is.

Three things follow from the design and are not obvious:

- **Only one person can host at a time.** Telegram allows exactly one poller per token (§4.9), so
  this is a baton pass, not parallelism. The launcher asks before taking over, **and the answer is
  the only thing that tells the two running instances apart**: an `s` runs `bot.py --take-over` and
  that instance keeps polling through the conflict, while the one nobody told anything yields after
  60 s. Both sides read the identical HTTP 409, so without that flag both conclude they lost and the
  group is left with no bot at all — measured on 2026-08-09, §4.9.
- **On macOS, distribution is `git clone` and never a download**, and that is also the update
  channel: the launcher runs `git pull --ff-only` on startup, so the owner pushes and every friend
  gets the change on their next double-click. It also side-steps Gatekeeper entirely, and it is what
  makes the launcher double-clickable at all: git restores the exec bit and writes no
  `com.apple.quarantine`, and a downloaded file has the first missing and the second present. A
  `.command` needs both to run — measured, §2.2.
- **On Windows there is a second way in, because a `.cmd` needs no exec bit**: `instalar-bot.cmd`, a
  downloaded bootstrap that fetches the repository as a tarball and hands off to `run-bot.cmd` (§2.2).
  It brings its own update channel with it — it re-fetches on every double-click — so **`git push` is
  still the entire release process for both kinds of copy**. Which copy has which updater, and the
  two rules that stop them contradicting each other, are in `docs/updating.md`. Untested on real
  Windows, like the launcher beside it.
- **Whatever was posted while nobody was hosting still arrives** — `run_polling` is called with
  `drop_pending_updates=False`, so **a host who starts after a gap sees that gap's links delivered
  at startup**, oldest first and one at a time. Telegram only holds updates for ~24 h, so a gap
  longer than that still loses the older end of it. Measured with nobody running: 7 updates queued,
  2 of them reels — that is the size of backlog a handover recovers, not a flood. Chosen 2026-08-10
  over the previous behaviour, which dropped the queue and told nobody. Two consequences worth
  knowing: the arrivals are not new messages, so they can be minutes or hours old; and a baton pass
  (above) may repeat the newest link, because the yielding instance cannot acknowledge its last
  batch through the 409 that made it yield.

The owner's workflow is `docs/updating.md`. Nothing else here changes: `python bot.py` above is
still the way the owner runs it, and the launcher is a convenience wrapped around exactly that.

### 2.2 The bot hands out its own installer

`/instalar` replies with what a friend needs in order to become a host: **on macOS the one line they
paste, on Windows the one file they download.** It works in the group and in a DM, and Telegram lists
it in the command menu, so nobody has to be told it exists.

**It is a message and never an attachment, and on macOS that is forced rather than chosen.** Three
facts rule an attachment out there — not only sending `run-bot.command` itself, but sending any small
bootstrap that would clone the repo and hand off to it. Two of them are about what a downloaded file
*is on macOS*, and were measured on 2026-08-09, macOS 15.1.1 (24B91), by opening one script four
times and checking whether its body actually ran:

| file mode | `com.apple.quarantine` | double-click runs it |
|---|---|---|
| 644 | absent | **no** — Terminal opens, the script never executes |
| 755 | absent | yes |
| 755 | present | **no** |
| 755 | removed again | yes (control) |

**A file that arrived over Telegram is in the first row and the third one at once.** Telegram
Desktop 7.0.9 (`com.tdesktop.Telegram`) declares `LSFileQuarantineEnabled` with no exclusions, and
the files already saved out of it on the owner's Mac are mode `644` carrying `com.apple.quarantine`
with agent `Telegram`. So an attached `.command` is refused twice over, and **right-click → Open is
not the fix**: it answers the quarantine half and cannot add an exec bit. The only thing that adds
one is `chmod +x` in a Terminal — which is the step that sending a file exists to avoid, so the
attachment is strictly worse than the pasted line, not better.

The third fact is the older one: **a launcher on its own is inert.** `run-bot.command`'s first act is
`git pull` in a directory with no `.git`, and its last is `exec .venv/bin/python bot.py` with no
`bot.py` beside it. It also needs `requirements.txt`. This objection, on its own, has a cheap answer
— a bootstrap that clones and then hands off would still be a file you double-click, and would still
land the friend in a real clone — so it is not what closes the question. The two measurements above
are.

Shipping the whole repository as an archive fails for a fourth reason, and it is the one that turns
out to be about the *updater* rather than the archive: with no `.git`, nothing in the folder can ever
`git pull`, so it is a snapshot that rots — the opposite of what this is for. The one command that
creates a real clone travels as text, so nothing is quarantined and nothing is missing a mode bit:
git restores both, which is exactly why a cloned `run-bot.command` runs on a double-click when the
same bytes downloaded do not. It lands the friend in a working copy whose launcher pulls on every
startup. **That is what keeps every friend current: the owner pushes, and the next double-click has
it.**

#### Windows gets a link instead, and it is the only platform where that can work

Everything above is a macOS measurement, and one line of it does not carry across: **a `.cmd` needs
no exec bit.** So a downloaded launcher-shaped file genuinely runs on Windows, where a downloaded
`.command` cannot. That asymmetry is the whole reason `/instalar windows` hands out a **link** and
`/instalar mac` still hands out a command, and it is why there is no macOS twin of the bootstrap.

What the link points at is not `run-bot.cmd`. A launcher on its own is inert on Windows for exactly
the reasons it is inert on macOS — its first act is `git pull` in a folder with no `.git` and its
last is running a `bot.py` that is not there — so **linking the launcher would move the git step, not
remove it**. The link points at `instalar-bot.cmd`, a bootstrap that fetches the repository as a
source tarball, unpacks it into the folder a clone would have made, and hands off to `run-bot.cmd`.
It re-implements nothing else: Python, ffmpeg, the venv, the token and the hand-over question all
still belong to the launcher.

Three things make it work, and each was measured on 2026-08-10 from macOS:

- **GitHub serves the tarball to nobody in particular.**
  `codeload.github.com/…/tar.gz/refs/heads/main` answers `HTTP/2 200`,
  `content-disposition: attachment`, `content-type: application/x-gzip`, with no account, no
  credentials and no git.
- **`curl` and `tar` already ship with Windows** (System32, since Windows 10 build 17063 / 1803), so
  nothing is installed on the friend's machine and no binary is ever downloaded. The bootstrap calls
  both by full path — a bare `tar` can resolve to Git for Windows' GNU tar, which reads `C:\…` after
  `-f` as a remote host and fails.
- **Re-running it cannot cost the friend their token.** `tar` overwrites the members of the archive
  and never touches a file that is not in it, and the archive is the *tracked* tree of `main`, so
  `.env` and `.venv/` are absent from it by construction. Verified by unpacking the real archive
  three times over a folder holding both.

**That the script runs at all is untested**: there is no Windows in this project. `docs/updating.md`
lists what was measured, what was only read from documentation, and what the first Windows friend
should be watched for.

**A tarball copy and a clone update differently**, which is a real cost of this and not a detail:
`git pull` for a clone, a re-fetch for a download. Two rules keep them from contradicting each other.
The bootstrap **refuses to unpack over a `.git`**, so they never fight over one folder. And
`run-bot.cmd` reads the `.tarball-install` stamp the bootstrap leaves, because *"esta copia no se
puede actualizar sola"* — the sentence it printed whenever there was no `.git` — is **false** of a
downloaded copy: that copy has an updater, it is just not git, and no question you can ask `git`
distinguishes the two. What it says instead names the installer rather than claiming the copy is
current, since opening the launcher directly updates nothing. The zip's sentence survives untouched
and is asserted, because for a hand-unpacked zip it was always true. `docs/updating.md` has the
table of which copy has which.

`mac` and `windows` are the arguments, and **bare is the common case, not the fallback** — a tap on
the command menu sends `/instalar` with nothing after it, and this audience taps. Bare answers with
both platforms; so does anything unrecognised, because the person asking is the person who does not
know the words and an error message would be the worst possible reply. Every word after the command
is looked at, so `en windows` and `para mac` work.

The reply is the only place in `bot.py` that sets a `parse_mode`, and it is `HTML`. The code block
is what makes Telegram offer tap-to-copy; MarkdownV2 would have meant escaping `.` `-` `(` `)` `!`
in every Spanish sentence, a standing trap for the next line of copy, while HTML needs `&` `<` `>`
and nothing else. Everything interpolated goes through `html.escape` — the `&&` in the pasted
command is the one that bites. **`_reply_text` still defaults to no parse mode**, and that default
is load-bearing: the apology, the oversize line and the insult answer are unescaped Spanish, one of
them carrying a raw URL.

**Two things the reply says, and its length is the third decision.** One platform is **three
lines** — what to do, the thing to act on, the token — and both platforms are **five**. That ceiling
is the feature and it is asserted, not a style note: the wall it replaced was 12 lines per platform
and **22 bare**, and the audience taps and skims, so the one line that matters was the one that got
skimmed past. A line earns its place by stopping a friend in the next minute, and exactly two do per
platform. **The first one is the obstacle that platform actually has, and they are different
obstacles**: on macOS git, because running it without the Command Line Tools pops Apple's installer
dialog; on Windows nothing to install at all, so the line spends itself on what the machine will ask
before it runs a downloaded file — plus the one wrinkle in the download, which is that
raw.githubusercontent.com serves a `.cmd` as `text/plain` with no `content-disposition` (measured), so
a browser is expected to show it rather than save it. The second is the same on both: the
**token comes from the owner, separately**, which the launcher asks for on first run.

The budget did not move when Windows changed mechanism, and that is the check that matters most here:
two lines per platform before and after, so a link cost nothing and bought a step.

**What was cut went somewhere, it was not dropped.** *Only one person hosts at a time* is not
something a friend acts on while reading a chat message: the launcher asks before taking over, at
the moment it matters (§2.1), and `EMPEZAR-ACA.md` opens with it. *From now on it is a double-click
and it updates itself* is the same, and lives under "Cada vez" in that file — which arrives with the
code either way, clone or tarball, so every reader of this reply gets it within the minute. The
bootstrap also says the one thing that file cannot know, in its own window: that for a downloaded copy
*this* is the everyday file, since it is the one that updates. The bare reply also
stopped spending a line advertising `/instalar mac`: `EMPEZAR-ACA.md` offers both narrow forms, and
tapping that line in Telegram would have sent the bare command again anyway, since only the
`/instalar` part is a tappable command and the ` mac` after it is plain text.

**The clone URL is hardcoded, not derived.** `git remote get-url origin` would answer at runtime,
but it would put a subprocess on a path anyone who can message the bot can trigger, and it would
hand friends whatever *that host's* remote happens to be: an SSH remote (`git@github.com:…`) is
useless to a friend with no GitHub account, and a copy with no `.git` — which both launchers
tolerate and say so — would have nothing to give at all. The cost is a string that goes stale if the
repository moves, and it cannot go stale *silently*: the self-check compares it against this
checkout's own `origin` by `owner/repo`, so an SSH origin still matches, and separately refuses
anything that is not `https://`. Verified 2026-08-09 with credential helpers disabled:
`git ls-remote` on that URL answers anonymously, so a friend with no GitHub account can clone.

**The download link is derived from that same string, and so is the check on it.** `BOOTSTRAP_URL` is
`CLONE_URL` rewritten to `raw.githubusercontent.com`, the way `CLONE_DIR` is derived rather than
written out: a second hand-typed copy of the repository's name is a second thing that can point
friends somewhere else. The repository is now named in four places — that constant, the reply's link,
`EMPEZAR-ACA.md`, and the `codeload` URL inside `instalar-bot.cmd` — and the last one is the dangerous
one, because a friend who downloads that file runs whatever *it* fetches. So the self-check opens
`instalar-bot.cmd` and asserts it fetches the archive of the same `owner/repo`, and that its
`--exclude` still names itself. Both are skipped when the file is absent, which is the normal state of
a downloaded copy: the bootstrap excludes itself from its own unpack, so `run-bot.cmd` is the only
`.cmd` that lands in a friend's folder.

#### The reply can never contain the token

This is the one thing in the feature that can do real harm. The bot answers **anybody who can
message it**, in any group it was added to, while the process holds the group's token in its
environment. One interpolation turns onboarding into a credential leak.

The guard is **invariance, not a filter**. A filter only catches the value it was told to look for.
Instead, every string this feature can produce — the three builder variants *and* what `on_install`
actually sends, in the DM and group forms — is built twice under two different, correctly shaped
fake tokens, and once with no token in the environment at all, and all three runs must come out
byte-identical. **A text that does not change when the token changes cannot contain it.** Name and
shape asserts are kept alongside it, so an ordinary leak fails on three counts; the invariance
assert is the one that catches a *transformed* leak, which the other two cannot see. The bait itself
is asserted too: both fakes must match the token shape and must differ from each other, or the rule
they arm is decoration.

What makes it hold is that `install_reply` and everything under it read **nothing** — no
environment, no file, no subprocess. That is a security property here, not a style preference.

### Self-check

```sh
.venv/bin/python bot.py --self-check
```

Around a minute. Assertion groups first, then **six real downloads** — YouTube, an Instagram reel,
an Instagram image post, Facebook, an Instagram image carousel and an Instagram video carousel —
each probed with `ffprobe` to confirm what Telegram will actually receive, and each asserted to come
back as the *kind* it should be and in the right number of files. It exits non-zero on the first
failure. Run it before every commit, together with `python -m py_compile bot.py`.

The carousel is the slow one: 130 thumbnail requests for 10 slides, ~17 s (§4.10).

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

`TRANSPORT_RETRY_PAUSE` is deliberately not in this table: it is not a timeout but the gap between
the two attempts a transport failure buys, and it multiplies `SOCKET_TIMEOUT` rather than competing
with it. §5.3 — read it before changing either number.

### 4.6 The failure path is the one that bites

In production, an upload timed out **and the apology timed out too**, escaped `_deliver`, and the
group received nothing at all — no video and no "no pude bajar ese link". A network bad enough to
fail a 1.2 MB upload is frequently bad enough to fail a text reply, so `_apologise()` is a separate,
protected attempt with its own timeouts that logs and never re-raises. **It is the last line of
defence and it is the one place in this file where swallowing an exception is correct.**

**One failing link does not cost the others in the same message, and this was measured rather than
assumed.** The delivery loop at the end of `_handle_links()` is a plain `for url in supported:` with
no `try` of its own, so the isolation has to come from `_deliver()` — and it does: its entire body
sits inside one `try`, and the `except Exception` logs, records the failure in the ledger under its
own class, and apologises. Nothing can escape it, because both calls it makes on the way out are
themselves non-raising by contract — `record_rejection()` swallows and logs, and so does
`_apologise()`. Driven with three supported links whose middle download raises: the other two
delivered, the failing one produced exactly one ledger record and exactly one apology, and the loop
did not stop. **Nothing was added for this** — the exposure the `for` loop looks like it has is
already closed one level down, and a second `try` around the loop would only add a place for a
failure to get quieter.

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
so **on Instagram** a genuinely broken extraction never reaches the fallback.

**That last clause is Instagram-only, and it used to be written without the qualifier.** Measured
2026-08-09 on `youtube.com/watch?v=AAAAAAAAAAA`: YouTube reports an unavailable video through the
*same* no-formats mechanism the flag suppresses — `raise_no_formats(reason, expected=True)` in the
extractor — so the probe returns instead of raising, with **0 formats and 38 thumbnails**, and
`is_image_post()` says yes to a failed extraction. Its answer is therefore *provisional* off
Instagram, and the fallback used to finish the discrimination one step later: those 38 thumbnails
are synthesised from a URL template (`i.ytimg.com/vi/<id>/…`, each carrying a `preference` key an
Instagram thumbnail does not have) and every one of them 404s, so no image came down and the
fallback declined.

**Provisional was not enough. On 2026-08-09 a YouTube short came back as its poster frame.**
YouTube was challenging the owner's address — `Sign in to confirm you're not a bot` — and the
extractor reports *that* through the same `raise_no_formats(reason, expected=True)` arm, so the
probe returned 0 formats and 38 thumbnails exactly like a dead video. One difference is fatal:
**the video exists, so its thumbnails are real.** One came down, the group got a still frame of the
video it had asked for, nobody learned that YouTube was blocked, and the ledger recorded a
success-shaped nothing. Neither guard could fire: there genuinely were no formats, and there
genuinely was an image.

**The fix is the site, asked before anything else runs: Instagram is the only site whose image
posts this bot has ever delivered.** `has_image_posts()` reads the pasted URL's host against
`IMAGE_POST_HOSTS`; anywhere else `_image_fallback` returns `None` on its first line and
`download_into` re-raises what the extractor said. That kills the class instead of the symptom — a
YouTube or Facebook link that fails to extract is treated as a failed video, whatever the reason,
today's or next year's — and it is free: **a failing YouTube link no longer pays for the probe or
for the ~38 thumbnail fetches** that earlier revisions of this section recorded as an accepted cost.

**Be careful with the reason, because the obvious phrasing is false.** "YouTube and Facebook cannot
have image posts" is true of YouTube and **not** of Facebook, whose extractor accepts `photo.php`
and `/posts/` URLs. The narrower true statement is the one this rests on: the image path is
measured on Instagram and nowhere else, and on Facebook a wrong guess is the defect itself —
`Cannot parse data` fires under mere throttling (§5.2), so a Facebook fallback would answer a
perfectly good video with its poster frame. A Facebook photo post therefore gets the apology, and
whether it ever reached the fallback before this guard is **unmeasured in both directions**: none
has ever been in `SELF_CHECK_URLS` or in the ledger. If a friend reports one, find a live public
photo post, see what the probe returns, and only then add the host — with its own
`SELF_CHECK_URLS` entry, because a second site on the image path needs the standing proof
Instagram has.

The **host** is the signal, not yt-dlp's `extractor` key. It is known *before* the probe, while
`extractor` exists only after an extraction that succeeded; and it is the same question
`is_supported()` already asks about the same string, so `_host_is_one_of()` answers both and the
two cannot drift apart. They can disagree — a `facebook.com/share/v/` link redirects internally,
and the self-check's own share link comes back as `1547227326881971.mp4` — but both readings say
"not Instagram", and every way this signal can be wrong ends in the apology rather than in a still
frame.

"Thumbnails that yield no image" is still the sound signal **inside** Instagram, and it is not
redundant with the host guard: the host guard covers the sites that have no image posts, this one
covers the site that does — an Instagram post whose signed thumbnail URLs all fail. It is also the
guard whose only cover the host guard took away, since the dead YouTube link no longer reaches it;
mutation testing caught that, and the check now drives it on Instagram. Every up-front signal
measured on top of it — the `preference` key, the placeholder title `youtube video #<id>`, the
thumbnail count — is a property of today's yt-dlp or today's YouTube, and keying the *working*
image path on any of them would risk turning a real image post into an apology.

**Carousels of images are albums; anything with a video slide in it is still refused.** See §4.10.

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

#### Two instances, measured 2026-08-09 — and why the hand-over needs a flag

The first run of the real thing, one host taking the baton from another on the real token:

| time | what happened |
|---|---|
| 18:23:54 | host B starts, taking the baton |
| 18:23:55 | A logs `another instance has taken the poll` |
| 18:23:59 | B logs the same line |
| 18:25:04 | B stops: *"El bot ahora lo tiene otra persona. Podés cerrar esta ventana."* |
| 18:25:09 | A stops, with that same sentence |
| after | `getUpdates` answers 200: **nobody is polling.** The group has no bot, and both people were told the other one had it |

Everything in that run behaved as designed except the conclusion: one warning per episode, silence
through the retries, no tracebacks in either process, and each side stopping only after its own
grace had passed — 65 s for B and 74 s for A, counted from its own first conflict. `CONFLICT_GRACE`
is a floor, not a deadline: python-telegram-bot's backoff decides when the next conflict arrives to
be judged, and that adds up to 30 s on top.

**Telegram provides no asymmetry, so it has to come from outside.** `getUpdates` does not designate
a winner: it terminates whichever long poll is outstanding when a new one arrives, so both instances
observe the same sustained conflict. Same code, same inputs, same conclusion — with a symmetric rule
mutual give-up is the *only* possible outcome, and it is the worst one available.

The one place in the product where somebody states an intent is the launcher's *"¿Se lo saco y lo
prendo yo? [s/n]"*. An `s` now runs `bot.py --take-over`; every other path runs `bot.py` exactly as
before. `conflict_action` reads that intent and the two sides diverge:

| the same conflict, lasting… | told to take over | not told |
|---|---|---|
| its first moment | *"Se lo estoy sacando a quien lo tenía prendido…"* | *"Otra persona prendió el bot…"* |
| `CONFLICT_GRACE`, 60 s | keeps polling | stops, and says so |
| `CONFLICT_STANDOFF`, 180 s | says once that the other side is not yielding, and keeps polling | — |

Three rules make that safe, and the self-check drives all three:

- **The role is fixed when the episode starts**, never re-read on later conflicts. The intent expires
  (`TAKE_OVER_WINDOW`, 120 s from startup, twice the incumbent's grace), and an expiry landing in the
  middle of a conflict would otherwise flip a standing-ground instance back onto the give-up path —
  both sides quitting again, two minutes later.
- **The intent does expire**, so the friend who took the bot at 18:00 is an ordinary incumbent by
  18:30 and yields to the next person like anybody else. Without an expiry only the first hand-over
  of the day would work.
- **The taking-over side never stops.** No path may end with nobody polling, and that is the only
  rule that guarantees it. Two people who both answer `s` therefore end up with two instances
  knocking each other off — a bot that works erratically, not a group without one — and both windows
  say so in Spanish and ask that one of them close. Nothing here can arbitrate: different laptops,
  no shared state, and Telegram refuses (§6).

Not verified without a second live instance: that python-telegram-bot delivers the `Conflict` to the
registered error handler at all. The path is asserted from `conflict_action` up to `on_error`, and
the library's own code says it does (`Application.run_polling` passes an `error_callback` that feeds
`process_error`), but nobody has watched it happen. **And the fixed hand-over has not been run
end to end either**: the check drives both roles over a simulated timeline, which is the whole of a
pure function and none of a second laptop.

### 4.10 Instagram carousels

A carousel whose slides are **all images** comes back as one Telegram album. Anything with a video
slide in it does not. Measured 2026-08-09 on `instagram.com/p/DbcsX-BlkZX/`:

| | |
|---|---|
| the info dict | `_type: playlist`, no top-level formats, **no top-level thumbnails**, `entries: 10` |
| each entry | `formats: 0`, `thumbnails: 13` — every slide is an image post, reachable like §4.8 |
| slide thumbnails | `id` and `url` only. **No width, no height** — same as §4.8, so the same rule: fetch all, biggest file wins |
| downloaded | 10 slides × 13 thumbnails = 130 files, 6.3 MB, 17.4 s |
| delivered | the 10 winners, 97 KB – 266 KB each, **1,756,160 B total** |

Four things here are not derivable from the code:

- **The bot's own probe used to see one slide, not ten.** `_ydl_options` sets `playlist_items: "1"`,
  which is right for the video path and wrong for inspecting a carousel: with the cap the probe
  returns `entries: 1`, without it `entries: 10`, and `playlist_count: 10` either way. Detection
  written against a raw `yt-dlp --dump-single-json` would be reading a dict the bot never sees.
  `_carousel_options` drops the cap; `noplaylist: True` stays, because Instagram returns the
  carousel as a playlist regardless.
- **`sendMediaGroup` takes 2–10 items** — "must include 2-10 items", quoted from the Bot API docs on
  2026-08-09, and matching `telegram.constants.MediaGroupLimit`, which is where the code reads it
  from. **python-telegram-bot does not enforce either bound**: `send_media_group` only mentions them
  in its docstring, so an 11-item call fails at Telegram's server, not locally. A longer carousel
  therefore sends the first 10 **after** a Spanish line saying how many the post really had. Said
  before the album, not after, so the warning cannot be the message that got lost.
- **`InputMediaPhoto(Path(...))` silently uploads nothing.** Its `__init__` calls
  `parse_file_input(..., local_mode=True)` unconditionally — "we don't have access to the actual
  setting" — so a `Path` or a `str` becomes the literal string `file:///...`, and the request layer
  only rewrites an `InputMedia` whose `.media` is an `InputFile`. The wire value would be
  `media: "file:///Users/..."` with zero attached files, which only a local Bot API server accepts.
  Passing an **open file object** is what produces an `InputFile` and an `attach://` URI. Verified by
  building the request parameters for all three forms (2026-08-09, PTB 22.8); the self-check asserts
  the type, because nothing short of the real group would show this.
- **A video slide anywhere means refuse.** `instagram.com/p/BQ0eAlwhDrw/` is a live all-video
  carousel — 3 entries, 4 formats and 12 thumbnails each — and it still arrives as a single video
  through the ordinary path, because the image fallback is only reached once the video download has
  failed. **Mixed photo/video carousels remain unmeasured**: the one public example named in yt-dlp
  #7569, `instagram.com/p/CtXtwOop1W5/`, answers "Instagram sent an empty media response" without
  cookies as of 2026-08-09. They get the apology. Telegram is not the obstacle — its docs restrict
  only documents and audio to same-type albums — yt-dlp's handling is (#7569, #11792).

**`img_index` in a pasted URL is ignored on purpose.** Instagram writes it from whichever slide the
sharer was looking at, so reading it as "send this one" is a guess about intent. The album is the
safe superset. The self-check's carousel URL still carries `img_index=9`, so this stays proven.

### 4.11 Message entities — the links only Telegram sees

`URL_PATTERN` requires `http(s)://`, so a bare `youtu.be/xyz` — which the Telegram client renders as
a tappable link — used to be invisible to the bot. `entity_urls()` reads the entities Telegram
attaches to the message and unions them with the regex's result, regex first, de-duplicated. Adding
a source can only ever add links; anything that worked before produces the same list in the same
order.

**Only `url` entities are taken.** Telegram has two types that carry a link:

| Type | What it is | Taken |
|---|---|---|
| `url` | text Telegram itself recognised as a link — what the entity says is what the group sees | **yes** |
| `text_link` | a URL hidden behind display text, from Telegram's "create link" formatting | **no** |

`text_link` is refused because acting on it means downloading something nobody in the chat can
read, and because no case has needed it. Refusing is the self-correcting mistake — nothing happens
and the friend pastes the plain link. Asking for one type also means a new entity type in a future
Bot API cannot quietly start feeding yt-dlp, which listing what to skip would not give you.

**The offsets are in UTF-16 code units, not Python characters, and this is the trap.** An emoji
outside the BMP is two code units and one Python character, so `text[offset:offset + length]` on
`😂 youtu.be/abc` returns `outu.be/abc` — a string `is_supported()` rejects without a word. The bot
calls python-telegram-bot's `parse_entity()`, which does the UTF-16 round trip, rather than slicing
itself. A schemeless entity gets `https://` prepended, because `urlparse("youtu.be/abc")` has no
hostname at all and the link would be dropped one line later.

**None of this has been confirmed end to end.** Every entity the self-check drives is one the check
constructed; nobody has posted a schemeless link into the real group and watched what Telegram
sends. The offsets are built the way the Bot API documents them, and that is an assumption until a
real paste confirms it.

**A URL no parser can read is answered "no", and it used to cost the whole message.** `urlparse`
does not hand back an empty hostname for these — it raises `ValueError` — and the raise escaped
`_handle_links`, so one malformed string meant the group got nothing at all for every good link in
that same message. Two shapes were measured, both `ValueError`:

| String | What `urlparse` says | Which source can produce it |
|---|---|---|
| `https://[::1/x` | `Invalid IPv6 URL` | entities only — `URL_PATTERN` excludes brackets |
| a host with a **fullwidth** `#`, `/`, `@` or `:` | `netloc contains invalid characters under NFKC normalization` | **the regex**, from ordinary message text |

The second row is why this guard does not depend on the open question above: `URL_PATTERN` accepts
any non-space character, so plain text with no entity anywhere reaches the parser and raises. That
half is confirmed.

**The guard lives in `_host_is_one_of`, not in `is_supported`,** and the difference is not cosmetic.
`is_supported` is not the only caller — `_handle_links` asks the same function about
`MEDIA_PLATFORM_HOSTS` directly, on the raw URL list, *after* `is_supported` has filtered. Guarding
one level up was measured and it **moves** the raise instead of removing it: a message whose only
URL is unparseable still takes the handler down. One host rule, one guard, every caller inherits it.
Answering is all it does; it validates nothing, and a URL that parses but is nonsense reaches yt-dlp
exactly as before. The string is still recorded in the ledger by the unsupported path like any other
bounced link, and `rejection_host()` already groups it under `?`.

### 4.12 Being called stupid — the only thing answered that is not a link

The owner asked for it in these words: *"cada vez que lea 'bot estupido' en cualquier mensaje (o
cualquier variante como 'estupido bot' o 'vot estupido' o letras faltantes) debe registrarlo y
responder 'Lo lamento, hago lo que puedo'"*. So `insult_words()` reads every message, and a hit
gets that sentence verbatim plus one line in `insults.jsonl`.

**The false-positive side is the whole design.** This runs on every message a group of friends
sends all day; a bot that apologises when nobody insulted it is worse than one that misses a typo,
and it is also the failure nobody can debug from the chat. Three rules do the work, each earned by
a phrase that broke a simpler one:

| Rule | What it stops | Measured |
|---|---|---|
| **Both words, and near each other** — within `INSULT_MAX_GAP` = 1 token | *"sos un estupido"* (a person), *"gracias bot"*, and *"el bot funciona, no seas estupido vos"* — both words, four apart | the pair is the signal; either word alone is ordinary chat |
| **A token may not be longer than the word it matches** | *"esa bota estupida"*, *"el boton estupido"* | difflib scores `bota`→`bot` at **0.857** and `boton`→`bot` at **0.750**; a typo drops or changes a letter, a longer word is a different word |
| **One threshold per word**, not one for both | *"abri el bot, estudio despues"* | `estudio`→`estupido` is **0.800**, so the threshold is 0.85; `vot`→`bot` is **0.667**, so that one is 0.66 |
| **A list of words that are not typos** (`NOT_THE_BOT`) | *"bro que estupido"*, *"esa bio estupida"* | `bro`→`bot` is **0.667** — *exactly* what `vot` scores, because both share two letters. No threshold can accept the typo the owner named and refuse the loanword |

The thresholds are `0.66` for `bot` and `0.85` for `estupido`, and each sits in a gap that was
measured rather than guessed:

| | lowest thing that must fire | highest thing that must not | threshold |
|---|---|---|---|
| `bot` | `vot` / `bo` / `ot` — **0.667**, one letter of three | `no` — 0.400, `o` — 0.500 | **0.66** |
| `estupido` | `estupida` — **0.875**, `estupdo` — 0.933 | `estudio` — **0.800** | **0.85** |

0.667 on a three-letter word is exactly "one of the three letters is wrong", which is where a typo
stops being a typo — the reason a single threshold cannot serve both words. Before either number
is moved, the phrase that motivates it has to be one the group actually sent; the two corpora in
the self-check (25 that must fire, 41 that must not) are where it goes.

**`NOT_THE_BOT` is the admission that the thresholds are not enough.** difflib scores by longest
common subsequence, so `bro` and `vot` are indistinguishable to it — two shared letters each, 0.667
each — and *"bro que estupido"* is an ordinary sentence that fired. The list is the 3-letter words
a Spanish chat plausibly types that clear 0.66: it was enumerated (all 226 tokens of ≤3 letters
that pass, filtered to real words), not imagined, and each entry costs nothing because nobody types
`boa` meaning the bot. The self-check refuses a **dead** entry — one that would not have matched
anyway — the same way it refuses an unreachable `FAILURE_SIGNATURES` row.

That one was found by a review pass *after* both corpora were written and green, which is the
honest measure of how far a corpus written by imagination gets you.

Normalisation is `unicodedata` and `re`, no dependency: NFD minus the combining marks makes
*estúpido* and *estupido* one word, casefold makes shouting match, splitting on anything outside
`a-z` turns punctuation and emoji into separators, and a run of one letter collapses so
*estupidoooo* and *bott* are the words they are meant to be — neither target word has a doubled
letter, so nothing is lost.

**Known misses, all deliberate.** `botestupido` written without the space (nothing joins tokens,
and the owner did not ask for it); `robot estupido`, because difflib scores `robot`→`bot` and
`boton`→`bot` identically at 0.750 and refusing the button is worth losing the robot; a real typo
that lands on a `NOT_THE_BOT` word; and anything further apart than one word, like *"el bot es un
estupido"*. A missed joke costs nothing.

**The record is its own file, not the ledger** (§5.1 is about links, and its report opens with "N
links bounced"), and it carries `when`, `chat_id`, `message_id` and **the two matched words**. Two
words is a deliberate exception to "no message bodies", argued rather than assumed: by
construction they are near-copies of `bot` and `estupido` — that is the only way they matched — so
they cannot carry anything private, and they are the only thing that can tell the owner a hit was
*wrong*. Dates alone cannot be audited: a false positive and a real insult look identical, and no
threshold could ever be moved on evidence. Their similarity scores are not stored because they are
recomputable from the words.

**Nobody has been insulted by a real person yet.** Every phrase in both corpora was written by an
agent imagining how this group types. That is the honest status of the numbers above: they
separate two invented lists cleanly, and the first real false positive is worth more than all of
them.

### 4.13 A live stream has no end, and it used to eat the bot

A link to a stream that is happening **now** is the only input that does not fail and does not
succeed — it downloads forever. Measured 2026-08-10 by driving the then-unguarded `download_into`
against `youtube.com/watch?v=X4VbdwhkE10` (a permanent lofi radio stream) in a child process with a
hard 20 s `SIGKILL`:

| | |
|---|---|
| exited on its own | **no** — killed at 20 s |
| bytes on disk | 2,097,152 in `X4VbdwhkE10.mp4.part` |
| growth | monotonic, ~104 kB/s = **~6.3 MB/min = ~375 MB/h** |

That is an audio-dominant stream, so it is the *cheap* case. Mechanism: `downloader/hls.py`'s
`can_download` yields `not is_live`, so the native downloader refuses and `downloader/__init__.py`
routes a live stream to `FFmpegFD`, which writes until something stops it.

**The unbounded file is not the worst half.** `temp_workspace`'s cleanup is a `finally` that only
runs when `download_into` **returns**, and on a live link it never returns — so the growing `.part`
is never cleaned while the process lives. And because PTB processes updates sequentially, that one
message **is the entire bot** until somebody kills it. One pasted link, on a friend's laptop, with
no way for them to know why.

**The three rows that define the guard**, measured with `--simulate` on 2026-08-10, yt-dlp 2026.7.4:

| URL | `is_live` | `live_status` | `was_live` | `duration` | verdict |
|---|---|---|---|---|---|
| `watch?v=X4VbdwhkE10` (live now) | `True` | `is_live` | `False` | `None` | **refused** |
| `watch?v=jNQXAC9IVRw` (ordinary) | `False` | `not_live` | `False` | 19 | accepted |
| `watch?v=zo5oewEQbsE` (**finished** stream) | `False` | `was_live` | **`True`** | 1146 | accepted |

**`was_live` is `True` for exactly the case that must NOT be refused**, which is the one way this
feature silently breaks. A finished stream is an ordinary bounded video — that one selects `136+140`
at 178 MB under `MEDIA_FORMAT`, so it leaves as the oversize link reply (§4.4). A guard keyed on
`was_live` rejects every stream replay the group pastes, and no check that only tries a live URL
would ever notice. `is_live_stream()` reads `is_live` and `live_status` and never `was_live`.

**A size or duration ceiling is not an alternative.** On the live row `duration`, `filesize` and
`filesize_approx` are **all `None`**, on the info dict *and* on the selected format — so a ceiling
cannot fire on a live stream at all, and it would false-positive on long ordinary videos.

**Nothing in format selection would have stopped this.** `MEDIA_FORMAT` does select a live format
(`95`, `m3u8_native`, `avc1.4D401F` + `mp4a.40.2`, height 720, format-level `is_live=True`, no
filesize), so the guard cannot live there — it has to answer before selection runs at all.

**Where the refusal happens.** `_refuse_live_stream` is wired as yt-dlp's `match_filter`, which
`_match_entry` consults from `process_video_result` **after `formats` is populated and before format
selection and `process_info`** (yt-dlp 2026.7.4, line 3042); a non-`None` answer makes it
`return info_dict` on the next line, so there is no format selection, no download and no file. The
refusal costs the metadata request that had already happened and nothing more. There is no earlier
hook that has seen the info dict.

`download_into` then asks `is_live_stream(info)` again and raises `LiveStreamError` — **before** its
`path is None` check, which is load-bearing: a refused stream is a successful extraction that
deliberately left no file, so the generic *"yt-dlp reported success but left no file"* would fire
first and misreport the one refusal that has its own name, its own ledger class and its own sentence.
It is asked again rather than carried out of the filter because the filter answers inside yt-dlp and
its reason never reaches the caller.

`LiveStreamError` subclasses `ExtractionError` so nothing on the delivery path needs to learn about
it: `_deliver`'s `except Exception` already catches it and `record_rejection` already writes
`type(exc).__name__`, so the ledger gets the new class for free. Only the sentence is new (§5.2).

Two traps in that wiring, both read off `_match_entry`:

- It calls `match_filter(info_dict, incomplete=...)` inside a `try` that catches `TypeError` and
  **retries positionally, returning `None` — download it — whenever `incomplete` is truthy**, and
  `process_video_result` passes `self._format_fields`, a non-empty set. A callable that does not
  accept `incomplete` as a keyword, or that raises `TypeError` internally, therefore fails **open**
  and in silence. That is why the check calls it both ways round explicitly.
- Returning yt-dlp's `NO_DEFAULT` sentinel makes it prompt on `input()` (line 1636). There is nobody
  at the terminal on a friend's laptop, so the bot would hang forever on the read — the same
  unbounded failure in a new costume. The filter returns a reason string or `None`, never that
  sentinel, and the check asserts it for four different info dicts.

**The check is driven by a fake yt-dlp that honours `match_filter` the way the real one does**, and it
asserts the *call* — `filtered(...)` versus `downloaded` — not only the outcome, because an
outcome-only assert passes on a bot that downloads the whole stream and then raises. The live case
and the finished case are both driven; the finished one is the assertion a guard keyed on `was_live`
dies on, and the only one that could ever catch it. **No seventh real download was added** — a live
stream cannot be a self-check entry, since the thing being asserted is that nothing is downloaded.

---

## 5. Operations — what actually breaks

**The application logic will not rot. The extractors will.** YouTube, Instagram and Facebook change
their pages without warning, and that is this project's real failure mode.

**"¿Te puedo mandar la ventana?" — yes, since 2026-08-10.** Before that date the answer was no, and
nobody had noticed: python-telegram-bot's HTTP client (`httpx`) logs every request at INFO with the
full URL, and every Telegram API URL carries the token in its path
(`api.telegram.org/bot<TOKEN>/getUpdates`). Measured on the live process: **374 of the 379 lines it
logged in 64 minutes were that line** — about six a minute, and the launcher does not redirect
stdout, so they go to the Terminal window a friend is looking at. A screenshot of it was full
control of the bot. `configure_logging()` now runs that one logger above INFO. Nothing else changed:
the deliveries, the bounced links with their URLs, the conflict lines (item 6) and the retry line
all still print, which is the point — that window is the only diagnostic anybody has here. The
self-check asserts **both** halves, no request URL *and* the bot's own INFO lines still arriving,
because a fix that had silenced the whole process would pass the first one alone. A redaction filter
over the log was considered and refused: it would have to be right about every URL shape httpx will
ever log, and it is silent when it is wrong.

1. **Videos stop downloading → update yt-dlp first.** `.venv/bin/pip install -U yt-dlp`. This fixes
   the large majority of breakages and nothing else is worth trying before it.
2. **The bot ignores a link → read the log.** `on_message` says which of the two happened:
   - `message N: no URL recognised, nothing to do` — neither the regex nor Telegram's own entities
     found a link (§4.11). A bare `youtu.be/xyz` is **not** this case any more; if it lands here,
     either Telegram sent no `url` entity or the link was a formatted `text_link`, which is refused
     on purpose.
   - `message N: 2 URL(s) found, none on a supported host -- rejected: …` — the URLs are printed
     because they are the entire diagnosis. Add the host to `SUPPORTED_HOSTS` if it belongs there.
     These also go in the ledger under `UnsupportedHost` (§5.1), so the question survives the
     window being closed: `bot.py --rejected` answers "what has this group been pasting".
     **The group may also have been told, once, for that message** — see §5.4.
3. **The bot stops seeing anything in a group** → it was probably demoted from administrator. See §3.
4. **YouTube fails with missing formats** → the first suspect is the warning yt-dlp prints on every
   extraction: *"No supported JavaScript runtime could be found… extraction without a JS runtime has
   been deprecated."* It is harmless today (2160p and merged 720p both resolve without one) and no
   runtime is installed on purpose — this project is meant to be copied onto an old Linux box. The
   escape hatch is `--js-runtimes node` or installing `deno`.

   **That suspect was wrong once already, on the failure that looks most like it.** When YouTube
   answers `Sign in to confirm you're not a bot` the JS runtime is not involved: forcing
   `--js-runtimes node` produced the identical error (owner, 2026-08-09). That failure is YouTube
   challenging *this address* — usually after a burst of extractions — every YouTube link in the
   group fails while it lasts, and it clears on its own. The bot now says so in Spanish (§5.2)
   instead of answering with a poster frame (§4.8). The operator action, if it does not clear: put a
   cookies file somewhere and point `YTDLP_COOKIES` at it (§4.7). Waiting is usually cheaper.
5. **A video arrives as a grey file row instead of playing** → the format string picked AV1 or a
   webm. §4.1 and §4.2. The self-check catches this; run it.
6. **The log says `another instance has taken the poll`** → somebody else opened a launcher. One
   line means somebody merely *asked* whether the bot was running and this instance recovered;
   `the conflict lasted 60 s` means the handover was real and this instance stopped on purpose.
   `taking the poll over as instructed` is the other side of the same event, on the instance that
   was told to take the bot. `still conflicting after 180 s` means two people both answered yes:
   nothing will resolve it by itself, and one of the two windows has to be closed. §2.1 and §4.9.

### 5.1 The rejected-links ledger

Every link that does **not** end in delivered media appends one JSON line to `rejected.jsonl`, next
to `bot.py`. Gitignored — it is the group's content. **Links only**: an insult is not a bounce and
has its own file (§4.12), because this report opens with "N links bounced" and groups by host, and
it is the one signal that decides which site to support next.

```sh
.venv/bin/python bot.py --rejected
```

**Two piles, and the error class is what keeps them apart.** A *bounce* is a supported link the bot
tried and failed on — something rotted, and the fix is usually a yt-dlp bump. An *unattempted* link
is a URL on a host that is not in `SUPPORTED_HOSTS`: nothing failed, the bot simply does not know
that site, and the fix is deciding whether to support it. They read as different groups in the
report because they lead to different actions, and the second pile is the one that drives the
roadmap — "the group pasted TikTok eight times this week" is the evidence that decides what to
build next, and it used to evaporate with the terminal window.

Unattempted records carry `error: "UnsupportedHost"` and an empty `detail`: nothing was attempted,
so there is no error text, and the class plus the URL are the whole record. **Every** URL of such a
message is recorded, not just the first — which site recurs is the entire point. Two things are
deliberately *not* recorded: a message with no URL in it (ordinary chat, and it would bury the
signal), and the skipped links of a message that also carried a supported one (something *was*
attempted there, so calling it unattempted would be false).

It groups by error class first, then by host, and lists every URL underneath. That order is the
diagnosis: the class says what *kind* of thing is going wrong — one rotted extractor looks nothing
like a run of files over the ceiling — and the host says where, which is usually the fix.

Three things about the records:

- **The message body is never written**, only the URL. Same rule as the ignore-logging in item 2
  above — this is a private group and the URL is the whole diagnosis.
- **`error` is the exception class name**, except for the two paths where nothing raised. A file too
  big to upload carries `OversizeForTelegram` and the byte count — deliberately in the ledger
  because a link reply is not the media, and because that path has still never run against Telegram
  (§6). A link on an unknown host carries `UnsupportedHost`.
- **`detail` is the raw failure text with terminal colour codes stripped**, and it stays raw
  whatever the group was told — §5.2 changes the chat message, never the record. The strip is there
  because one live record from 2026-08-09 carries yt-dlp's red `ERROR:` as the literal bytes
  `\x1b[0;31mERROR:\x1b[0m` while a second record of the *same* failure nine minutes later carries
  none. **What makes yt-dlp colour one run and not the next is not known.** The obvious theory — it
  colours a terminal and not a pipe — was tested both ways and produced no escapes either time, so
  it is refuted, not confirmed. `strip_ansi` therefore runs unconditionally: a fix behind a TTY
  check would be a fix behind the wrong condition.

**The ledger fragments across hosts, and that is accepted, not overlooked.** Each friend's machine
records only what it saw and nothing merges them. At ~20 links a week the owner reading his
own file, and asking a friend to send theirs when a week is missing, costs less than any sync would.
The format is append-only lines, so `cat` is the merge. Do not build syncing for this (§6).

A failure to write the ledger is swallowed and logged: it is diagnostics bolted onto the failure
path, and it may never cost the group its apology.

### 5.2 What the group is told when a link bounces

`FAILURE_SIGNATURES` maps a failure the bot can recognise to one Spanish line; `failure_reply()`
matches, and anything it does not recognise is still `no pude bajar ese link`, exactly as before.
The table is data and the matcher is logic — adding a failure is adding a row.

Every row below is a signature measured by running `download_into` against the live site — **not**
by reading `yt-dlp --simulate`, because what `download_into` produces is what the bot classifies,
and only running it proves the two are the same string. Five were measured on 2026-08-09; the sixth
is marked and explained under the table, and the seventh arrived by itself on 2026-08-10 — the
ledger recorded it in production, which is the cheapest measurement there is.

| What happened | Matched on | The group hears |
|---|---|---|
| Instagram post restricted by audience | `this content isn't available to everyone` | *ese post de instagram no es público, no me deja verlo* |
| Instagram post gone, private or auth-walled | `instagram sent an empty media response` | *…puede que sea privado o que ya no exista* |
| An Instagram **profile** URL, not a post | `[instagram:user]` + `unable to extract data` | *ese link es de un perfil…, pasame el del reel o la foto* |
| Facebook post dead **or** Facebook throttling | `[facebook]` + `cannot parse data` | *…puede que ya no exista o que facebook me esté frenando. probá de nuevo en un rato* |
| A YouTube video that is deleted **or** private | `[youtube]` + `video unavailable` | *…puede que lo hayan borrado o que sea privado* |
| YouTube challenging this address † | `[youtube]` + `sign in to confirm` + `not a bot` | *youtube me está bloqueando…: no es culpa del link, probá de nuevo más tarde* |
| The name would not resolve — twice (§5.3) | `resolving timed out` | *no pude conectarme…: puede ser mi conexión, la tuya o la del sitio. mandámelo de nuevo* |

**Three of those seven hedge on the cause, and that is the rule, not a hesitation.** Facebook's
`Cannot parse data` fires on a perfectly good URL under rate limiting — an earlier agent hit exactly
that after five self-checks in 25 minutes — so "ese post no existe" would be a confident lie about
half the time. YouTube cannot distinguish deleted from private: `watch?v=AAAAAAAAAAA` and
`?v=ZZZZZZZZZZZ` produce byte-identical text. A reply may never claim more than its signature
carries.

**The last row hedges on something else — whose network it was** — and it is the only row in the
table whose advice the friend can act on in the next ten seconds. The host's wifi, the friend's,
and the site being unreachable produce the identical string, so it offers all three and blames
none; and because that failure is the only one measured clearing on its own, it is the only one
that asks for the link again. It is also the only row the group reaches **after** the bot has
already tried twice (§5.3), which is why the ledger detail it must keep matching carries
`(retried once)` in front of the extractor's text.

Its marker is one word narrower than it could be, on purpose. `failed to perform, curl:` and
`(caused by transporterror` are both in the same record and both would catch a reset connection or
a refused socket as well — neither is used, because what was measured clearing by itself is *this*
failure. Those other transport failures still get their second attempt (the retry reads the
exception, not this string); they just fall through to the generic apology instead of being told
something nobody has verified.

**The bot-check row (†) is the mirror of that rule and the one exception to the measurement
rule.** It does
*not* hedge, because the signature does not: YouTube says plainly that it wants a login, so the
group is told the one thing it can act on — the link is fine, the bot is blocked. The **operator**
action lives in §5 item 4 and in `YTDLP_COOKIES`, not in a sentence written for a friend.

† **This is the only row the branch that added it could not re-measure.** The owner hit it on
`youtube.com/shorts/5kC43KL_mBE` on 2026-08-09 and pasted the error; by the time the fix was
written the challenge on that address had lifted (the same short extracts 28 formats), so the
string in the self-check is his paste plus the tail yt-dlp 2026.7.4 appends to it —
`_login_hint("cookies")` in `extractor/common.py`, wrapped by `_youtube_login_hint` in
`extractor/youtube/_base.py`. The markers deliberately step **around** the apostrophe in `you're`:
that character arrives from YouTube's own JSON, and a typographic one would kill the row silently,
the same way a capital would. Both spellings are asserted.

**The deleted-or-private row used to be keyed on the bot's own sentence, and that was a defect, not
a design.** Until 2026-08-09 the image fallback answered a dead YouTube link with `has no video and
no downloadable image either` — its own words, byte-identical for every failing YouTube link — and
yt-dlp's `Video unavailable` was thrown away before anything downstream saw it, ledger included.
The fallback now declines instead of raising when its thumbnails yield no image (§4.8), so the
extractor's diagnosis survives, and the row is keyed on it: `[youtube]` pins it to the host and
`video unavailable` is the cause. Re-measured against the live site after the fix.

The cost that came with it is gone too, and later than the row: a dead YouTube link used to burn
~38 thumbnail requests before the fallback could tell they were worthless, and since the fallback
declines on the host it now makes no request at all (§4.8).

**One reply is not in that table at all, and must never be: the live stream.** `failure_reply()` asks
the exception **class** before it looks at any text, and `LiveStreamError` returns
*ese link es una transmisión en vivo, no puedo bajarla hasta que termine* (§4.13). Every row in the
table is keyed on a string **that came from the site**; this refusal never asks the site anything —
the bot decides it off the info dict, before a byte is written — so the only string available to key
a row on would be **the bot's own sentence**, which is exactly the defect the deleted-or-private row
was fixed of below. The bot's words are byte-identical for every link that reaches them, so such a
row would tell the ledger and the group nothing the class had not already said. The discrimination is
therefore the exception, the same one `is_transport_failure` makes: **the class, never the message.**

It does **not** hedge, for the same reason the bot-check row does not — `is_live` is the site stating
a fact about its own post. Its actionable half, *hasta que termine*, is true rather than kind: once
the stream ends the same link becomes an ordinary bounded video and downloads normally. The reply is
picked before the single protected send, so this adds **no** fifth swallow site; the ledger records it
as its own error class, `LiveStreamError`, with the raw detail.

**These strings are upstream prose and they will drift.** The design makes drift fail safe: a
reworded message matches no row and the group gets the generic apology, which is where it started.
A row that stops firing shows up in the ledger, which keeps the raw text — copy the new sentence,
add a row. Do not loosen a marker to "catch more": a loose marker firing on the wrong failure is
the only outcome worse than the generic apology.

### 5.3 A blip is not a bounce — the one retry

Two good links bounced three minutes apart on 2026-08-10, and the ledger named the cause without
anybody guessing:

```
ERROR: [Instagram] …: Unable to download webpage: Failed to perform,
curl: (28) Resolving timed out after 20001 milliseconds
```

`curl: (28) Resolving timed out` is **DNS failing on the machine hosting the bot** — not Instagram,
not the links, which both extracted fine minutes later. On a bot that lives on whichever friend's
home wifi is up, this is the most likely recurring failure there is, and it is the one where the
right answer is *try again* rather than *explain*.

**None of yt-dlp's own retry knobs reaches it, and that was measured before anything was written.**
Method: a real Instagram extraction pointed at a local listener that accepts the connection and
closes it — a transport failure at exactly the call site a DNS timeout fails at
(`_request_webpage`, during metadata extraction), with nothing sent to Instagram at all. Every
configuration made **exactly one** transport attempt:

| yt-dlp options | Transport attempts |
|---|---|
| as shipped (`retries: 3`) | 1 |
| `+ extractor_retries: 3` (its own default anyway) | 1 |
| `+ extractor_retries: 10` | 1 |
| `+ retries: 10, fragment_retries: 10` | 1 |
| all three at 10 | 1 |

The source says why, and it is worth knowing because the names are misleading:

- **`retries`** is the **media file** downloader's loop — `downloader/http.py`, one call site. It
  covers the bytes of the video, after extraction has already succeeded.
- **`fragment_retries`** belongs to the fragment downloaders (DASH, HLS, ISM, external). Same half
  of the process, smaller unit.
- **`extractor_retries`** only exists where an extractor explicitly wraps a section in
  `RetryManager` (`extractor/common.py`). Nine extractors do — YouTube and TikTok among them —
  and **Instagram is not one of them**, so setting it changes nothing whatsoever for the site this
  group actually pastes. It is also *documented* as being for "known errors", not for transport.

So the retry is the bot's own, it lives in `download_into`, and it is **one** extra attempt after
`TRANSPORT_RETRY_PAUSE`. Not a loop, no backoff, no jitter, no library: the evidence is a blip that
cleared inside three minutes, so a second attempt is either enough or it was never a blip.

**The discrimination is the exception, not the message.** yt-dlp hangs the original failure on the
`DownloadError` it raises (`YoutubeDL.trouble` copies `sys.exc_info()` onto it), and anything below
the HTTP layer arrives there as `networking.exceptions.TransportError` — verified against
yt-dlp 2026.7.4 on 2026-08-10. `HTTPError` is a *sibling* of that class, not a subclass, so a 404,
a 403 or a rate-limit page is an **answer** and is never retried; neither is an `ExtractorError`
such as "this post is private". `is_transport_failure()` is the whole rule.

**A transport failure also skips the image fallback**, which is not a saving but the only correct
answer: that probe's job is to ask the site what kind of post this is, and the site is precisely
what could not be reached.

**What a real outage costs the group**, derived from the measured attempt count times
`SOCKET_TIMEOUT` (20 s — the production record says "20001 milliseconds", which is that constant):

| Link | Before | Now |
|---|---|---|
| Instagram | ~40 s — the failed extraction, then the image fallback's probe timing out too | ~43 s — two real attempts, plus the 3 s pause |
| YouTube, Facebook | ~20 s — one attempt; the host guard already skipped the fallback | ~43 s |

On Instagram the change is therefore almost free: it spends on a second real attempt what used to
be spent on a probe that could not have worked. Off Instagram it roughly doubles the wait for the
apology, and that is the price of the link not being lost.

**If the retry fails too, the ledger says so.** The record's `detail` opens with
`yt-dlp could not download <url> (retried once):` and the extractor's own raw text follows it
untouched, so `bot.py --rejected` tells "the network was down for two attempts" apart from a
first-time failure without any record growing a field. Whoever is watching the terminal learns
earlier — one `WARNING` line goes out between the attempts, while the link can still be saved.

### 5.4 Silence, and the one place it is the wrong answer

Every path through `on_message` says something when it cannot deliver — a named failure gets its
Spanish line, an unrecognised one gets `no pude bajar ese link`, a file over the ceiling gets the
direct link — with one exception that used to say nothing at all: **a URL on a host the bot does
not support**. It was recorded in the ledger, logged once, and never mentioned in the chat. From
inside the chat that is indistinguishable from the bot being dead.

The decision behind that silence is still right and is **not** reversed: *a bot that answers "no
pude bajar ese link" to every news article in the chat is worse than one that stays quiet.* A
Spotify link, a Google Doc, a nota in La Nación — nobody pasting one of those is waiting for a
video, and answering them all is how a useful bot becomes noise.

So the exception is narrow: **`MEDIA_PLATFORM_HOSTS`** — sites where a link is a video by
construction (TikTok, Twitch, Vimeo, Dailymotion, Streamable, Kick). One of those in a message
earns exactly one line, `ese sitio no lo manejo, no puedo bajar ese link`, and nothing else does.

Four things about it:

- **One reply per message, not per URL.** Three TikToks pasted together are one question.
- **The ledger still records every unsupported URL**, answered or not. The record is the roadmap
  input (§5.1) and the reply is courtesy to whoever is waiting; tying either to the other would
  destroy the report that decides which site gets supported next.
- **It is not a step toward supporting those sites.** Nothing is downloaded from any of them.
- **The list is judgement, not evidence, and that is its weak point.** The ledger is what should
  grow it, and on 2026-08-10 it had nothing to say: 6 records, not one of them an `UnsupportedHost`
  — this group pastes Instagram and essentially nothing else. **X/Twitter, Reddit and Pinterest are
  deliberately absent**, because a link to one of those is as often an argument or a photo thread
  as a video; they are the first candidates the day `bot.py --rejected` shows the group pasting
  them. Do not add a host because it sounds likely.

**"Bot caído" is the one cause the bot cannot report**, and nothing here pretends otherwise: a
process that is not running sends no messages. No heartbeat, no watchdog, no status ping, no second
process — the mitigation is the baton pass (§2.1), where somebody else starts hosting.

---

## 6. Deliberately not built

Each of these is a decision with a reason, not an oversight. Re-opening one needs new evidence, not
a preference.

| Not built | Why |
|---|---|
| Database, job queue, web framework, Docker | 20 links a week. Cost with no benefit. |
| `systemd` unit, `launchd` plist, any process manager | Belongs to the port onto the spare Linux machine, not here. Adding it now ties the repo to macOS. |
| Sending the launcher itself as a Telegram file attachment | A launcher on its own is inert, and a file that arrives over the network is quarantined (§2.2). The clone command is the same feature with neither problem. |
| Sending the whole repository as a zip or a `git archive` | Size was never the obstacle — it is well under Telegram's document ceiling — but with no `.git` it can never `git pull`, so it is a snapshot that rots. That is the opposite of what was asked for. |
| Deriving the clone URL from `git remote get-url origin` | A subprocess on a path anyone who can message the bot can trigger, and it would hand friends an SSH remote or nothing at all (§2.2). The staleness it would have prevented is pinned by the self-check instead. |
| Auto-start for the launcher — a LaunchAgent, a Startup shortcut | The baton pass is manual on purpose. Two friends who each installed one would quietly re-create the 409 problem every morning, with nobody at the keyboard to answer the question the launcher asks. |
| A cross-platform launcher, a Python bootstrapper, a shared config for the two scripts | Two ~100-line scripts that each read plainly in their own idiom beat one clever thing neither platform's user can debug. |
| A lock file to answer "is anybody hosting?" | It would live on the wrong machine and go stale. Telegram's own 409 is the only authority, and asking costs one conflict (§4.9). |
| A tie-break between two instances that were both told to take over | There is no channel to hold it on: different laptops, different networks, no shared state, and Telegram designates no winner (§4.9). A negotiation over the group chat would be visible to the friends and could still race. The two people can talk to each other; their bots cannot — so the standoff is announced in both windows and left to them. |
| A JS runtime (`deno`, wiring `node`) | Extraction works without one today, measured. The port target argues against a new runtime dependency added to silence a warning. |
| A local Telegram Bot API server for 2 GB uploads | Compiling tdlib for a ceiling meme-length clips rarely reach. |
| TikTok support | Not requested, and the IP was blocked when it was probed. A TikTok link now gets *told* the bot does not handle that site (§5.4); nothing is downloaded from it. |
| A heartbeat, watchdog, status ping or second process to report "the bot is down" | A process that is not running cannot send a message, so this can only be answered from somewhere else — and the somewhere else is the baton pass (§2.1), which already exists and needs no daemon. |
| An Instagram throwaway account and cookies | Unnecessary — anonymous extraction works (§4.7). |
| Syncing the rejected-links ledger between hosts | It fragments by design (§5.1). A server or a shared database for ~20 links a week is exactly the cost this repo refuses; `cat` merges the files when the owner actually wants them merged. |
| Playlists, channels, a dashboard, accounts, rate limiting | Out of scope, permanently. |
| A retry **loop**, exponential backoff, jitter, or any retry library | A transport failure buys exactly one more attempt and everything else buys none (§5.3). The evidence is a blip that cleared inside three minutes: a schedule of attempts would only make the group wait longer for the same apology. The failure path stays local and small on purpose (§4.6). |
| A second Python file | See DESIGN LAW 1 in `AGENTS.md`. |

---

## 7. Repository layout

```
bot.py                    the whole application, plus its self-check
run-bot.command           the macOS launcher. Committed 100755 or it does not double-click.
run-bot.cmd               the Windows launcher. Untested on real Windows.
instalar-bot.cmd          the Windows bootstrap: what /instalar windows links to. §2.2. Untested.
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
insults.jsonl             every time the group called the bot stupid. gitignored. §4.12
.venv/                    gitignored, and it also holds the launcher's dependency stamp
.tarball-install          only in a copy the Windows bootstrap made. Never in this repo. §2.2
```

Nothing in this repo is generated. Everything tracked is either the application, its pins, or a
document a human or an agent is expected to read.
