# The always-on host

Load when: setting up or debugging the machine that hosts the bot 24/7, or working on
`serve.py`, `run-server.cmd` or the mute switch. Ignore when: working on the friends'
launchers (`run-bot.command`, `run-bot.cmd`) — those are a person double-clicking, and
every question they ask is answered by somebody who is sitting there.

The machine: a headless Debian VM on an old Windows 7 ASUS, somewhere the owner cannot
reach most of the day. **Windows 7 cannot host the bot itself** -- its Python ceiling is
3.8 and every current yt-dlp needs 3.10+, measured in `docs/server-vm.md`, which is where
the VM, its sizing and the Linux install live. Read that first; this file is about what
the supervisor does once it is running.

It is the **fallback host**, not an exclusive one: the owner still hosts from his laptop
whenever he wants, and this machine gets out of the way and comes back by itself.

## The three pieces

| piece | what it is |
|---|---|
| `run-server.cmd` | a path, a check and a loop. Its only job is restarting `serve.py` if the *interpreter* dies. Cannot be tested from a Mac, so it holds nothing worth testing |
| `serve.py` | the supervisor: pull, install a changed pin, probe, run `bot.py` as a child, restart it forever. All the logic, tested on macOS |
| `/apagar` and `/prender` | the remote control, over Telegram, owner-only. Mutes the bot **without stopping the process**: `.paused` next to `bot.py` is the state, and it survives every restart |

## One-time setup on the machine

**On Linux -- the actual host -- this whole section is one script:** `instalar-servidor.sh`
does apt, the clone, the venv, the pins, the token and the systemd unit. See
`docs/server-vm.md`. What follows is the **Windows** path, which applies to a Windows 10+
machine and is kept because `run-server.cmd` exists for one.

**Install it with `git clone`, NOT with `instalar-bot.cmd`.** The bootstrap exists for a
friend who has no git: it downloads a source tarball, which leaves a folder that is not a
repository. On this machine that would silently cost the thing that matters most — the
`git pull` in `serve.py` is the only way a fix reaches a computer nobody visits, and
without a repo it does nothing, forever, in silence.

0. **The prerequisites Windows does not ship.** From a terminal, using winget (Windows 10
   1809+); the URLs are the fallback when winget is missing:
   ```
   winget install --id Git.Git
   winget install --id Python.Python.3.12      # 3.11 or newer; python.org/downloads/windows
   winget install --id Gyan.FFmpeg             # gyan.dev/ffmpeg/builds  <- the usual sticking point
   ```
   Then **close and reopen the terminal** so PATH is picked up, and check all three:
   `git --version`, `python --version`, `ffmpeg -version`. ffmpeg is not optional: 720p
   on YouTube arrives as separate video and audio streams and ffmpeg is what merges them.
   Installed from a zip instead of winget, its `bin` folder has to be added to PATH by
   hand or `run-bot.cmd` will keep saying it is missing.
1. **Clone, then `run-bot.cmd` once.**
   ```
   cd %USERPROFILE%\Documents
   git clone https://github.com/medinajuanpablo-dev/pibes-laburantes-bot.git
   ```
   `run-bot.cmd` installs the venv and the pins and asks for the token. It will also ask
   whether to take the bot over if the owner's laptop is polling right then — answering
   `n` and closing is fine, the setup is already done by that point. Let it get as far as
   the bot actually starting, then close the window. `run-server.cmd` refuses to guess at
   any of that: it asks no questions, because nobody is there to answer.
2. **Add the owner's Telegram user id to `.env`**, on its own line:
   `TELEGRAM_OWNER_ID=123456789`. Without it `/apagar` and `/prender` do not exist —
   which is the correct default everywhere else, and the reason the friends' laptops
   need no extra setup and cannot be muted by anybody.
   **To find the id:** run the bot on your own laptop, send it `/apagar` in a DM, and
   read the line in that window — `user 123456789 asked to apagar the bot and is not the
   owner`. The bot never answers that command to a stranger, on purpose; the log line is
   the whole feedback, and here it doubles as the way to bootstrap this setting.
3. **`run-server.cmd`.** Leave the window open. `server.log` next to it holds everything,
   rotating at 5 MB, two files.
4. **Prove it from the phone, before leaving the room.** Post a link in the group and watch
   it arrive. Then `/apagar`, post another one, and watch **nothing** happen — that is the
   remote control working. Then `/prender`. Anything else (a reply, silence to `/prender`)
   means `TELEGRAM_OWNER_ID` is wrong, and `server.log` says which id actually asked.
5. **Prove the yield, too.** Open `run-bot.command` on the laptop and answer `s`: the ASUS
   window logs that it stopped receiving. Close the laptop's window and, within five
   minutes, the ASUS logs `starting bot.py` again with no help from anybody.

## Making it survive the room it is in

None of this is verified from here — there is no Windows on the owner's Mac (`README.md`
§7) — so treat it as the checklist to walk once, on the machine:

- **Sleep off.** Settings → System → Power → Screen and sleep → *When plugged in, put my
  device to sleep after: Never*. A sleeping host is an off host.
- **Start on log-on.** Task Scheduler → Create Task → trigger *At log on*, action *Start
  a program* → `run-server.cmd`, and *Run only when user is logged on* so it keeps its
  console window. Set *Start in* to the repo folder.
- **Log on by itself after a reboot.** `netplwiz` → uncheck *Users must enter a user name
  and password*. On Windows 11 that checkbox is hidden until the
  `DevicePasswordLessBuildVersion` registry value is set to 0; without auto-login, the
  task above never fires after a power cut.
- **Come back after a power cut.** BIOS setting, not Windows: on ASUS boards it is
  *Restore AC Power Loss* → *Power On*.
- **Windows Update will reboot it.** Nothing here prevents that; auto-login plus the
  log-on trigger is what makes the reboot cost minutes instead of the rest of the day.

## What the supervisor does, and what it deliberately does not

**It never fights for the token.** Before each start it runs the same long-poll probe the
launchers use (`README.md` §4.9): a 409 means somebody else is polling, and it waits five
minutes and asks again. So when the owner hosts from his laptop, this machine's bot yields
on its own, and comes back **within five minutes of him closing his window** — no command,
no trip to the ASUS. Without that probe, a supervisor plus the `give-up` path is an
infinite baton war: revive, steal the poll, get displaced, revive.

**The cost of the probe, measured:** an ordinary crash costs ~15 s to recover, not 5 — ten
of them are the probe holding a poll to see whether anybody displaces it.

**It pulls, so the owner never travels for a rot fix.** Each cycle does `git pull --ff-only`
and reinstalls when `requirements.txt` changed (its own stamp, `.venv/.requirements-served`,
so it cannot collide with either launcher's). A pushed pin bump lands on the next restart.

Not built, and not by accident:

- **No heartbeat, no status ping, no watchdog over the network.** The child exiting is the
  only failure this can detect, so it is the only one it claims to handle. "The house lost
  power" still shows up as a group with no bot and nobody told.
- **No `--take-over` from this machine, ever.** It is the fallback, and the owner is the
  only other host.
- **A change to `serve.py` itself needs the window closed and reopened.** The pull brings
  it, but the running interpreter keeps the version it started with. Fixing that means a
  supervisor over the supervisor; `bot.py` changes, which is the file that actually
  changes, do take effect on the next restart.
- **A muted bot still holds the token.** `/apagar` silences the group's bot; it does not
  hand the poll to anybody. That is the point — the process stays up and reachable.
