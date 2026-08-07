# the-bot

A private Telegram bot for one group of friends. Someone pastes a YouTube / Instagram / Facebook
link; the bot replies with the video or image inline, so nobody has to leave the chat.

One file, `bot.py`. No database, no queue, no Docker, no process manager — at ~20 links a week
none of those buy anything.

## Requirements

- Python 3.11+
- `ffmpeg` on your `PATH` (`brew install ffmpeg` / `apt install ffmpeg`). It is **required**: the
  720p quality cap works by merging separate video and audio streams, which is ffmpeg's job.

## Run it

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
TELEGRAM_BOT_TOKEN=<your-token> .venv/bin/python bot.py
```

The token comes from [@BotFather](https://t.me/BotFather) and lives only in your environment.
It is never written to a file in this repo. Keep it in a gitignored `.env` you source yourself if
you like typing less.

Self-check — the pure helpers run offline, then it really downloads one short clip from each of the
three sites and asserts with `ffprobe` that what came back is something Telegram plays inline:

```sh
.venv/bin/python bot.py --self-check
```

### Optional: `YTDLP_COOKIES`

Insurance, not a requirement — **all three sites extract anonymously today** (measured 2026-08-07;
see Operations). If that changes, set `YTDLP_COOKIES` to the path of a Netscape-format cookie file
and the bot passes it to yt-dlp; leave it unset and nothing changes. Use a throwaway account, never
your own, and keep the file out of git (`cookies.txt` and `*.cookies.txt` are already ignored).

## BotFather setup — the bot does nothing without this

New bots run in **privacy mode**, where they only see commands aimed at them. A plain link pasted
in a group is invisible to them. Two steps, both in a chat with @BotFather:

1. `/setprivacy` → pick the bot → **Disable**.
2. **Remove the bot from the group and add it again.** The change does not apply to groups the bot
   is already in.

Verified against Telegram's own documentation on 2026-08-07, which still states, of disabling
privacy mode: *"The bot will need to be re-added to the group for this change to take effect."*
(https://core.telegram.org/bots/features#privacy-mode). The alternative Telegram documents is
making the bot a group **admin** — admins always receive all messages, privacy mode or not.

Then add the bot to the group and paste a link.

## Operations

**yt-dlp is the part that rots.** The application logic will not break; the extractors will,
because YouTube, Instagram and Facebook change their pages without warning.

- **When extraction starts failing, update yt-dlp first**: `.venv/bin/pip install -U yt-dlp`.
  That fixes the large majority of breakages, and nothing else is worth trying before it.
- **Known warning, harmless today.** Every YouTube extraction prints:
  *"No supported JavaScript runtime could be found. Only deno is enabled by default… YouTube
  extraction without a JS runtime has been deprecated, and some formats may be missing."*
  Extraction still works — measured on 2026-08-07 at both 2160p and merged 720p. No JS runtime is
  installed on purpose: this project is meant to be copied onto an old Linux box, and a runtime
  dependency added to silence a warning is a cost with no benefit today.
  **If YouTube extraction starts failing with missing formats, that warning is your first suspect.**
  The escape hatch is `--js-runtimes node` (Node is usually already around) or installing `deno`.
- **Telegram's bot upload ceiling is 50 MB** for video, animation and generic files, and 10 MB for
  photos (https://core.telegram.org/bots/api#sending-files, checked 2026-08-07). The quality cap is
  sized against that, not against what the source video happens to be.
- **What the group actually gets**, downloaded and inspected with `ffprobe` on 2026-08-07, all three
  anonymously with no cookies:

  | Site | Result | Merge |
  |---|---|---|
  | YouTube, 3.5-min video | 1280x720 H.264/AAC mp4, 29,969,207 B | yes, ffmpeg |
  | Instagram reel, 30 s | 772x720 H.264/AAC mp4, 1,272,833 B | no |
  | Facebook reel, 27 s | 720x900 H.264/AAC mp4, 1,881,291 B | no |

  The format string prefers H.264/AAC deliberately: that is what Telegram clients play inline
  rather than showing as a file. On YouTube it costs about 9 MB over the AV1 alternative, which is
  a good trade against a 50 MB ceiling.
- The bot only answers links to those three sites. Anything else in the chat is ignored rather than
  attempted and apologised for.
- **If a download comes out over the ceiling, the bot replies with a link instead of failing.** The
  decision is made on the real size of the finished file, never on yt-dlp's pre-download estimate —
  that estimate reports `NA` on both Instagram and Facebook, so anything built on it would be dead
  code. The cost is that an oversized video is downloaded before being turned down, which is fine
  at this volume.
