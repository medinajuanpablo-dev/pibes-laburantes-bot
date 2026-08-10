# AGENTS.md — read this before changing anything

A private Telegram meme bot. **One group of friends, ~20 links a week.** That number is the design
input for every decision here; if a change only makes sense at higher volume, it does not belong.

Everything is `bot.py`. Its self-check is in the same file: `python bot.py --self-check`.

## Routing — where the answers are

| You need… | Go to |
|---|---|
| what it does, how to run it, the architecture | `README.md` §1–§2 |
| the launchers, the baton pass, one-poller-per-token | `README.md` §2.1 and §4.9 |
| `/instalar`, its line budget, why it is a message and not an attachment, the one parse mode in this file | `README.md` §2.2 ← read before touching `install_reply` |
| the Windows download, the bootstrap, and **which copy updates how** | `README.md` §2.2 + `docs/updating.md` ← read before touching `instalar-bot.cmd` or either update path |
| how the owner ships a change, bumps a pin, sets up a new host | `docs/updating.md` |
| what a friend hosting the bot is told (Spanish, product copy) | `EMPEZAR-ACA.md` |
| privacy mode / why the bot sees nothing in a group | `README.md` §3 |
| **every measured fact — codecs, sizes, ceilings, timeouts** | `README.md` §4 ← read before touching `MEDIA_FORMAT` or any timeout |
| what breaks in production and how to diagnose it | `README.md` §5 |
| the ledger of bounced links and `bot.py --rejected` | `README.md` §5.1 |
| which links get answered, which get silence, and why "the bot is down" cannot be one of the answers | `README.md` §5.4 ← read before touching `MEDIA_PLATFORM_HOSTS` or `_handle_links` |
| the insult the bot answers, its two corpora and both thresholds | `README.md` §4.12 ← read before touching `INSULT_WORDS` |
| the Spanish line each named failure gets, and why three of them hedge | `README.md` §5.2 ← read before touching `FAILURE_SIGNATURES` |
| why a failed link gets a second attempt, which yt-dlp knob does **not** reach it, and what a real outage costs the group in seconds | `README.md` §5.3 ← read before touching the retry, `TRANSPORT_RETRY_PAUSE` or `SOCKET_TIMEOUT` |
| carousels, albums, `sendMediaGroup`'s limits | `README.md` §4.10 |
| message entities, the UTF-16 offset trap, why `text_link` is refused | `README.md` §4.11 |
| why something was *not* built | `README.md` §6 |
| how the project got here, and which premises turned out false | `docs/history.md` |
| the original plan and prompt-order (superseded) | `docs/archive/` |

## Design laws

1. **One file until it hurts.** No `src/`, no packages, no class hierarchy, no plugin registry, no
   dependency injection. A second file needs its reason in the commit message.
2. **Nothing OS-specific in `bot.py`.** It gets copied onto an old Linux box. No `/opt/homebrew`
   paths, no `launchd`, no Homebrew assumptions. Resolve ffmpeg from `PATH`. The two launchers and
   the Windows bootstrap are the only OS-specific files here, that is what they are for, and none of
   it may leak inwards. **Describing a platform is text, not behaviour**: `/instalar` naming a
   platform's obstacle is copy, and it still reads nothing about the machine it runs on.
3. **No database, no job queue, no web framework, no Docker, no process manager.** At this volume
   they are cost with no benefit. `systemd` belongs to the port, not here.
4. **Secrets never enter git.** The token is `TELEGRAM_BOT_TOKEN` from the environment, kept in a
   gitignored `.env`. Never a default, never an example, never a comment. Run `git status` before
   every commit and stage explicit paths — never `git add .` or `git add -A`.
5. **Code, comments, commit messages and docs in English. The bot's chat messages are in Spanish** —
   that is the group's language.
6. **Mark deliberate shortcuts with a `ponytail:` comment** naming the ceiling *and* the upgrade
   path.

## Non-obvious things you cannot derive from the code

- **`MEDIA_FORMAT` is load-bearing and its branches are not interchangeable.** Telegram delivers
  AV1-in-webm as a **document** — a grey file row with no playback — which defeats the entire point
  of the bot. The rule is *mp4 container, not AV1*; it is **not** *h264 only*, because vp9 in an mp4
  played inline. Both facts are measured (`README.md` §4.1). The `?` in `height<=?720` is load-bearing
  too: without it Instagram and Facebook match no branch at all.
- **A height cap is not a size guarantee.** Portrait video (720x900, 1440x1800) exceeds a 720 height
  cap while being a small file. The only real size guard is the byte count of the finished file, and
  `filesize_approx` is `NA` on two of the three sites, so anything built on the estimate is dead code.
- **Exactly three places swallow an exception, and all three are load-bearing.** `_apologise()` is
  the last line of defence: if it re-raises, the group gets nothing at all, which happened in
  production. `record_rejection()` and `record_insult()` are diagnostics bolted onto a path that
  has to finish anyway: a full disk or a read-only checkout may not cost the group its apology or
  its answer. Anywhere else, a swallow is a bug — and note what is deliberately *not* protected,
  the insult reply itself, which is why it runs after the links (see `on_message`).
- **`connect_timeout` must be passed explicitly.** python-telegram-bot defaults it to 5.0 s and only
  substitutes its own default when the caller passes nothing, so `write_timeout` alone does not
  protect an upload.
- **The self-check's log-capture raises the logger's level.** `_self_check` configures logging at
  WARNING, so INFO records are dropped before reaching any handler; a logging assert written without
  that is silently vacuous.
- **`ignore_no_formats_error` does nothing while downloading.** yt-dlp's `dl()` calls
  `raise_no_formats(info, forced=True)` and the forced arm raises whatever the flag says. It works
  only with `download=False`, which is why the image fallback probes separately instead of folding
  the flag into `_ydl_options` (`README.md` §4.8).
- **Telegram allows exactly one poller per token, and a conflict here is normal, not a bug.** Two
  pollers both get HTTP 409; the losing bot does not exit, it retries. The launcher's "is anybody
  running?" probe is itself a competing `getUpdates`, so *asking* costs the running instance one
  conflict — which is why `on_error` announces a conflict but only gives up on one that lasts
  `CONFLICT_GRACE`. Exiting on the first one would make the question a remote kill switch, and
  `CONFLICT_EPISODE_GAP` must stay above python-telegram-bot's retry backoff, capped at 30 s.
  Numbers and method: `README.md` §4.9.
- **Two instances of the old build both quit, and the group was left with no bot** — measured
  2026-08-09, the first time the hand-over was ever run for real. `getUpdates` designates no winner:
  it terminates whichever long poll is outstanding, so both sides observe the same sustained
  conflict, and a symmetric rule made both of them conclude they had lost. **The asymmetry cannot
  come from Telegram and there is nothing else between the two hosts** — different laptops, no
  shared state — so it is injected from the one place a human states an intent: the launcher's
  *"¿Se lo saco?"*, carried in as `--take-over`. Three consequences are load-bearing and each has a
  mutation on the list below: the role is fixed at the **episode's start** (an intent expiring
  mid-conflict would flip a standing instance back onto the give-up path and reproduce the bug two
  minutes later); the intent **expires**, or only the first hand-over of the day would work; and the
  taking-over side **never stops**, because that is the only rule that cannot end with nobody
  polling. Two people who both answer yes get an erratic bot and a Spanish line asking one of them
  to close the window — the people are the tie-break, since nothing in the system can be.
- **The insult matcher is tuned against two corpora, and the second one is the feature.** Both
  ship as the check (`README.md` §4.12). The three rules that keep it quiet are not
  interchangeable: both words within one token of each other; **a token may never be longer than
  the word it matches** (difflib scores `bota`→`bot` at 0.857 and `boton`→`bot` at 0.750); a
  threshold per word (`estudio`→`estupido` is 0.800, so 0.85; `vot`→`bot` is 0.667, so 0.66 — one
  number cannot serve a three-letter word and an eight-letter one); and **`NOT_THE_BOT`, because
  the thresholds are provably not enough** — difflib scores by longest common subsequence, so `bro`
  and `vot` are the same number to it, and *"bro que estupido"* fired. Do not move a threshold
  without a phrase the group actually sent, and do not add a "smarter" rule: the residual misses
  (`botestupido`, `robot estupido`) are cheap and the false positives are not.
- **An insult is not a bounced link and does not go in the ledger.** Its own file, its own reason:
  `--rejected` opens with "N links bounced", groups by host, and is the one report that decides
  which site to support next. The two matched words *are* stored, as an argued exception to "no
  message bodies" — they are near-copies of `bot` and `estupido` by construction, so they carry
  nothing private, and without them a false positive is indistinguishable from a real insult and no
  threshold could ever be moved on evidence.
- **`drop_pending_updates=True` is a decision, not a default.** Telegram holds updates ~24 h and
  every handover follows a gap in which nobody hosted, so replaying the queue dumps the whole gap
  into the group at once. The accepted cost is that a link posted while the bot was off is lost.
- **A downloaded `.command` cannot be double-clicked, and it takes two independent things to make
  one that can.** It needs the exec bit **and** no `com.apple.quarantine`; a download has neither.
  All four combinations were measured on 2026-08-09 (macOS 15.1.1), along with what Telegram
  Desktop actually writes — mode `644`, quarantined — so an attachment fails both tests at once
  (`README.md` §2.2 has the table). `git clone` supplies both, because git stores the mode
  (`run-bot.command` is `100755` in the index) and writes no quarantine. That is the entire reason
  distribution is a clone rather than a zip **on macOS**, it is why the launcher can update itself,
  and it is why `/instalar mac` hands out a **command** and never a file as an attachment.
  **Right-click → Open does not rescue an attachment**: it speaks to the quarantine half and cannot
  add an exec bit, and the `chmod +x` that would costs the friend exactly the Terminal that sending a
  file was meant to save. A launcher on its own is also inert — its first act is `git pull` in a
  directory with no `.git` — but that objection alone would not settle it, because a bootstrap that
  clones and hands off is still a file you double-click. The two measurements are what settle it.
- **On Windows exactly one of those two facts is false, and that single difference is a whole
  feature.** A `.cmd` needs no exec bit, so a downloaded one runs, and the mark-of-the-web dialog is
  a confirmation rather than a refusal. So `/instalar windows` hands out a **link** to
  `instalar-bot.cmd`, which fetches the repository as a source tarball, unpacks it and hands off to
  `run-bot.cmd`. The inert-launcher objection is exactly why the link is **not** `run-bot.cmd`:
  linking the launcher would move the git step rather than remove it. **Do not build a macOS twin of
  the bootstrap** — there the download cannot run at all — and do not weaken anything on the macOS
  path to make the two symmetric. Two things bind anyone touching either path. A tarball copy has no
  `.git` and updates by re-fetching, so the bootstrap **refuses to unpack over a `.git`**: otherwise
  it leaves a clone's tree dirty, `git pull --ff-only` then refuses it, and the download has
  destroyed the update channel of the one copy that had a working one. And **`git push` is still the
  entire release process for both kinds of copy**, because codeload serves the tip of `main`.
  Everything about the *execution* of the two Windows files is **untested** — no Windows exists in
  this project; `docs/updating.md` separates what was measured here from what was only read.
- **The install reply has a line budget and it is asserted exactly** — three lines per platform,
  five for both. It is a product decision, not formatting: a friend taps and skims, and at 22 lines
  the line that mattered was the one skipped. A line earns its place only by stopping somebody in
  the next minute, which is why each platform gets **its own obstacle and the token, and nothing
  else** — git on macOS, and on Windows the confirmation the machine asks for plus the browser
  showing the file instead of saving it, because that platform installs nothing now. The
  one-host-at-a-time rule and the double-click-from-now-on payoff live in `EMPEZAR-ACA.md`, which
  arrives with the code either way. Adding a line here means taking one out, and **the budget did not
  move when Windows changed mechanism**: two lines per platform before and after. `README.md` §2.2.
- **Exactly one reply in this file sets a `parse_mode`, and `_reply_text`'s default must stay
  `None`.** `/instalar` needs HTML for its code block, which is what makes Telegram offer
  tap-to-copy. Every other reply — the apology, the oversize line with a raw URL in it, the insult
  answer — is unescaped Spanish written by whoever last edited the copy. Turning markup on for all
  of them, or configuring a PTB `Defaults` object, would make the next stray character either vanish
  from the message or fail the send. Inside `/instalar`, every interpolated value goes through
  `html.escape`; the `&&` in the pasted command is the one that bites.
- **The install reply is proved not to carry the token by *invariance*, not by a filter.** `/instalar`
  answers anybody who can message the bot, in any group it was added to, while the process holds the
  group's token in its environment. A "the token is not in the text" assert only catches the value it
  was told to look for; the real guard builds every string the feature can produce under two
  different fake tokens and once with none, and requires all three to be byte-identical — a text
  that does not change when the token changes cannot contain it. That is why `install_reply` and
  everything under it read nothing at all: no environment, no file, no subprocess. **Do not make any
  of it read something**, and do not "improve" the guard into a redaction pass — a filter would let a
  transformed leak through, which is exactly the mutation the invariance assert catches and the other
  two do not. The two fakes must stay token-shaped and different from each other; both are asserted,
  because bait that is not shaped like the quarry arms nothing. `README.md` §2.2.
- **No yt-dlp retry knob reaches a transport failure during metadata extraction, and all three were
  measured saying so.** `retries` is the media downloader's loop (`downloader/http.py`),
  `fragment_retries` the fragment downloaders', and `extractor_retries` only exists where an
  extractor wraps a section in `RetryManager` — nine do and **Instagram is not one of them**. Set to
  10 they still produce exactly one transport attempt (`README.md` §5.3 has the table and the
  method). That is why the second attempt is the bot's own, and why "just set `extractor_retries`"
  is the plausible-sounding fix that does nothing. The discrimination is the **exception**, never
  the message: yt-dlp hangs the original on `DownloadError.exc_info`, transport failures arrive
  there as `networking.exceptions.TransportError`, and `HTTPError` is that class's **sibling**, not
  its subclass — so a 404 or a rate-limit page is an answer and is never retried. A transport
  failure also never reaches the image fallback, because that probe asks the site a question and the
  site is what could not be reached; that is what keeps the retry nearly free on Instagram, where
  the probe used to burn a second `SOCKET_TIMEOUT` arriving at the same apology.
- **`_publish_commands` is the fourth place in this file that swallows an exception**, and it is the
  only one that is not on the delivery path. A `post_init` callback that raises aborts
  `run_polling`, so a network blip while publishing a *menu* would leave a friend staring at a
  traceback with no bot running. It catches `telegram.error.TelegramError` and nothing wider, so a
  real bug in the command list still shouts.
- **An image post's thumbnails carry no dimensions, and the list is not sorted worst-to-best.** A
  reel's thumbnails *do* carry width/height, which makes the wrong assumption easy. Selection is by
  downloaded file size for that reason. Also: `duration` and `title` discriminate image from video
  not at all — `formats` is the only signal. A **carousel slide's** thumbnails behave identically,
  so the album reuses that rule per slide rather than inventing a second one.
- **`telegram.InputMediaPhoto(Path(...))` uploads nothing.** It turns a `Path` or `str` into the
  literal string `file:///…`, and the request layer only rewrites an `InputMedia` whose `.media` is
  an `InputFile`, so the call reaches api.telegram.org with no attached bytes. An **open file
  object** is what makes it an `InputFile` behind an `attach://` URI. Nothing but the real group
  would reveal this, so the self-check asserts the type. `README.md` §4.10.
- **`_ydl_options`' `playlist_items: "1"` hides a carousel from the bot.** With it the fallback
  probe sees `entries: 1`; without it, all of them. Anything measured with a raw
  `yt-dlp --dump-single-json` is therefore a different dict from the one the code gets — carousel
  work goes through `_carousel_options`. `README.md` §4.10.
- **Instagram is the only site whose image posts this bot delivers, and that is the first question
  `_image_fallback` asks.** `is_image_post()` says yes to a *failed* extraction — `ignore_no_formats_error` does not suppress
  a broken Instagram extraction, but YouTube reports both an unavailable video and a
  `Sign in to confirm you're not a bot` challenge through that same no-formats mechanism, so the
  probe comes back with no formats and 38 thumbnails and the function says yes (measured
  2026-08-09). When the video is merely *blocked* rather than dead its thumbnails are **real**, one
  downloads, and the group gets the poster frame of the video it asked for. No downstream guard can
  see that, so the discrimination is the **site**: `has_image_posts()` reads the pasted URL's host,
  and off Instagram the fallback declines before it probes. Host, not yt-dlp's `extractor` key —
  the host is known before the probe and is the same rule `is_supported()` uses (`_host_is_one_of`).
  This also removed the ~38 pointless thumbnail fetches a failing YouTube link used to cost.
  **The reason is not "the other sites have no image posts" — that is false of Facebook**, whose
  extractor accepts `photo.php` and `/posts/`. The true one is narrower: the image path is measured
  on Instagram and nowhere else, and on Facebook a wrong guess *is* the defect, because
  `Cannot parse data` fires under mere throttling and the fallback would answer a good video with
  its poster frame. A Facebook photo post gets the apology; whether it ever worked is unmeasured
  in both directions (`README.md` §4.8 has the upgrade path).
- **Inside Instagram the older guard still carries everything**: **thumbnails that yield no image
  mean the post never was an image post**, so the fallback returns None and `download_into`
  re-raises the extractor's own words. The host guard took away that guard's only cover (the dead
  YouTube link) and mutation testing caught it going green — its check is an Instagram case now,
  and it is not redundant. Do not move either decision up into `is_image_post` — `README.md` §4.8
  lists the up-front signals and why each risks the working image path. Nothing on the single-image
  fallback path may raise an error of its own; that error would replace the extractor's and the
  ledger would record the fallback's opinion.
- **A reply may never claim more than its signature carries, and three of the seven cannot.**
  Facebook's `Cannot parse data` also fires on a good URL under rate limiting, and YouTube's
  deleted and private are byte-identical, so those replies offer both readings. `README.md` §5.2.
  An unrecognised failure must stay `FAILURE_REPLY` — the feature adds precision where precision
  exists and must never turn an unknown into a guess. The mirror holds too: the bot-check row does
  **not** hedge, because that signature is not ambiguous. The DNS row hedges on a different axis —
  **whose** network — and is the only row that asks for the link again, because it is the only
  failure measured clearing on its own; its marker is deliberately narrower than the transport
  failures the retry covers, so a reset connection is retried but not named.
- **The bot-check row is the one signature nobody on this branch could re-measure**, and its
  markers step around the apostrophe in `you're` on purpose — that character comes from YouTube's
  JSON and a typographic one would kill the row as silently as a capital would. `README.md` §5.2.
- **`carousel_slides`' per-entry `formats` guard cannot be covered live.** It only runs once a video
  carousel's download has already failed, and no public URL sits in that state. The live all-video
  carousel in `SELF_CHECK_URLS` proves something weaker — that video carousels still belong to the
  video path — and it was briefly documented as proving more. Dict asserts are that guard's only
  cover; do not delete them believing the network entry has it.

## How to prove a change

- **Ship the check with the change.** A slice without its check is not done. `python -m py_compile
  bot.py` **and** `python bot.py --self-check` pass before every commit.
- **Green is not enough — mutation-test it.** On a clean copy (`git archive HEAD | tar -x -C <tmp>`),
  sabotage one thing at a time and confirm the check goes red. Prove it dies when the feature is
  *absent* **and** when it is *present but wrong*. In this repo mutation testing caught three real
  holes that a passing run did not, including a fix that shipped with no guard at all and a privacy
  assert that covered only one of two branches.
  Mutations that must stay red: `MEDIA_FORMAT` → codec-agnostic · `_send` drops `connect_timeout` ·
  the apology left unprotected · `MESSAGE_FILTER` → `filters.TEXT | filters.CAPTION` ·
  `delivery_decision` `<=` → `<` · the ignore-logging dropping its rejected URLs or leaking the body ·
  `is_image_post` dropping its `formats` or carousel guard · the thumbnail chosen by list order
  rather than by size · the image fallback raising its own error instead of declining when no
  thumbnail yields an image (that is the lost YouTube diagnosis, and it passes every other check —
  its only live cover was the dead YouTube link and the host guard took that away, so it is driven
  on Instagram now) ·
  that same branch declining when an image *did* come down (an image post must still be sent) ·
  the image fallback running for a site with no image posts — removed, inverted, moved after the
  probe (the calls are asserted, not only the outcome), `IMAGE_POST_HOSTS` widened to every
  supported host, emptied, or losing `instagr.am` · `_host_is_one_of` dropping its subdomain arm or
  matching the URL as a substring · the bot-check row deleted, keyed on the apostrophe, written
  with a capital, losing its `[youtube]` marker, or rewritten as a hedge ·
  `main` not registering `on_error` · the conflict handler's `quiet` branch ·
  `CONFLICT_GRACE` set to 0 (a probe would then kill a healthy bot) · the episode reset in
  `conflict_action` · the Spanish line the person at the window reads ·
  `take_over_requested` reading nothing, reading everything, or matching loosely enough that a typo
  starts an instance that will never give the baton back · `main` not turning the flag into the
  deadline, or `on_error` not passing it to `conflict_action` · `conflict_action` ignoring the
  intent (that is exactly the 2026-08-09 bug) · the role read from `now` instead of the episode's
  start · the intent never expiring · `TAKE_OVER_WINDOW` set to 0, so the intent is never live for
  anything · `stand-ground` said on every conflict, on none, or falling through into the stop ·
  the taking-over side reading the incumbent's line, which tells the person the opposite of what
  they just asked for · the give-up line losing its hedge or its "open this again" way back ·
  `run_polling` losing `drop_pending_updates` ·
  either `record_rejection` call site in `_deliver` · a ledger write failure escaping · the ledger
  recording the message body · `on_message` not recording an unsupported host at all, recording only
  the first of its URLs, or sharing a real failure's error class · a message with no URL, or a mixed
  message whose supported link WAS attempted, recorded as unattempted · `entity_urls` slicing the
  text by Python index instead of asking `parse_entity` (an emoji before the link then eats its
  first character, silently) · the entity source replacing the regex instead of unioning with it ·
  a schemeless entity delivered without a scheme (`urlparse` gives it no hostname) · the union
  losing its de-duplication, or accepting `text_link` · a record written without its line ending · `read_rejections` dying on
  a half-written line · the report dropping its error-class or host grouping, or hiding the URLs ·
  any of `carousel_slides`' four guards · an album sent as `Path`s instead of open files · the
  album send dropping `connect_timeout` · `upload_ceiling` holding an album to 50 MiB · `_deliver`
  sizing an album by its first slide instead of its largest · a truncated carousel sent silently ·
  album slides picked by list order or delivered out of order · `ALBUM_MAX_ITEMS` not following
  `telegram.constants.MediaGroupLimit` · `strip_ansi` made a no-op or dropped from
  `rejection_record` · an `ANSI_ESCAPE` greedy enough to eat `[Instagram]` · `_apologise` ignoring
  its detail, or `_deliver` not passing it ·
  `is_transport_failure` reading the `DownloadError` itself instead of what yt-dlp hung under it,
  made constant in either direction, or widened to `HTTPError`/`ExtractorError` (that last one
  retries every restricted post and dead video, and is invisible in production because the group
  still gets the right apology — just twice as slowly, on every failure) · the retry not firing,
  firing more than once, or firing with no pause · `TRANSPORT_RETRY_PAUSE` set to 0 · a transport
  failure reaching the image fallback · the retry note dropped from the ledger's detail, written
  onto a bounce that was never retried, or placed where it breaks `failure_reply`'s match ·
  the DNS row deleted, widened to `failed to perform` or `caused by transporterror` (both appear in
  the same record and both catch failures nobody has measured clearing on their own), rewritten
  without its hedge, or losing the "mandámelo de nuevo" that is the only actionable advice in the
  whole table ·
  `failure_reply` matching with `any` instead of `all`,
  losing its casefold or its strip · an unrecognised failure returning anything but `FAILURE_REPLY`
  · one of the three hedged replies rewritten as a certainty · a `FAILURE_SIGNATURES` marker
  written with a capital (it can then never fire) · a row added that no measured signature reaches
  · the YouTube row keyed back on the bot's own sentence instead of the extractor's
  · the ledger storing the friendly reply instead of the raw detail ·
  the insult never answered, answered but not recorded, recorded but not answered, answered with
  anything but the owner's exact sentence, or answered *before* the links (`_deliver` cannot raise
  and an ordinary reply can — the order is the protection) · `on_message` losing either half ·
  the length rule dropped, so `bota` and `boton` become insults · `INSULT_MAX_GAP` widened to four
  or closed to zero · either threshold moved in either direction (0.66/0.85 each sit in a measured
  gap) · `NOT_THE_BOT` emptied or stopped being consulted (`bro que estupido` fires), or given a
  **dead** entry that would not have matched anyway ·
  the accents, the casefold or the run-collapsing dropped from `insult_tokens` · the insult
  half reading `text` but not `caption` · the record or the log line carrying the message body ·
  an insult write failure escaping · insults written into `rejected.jsonl` ·
  `/instalar` not registered as a handler · `post_init` not wired, so the command menu is never
  published · `_publish_commands` letting a failed publish escape (that aborts `run_polling`) or
  dropping `start` from the list · `on_install` losing its `parse_mode`, or HTML becoming
  `_reply_text`'s default · `html.escape` dropped from the pasted command, so a raw `&&` reaches
  Telegram · a bare `/instalar` picking one platform instead of serving both · an unrecognised word
  erroring instead of meaning both · either platform's block naming the other's launcher, or the
  pasted command stopping at the folder instead of opening the launcher ·
  the token interpolated into the reply whole, as its secret half, from the handler instead of the
  builder, or **reversed** (that last one is invisible to the name and shape asserts and is what the
  invariance assert is for) · a fake token that is not token-shaped, or the two fakes made equal ·
  `CLONE_URL` pointed at another repository or written as the ssh remote · `CLONE_DIR` keeping its
  `.git` suffix · `BOOTSTRAP_URL` hand-written instead of derived, pointed at another repository, or
  losing its `main` or its filename · either warning dropped — the owner-hands-out-the-token line,
  the macOS Command Line Tools dialog, or Windows' *Ejecutar de todas formas*. Note that a bare
  `"git" in text` assert is **vacuous** twice over: the pasted command contains `git clone`, and the
  Windows reply's link contains `raw.githubusercontent.com`. · the Windows reply losing its download
  link, putting it inside a `<pre>` (a URL in a code block is not tappable), naming `run-bot.cmd`
  instead of the bootstrap (that copy would run and never update again), or going back to demanding
  git · `instalar-bot.cmd` fetching a different repository than `CLONE_URL`, dropping the
  `--exclude` that keeps it out of its own unpack, or not writing the `.tarball-install` stamp ·
  `run-bot.cmd` not reading that stamp, keying on some other file, not naming `instalar-bot.cmd` in
  the sentence it prints, or **losing the "no se puede actualizar sola" line**, which is still true
  of a hand-unpacked zip · an accent or `ñ` added to either `.cmd` (cmd.exe reads them in the OEM
  codepage, so it is mojibake in the window a friend is reading) · note that asserting a name is
  *somewhere* in a `.cmd` is **vacuous**: both files carry both names in their comments, so the stamp
  checks pin the redirection that writes it, the `if exist` that reads it, and the `echo ` lines a
  friend actually sees · **a line added to the reply, or one removed** —
  the budget is three lines per platform and five bare, and it is asserted exactly, because the defect
  it replaced was a 22-line wall nobody read to the end of.
- **The self-check really downloads six times** — YouTube, an Instagram reel, an Instagram image
  post, Facebook, an Instagram image carousel and an Instagram video carousel. That is deliberate:
  extraction rotting is this project's actual failure mode and only a real download detects it.
  Keep the clips short, and when you change one, verify the codec mutation still goes red on it — a
  clip that only offers h264 would silently empty that check. Entries are
  `(url, expected_kind, expected_files)`; both are asserted, so a reel arriving as a still fails and
  so does a carousel that loses a slide.
- **Any check that reaches `on_message` or `_deliver` must swap `REJECTED_LEDGER` *and*
  `INSULT_LEDGER` first.** Both paths write — the unsupported-host path fires on a plain text
  message, and the insult path fires on one with no link at all — and a self-check that forgets
  leaves invented links in the owner's real `rejected.jsonl`, which is the file he reads to decide
  what to build. Swap the module globals and restore them in a `finally`, like every other check
  here does.
- **You cannot test the Telegram layer without a token, and you should not try.** No `.env` exists in
  a fresh worktree. Deterministic checks are yours; the live run belongs to whoever holds the token.
  *"I could not test this live"* is the correct note, not a failure.

## Open, known, and deliberately unfixed

- **The second URL source is verified by construction only.** `entity_urls()` reads Telegram's own
  `url` entities so a schemeless `youtu.be/xyz` is no longer invisible (`README.md` §4.11), but
  **nobody has posted one into the real group and watched what Telegram sends.** Every entity the
  self-check drives is one the check built, with offsets written the way the Bot API documents them
  — UTF-16 code units. Treat "Telegram sends a `url` entity for a bare domain, at these offsets" as
  an assumption until a real paste confirms it. `text_link` is refused deliberately, and while it
  is refused its demand is **invisible**: a message carrying only one logs `no URL recognised` and
  writes no ledger record, so nobody will see it building up.
- **The oversize → direct-link path has never run against Telegram.** Nothing in the live session
  exceeded 50 MB. It is covered by asserts and a source read only. Treat it as unproven.
- **Mixed photo/video Instagram carousels are still refused, and still unmeasured.** All-image ones
  are albums now (`README.md` §4.10), but the one public mixed example named in yt-dlp #7569 is
  auth-walled as of 2026-08-09, so a mixed post gets the apology rather than a half-album. Telegram
  itself would take the mix; yt-dlp is the obstacle (#7569, #11792). Upgrade path: find a live one,
  check whether its video entries download under `MEDIA_FORMAT`, and only then build it.
- **A carousel of more than 10 slides has never been seen.** Telegram's album is 2–10 items, and the
  code caps and announces the cut in Spanish, but no public post has exercised it — Instagram's own
  carousel was 10 items when the feature launched. The branch is asserted with a stand-in Media.
- **Nobody has watched python-telegram-bot hand a `Conflict` to `on_error`.** The rule and the
  handler are asserted, and the library's own code routes polling errors to `process_error`, but
  the end-to-end path needs two live instances on the real token.
- **The fixed hand-over has not been run end to end.** Both roles are driven over a simulated
  timeline and every branch of the launcher's probe was driven with its real lines, but two
  instances on one token is the owner's experiment, not something a check can reach. What it has to
  confirm: the taker survives the incumbent's 60 s, the incumbent stops with the new sentence, and
  `getUpdates` afterwards shows **somebody** polling. The two-people-both-say-yes standoff is even
  further from cover — it needs three machines or two runs of the launcher answered yes twice.
- **Nobody has ever insulted the bot for real.** Every phrase in both corpora was invented by an
  agent imagining how this group types, so "41 ordinary messages stay quiet" measures the lists,
  not the group — and the list's limits are demonstrated, not hypothetical: `bro que estupido`
  fired and was caught by a review pass, not by the corpus that had just gone green. The first
  real false positive is worth more than all of them; when one arrives, the two words are in
  `insults.jsonl` and the fix is a corpus line plus a `NOT_THE_BOT` entry.
- **Neither Windows file has ever run on Windows.** `run-bot.cmd` and `instalar-bot.cmd` were both
  written on a Mac and only statically checked — ASCII, CRLF, every `goto` has a label, no bare `&`
  in an `if` — including the launcher's `--take-over` path, which is the mirror of the macOS one
  whose branches *were* driven. Say "untested" in that word until somebody watches them;
  `docs/updating.md` lists what to watch and, for the bootstrap, separates the parts that **were**
  measured here (the tarball URL, what the archive contains, that the unpack cannot touch `.env` or
  `.venv`, that a truncated download is caught first) from the part that cannot be: whether cmd.exe
  runs any of it. The likeliest place a friend gets stuck is not the script at all —
  raw.githubusercontent.com serves a `.cmd` as `text/plain` with no `content-disposition` (measured),
  so the browser is expected to display it and the friend has to save it, which is why the reply
  spends words on Ctrl+S. `/instalar windows` no longer hands out a Git Bash command line at all, so
  the old "does `./run-bot.cmd` work from Git Bash" question is gone with it.
- **Nobody has seen the install reply rendered by a Telegram client.** The HTML is asserted offline
  — allowed tags only, no bare `&`, under the length ceiling — but no message has been sent, so
  "the `<pre>` block offers tap-to-copy" is read from Telegram's documentation and not measured.
  What to watch on the first real send: that the block renders as a block with a copy affordance,
  that the `&&` arrives as `&&` and not `&amp;&amp;`, and that the whole thing is one message. The
  shrink added one thing to watch: the reply now carries **no blank lines at all**, on the
  assumption that a `<pre>` block separates itself visually from the sentence above it. If it does
  not, the fix is a blank line and a bumped number in the budget assert, not a rewritten reply.
- **Why yt-dlp coloured one ledger record and not the next is unknown**, and the search was stopped
  on purpose after the TTY theory was refuted (pipe and pseudo-TTY both produced no escapes). The
  strip is unconditional so the cause does not matter; do not gate it on a condition to "fix it
  properly", and do not restart the hunt for a cosmetic defect.
- **The ~38 pointless thumbnail requests a dead YouTube link used to cost are gone**, as a side
  effect of the host guard rather than as a goal: the fallback now declines before it probes, so a
  failing YouTube or Facebook link makes no extra request at all. The open question this replaces —
  finding an up-front signal *inside* Instagram — is still closed for the same reason as before
  (`README.md` §4.8): every candidate risks the working image path. Do not re-open it.
- **Only seven failures are named; everything else is still `no pude bajar ese link`.** That is the
  design, not a gap. A row costs a measurement against the live site — never a guess from an issue
  tracker or from what an error "probably" says. The sixth is the closest this has come to
  breaking that rule: the owner measured it, the branch that added it could not (`README.md` §5.2).
  The seventh cost nothing to measure: production produced it and the ledger wrote it down.
- **`pool_timeout` is left at its 1.0 s default.** It governs contention for a 256-connection pool
  that a sequential bot never contends for.
- **PTB processes updates sequentially**, so a slow upload blocks the handler for its duration. The
  upgrade path, if it ever matters, is passing a file object instead of a `Path` — which also stops
  holding 50 MB in RAM.
