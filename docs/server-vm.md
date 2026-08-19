# The Linux box inside the Windows 7 machine

Load when: building or debugging the always-on host, which is a headless Debian VM
running on a Windows 7 desktop. Ignore when: working on the bot itself, or on the
friends' launchers. The bot-side setup that happens *inside* this VM is
`instalar-servidor.sh`, and what the supervisor does once it runs is `docs/server.md`.

## Why a VM at all, measured

The machine that can be left on is a Windows 7 ASUS, 6 GB of RAM, and somebody uses it
for Office and browsing. Both requirements are hard: **Windows 7 stays**, and **both
systems run at the same time** — which rules out dual boot, since only one of them is
booted at a time.

Windows 7 cannot run the bot at all, and this is not a matter of writing older code:

| what was measured, 2026-08-19 | result |
|---|---|
| yt-dlp `2026.8.18` nightly and python-telegram-bot 22.8 | both declare `requires_python >= 3.10` |
| newest Python with a Windows 7 installer | **3.8.10**, June 2021 — 3.9 dropped Windows 7 |
| yt-dlp `2024.8.6`, the newest that runs on Python 3.8, against YouTube | `Please sign in` — it never reaches the metadata |
| the same yt-dlp against Instagram | `rate-limit reached or login required` |
| the same yt-dlp against Facebook | `No video formats found!` |
| yt-dlp's own README, every Windows binary | "Win8+" |

The same three URLs downloaded fine minutes earlier with the current nightly. **The part
that has to be current is not this project's code — it is the extractor**, and the sites
it fights change every few weeks. A rewrite in "older technology" would produce, after
days of work, a bot that answers *"no pude bajar ese link"* to everything, forever, with
no upgrade path. That is the whole argument for the VM.

## The host, and the VM's shape

VirtualBox **6.1.50** is the last release of the branch that supports a Windows 7 host
(6.1.50 is the newest 6.1.x on `download.virtualbox.org`; 7.x wants 8.1+, which could not
be confirmed from Oracle's docs — those pages are JS-rendered — so try 7.x first if you
like, and fall back).

With 6 GB on the host, the split is comfortable rather than tight:

| setting | value | why |
|---|---|---|
| RAM | **2048 MB** | the bot needs far less — measured 63 MB idle and a 102 MB peak downloading a 27 MB video with the ffmpeg merge — so this is headroom for whatever else the box gets used for. Windows 7 plus Office is happy with the remaining 4 GB |
| CPU | **2 cores** | one is enough for the bot: ffmpeg only *remuxes* here (the selector picks avc1+mp4a and the merge is a stream copy), so nothing re-encodes. The second core is for the future uses |
| Disk | **30 GB**, dynamically allocated | Debian without a desktop is ~2 GB. The file grows only as it is used |
| Network | **NAT** | the bot only makes outbound connections: nothing to forward, nothing exposed. See *Reaching it from outside* if that changes |
| Guest type | Debian (64-bit) | **if the 64-bit options are missing from that dropdown, VT-x is off or absent.** Turn it on in the BIOS; if the CPU truly lacks it, the guest must be 32-bit and the distro has to be Debian 12 i386 — that still works, because `curl-cffi` publishes a `manylinux2014_i686` wheel, but Debian 13 has no i386 installer |
| Audio, USB, clipboard, shared folders | off | a server has no use for any of it, and each one is a driver in the guest |

## Installing the guest: minimal on purpose

Debian **13.6.0 amd64 netinst** (`cdimage.debian.org/debian-cd/current/amd64/iso-cd/`),
which ships Python 3.13 — comfortably above the 3.10 floor. Ubuntu Server LTS is the
alternative if you want the most cloud-identical box; Debian is chosen here for being
lighter and free of snaps.

At the installer's **Software selection** screen, the whole point of this section:

- **uncheck** *Debian desktop environment* and every desktop under it — this is what makes
  the difference between ~100 MB and ~1 GB of RAM at idle,
- **check** *SSH server* — the only way you will ever talk to this machine comfortably,
- leave *standard system utilities* checked.

Everything else is the default: guided partitioning, whole (virtual) disk, and let it
create its own swap.

## After the first boot, in the guest

```sh
sudo apt update && sudo apt full-upgrade -y      # the ISO is a snapshot, this is today
curl -fsSLO https://raw.githubusercontent.com/medinajuanpablo-dev/pibes-laburantes-bot/main/instalar-servidor.sh
bash instalar-servidor.sh
```

That script installs git, python3, ffmpeg and unattended-upgrades, clones the repo, builds
the venv from the pins, asks for the token, and installs the `the-bot` systemd unit that
keeps `serve.py` alive across reboots. **systemd replaces `run-server.cmd`'s outer loop and
nothing else**: the bot's own restarts, the `git pull`, and the yielding to another host all
stay inside `serve.py`, which is where they are tested.

Then prove it, and do not skip this — it is six real downloads:

```sh
cd ~/pibes-laburantes-bot && .venv/bin/python bot.py --self-check
free -m                                          # in another session, while it runs
```

If the self-check passes inside the guest, this machine can host the bot. If it does not,
nothing else about the setup matters yet.

## Starting the VM with the host, headless

The VM must come up when whoever uses the ASUS logs into Windows, and it must not show a
window. On the host:

```
"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" startvm the-bot --type headless
```

Put that in Task Scheduler with an *At log on* trigger. Two things to know:

- **The host shutting down stops the VM the way pulling a plug does.** ext4's journal
  handles it and `serve.py` starts everything again at the next boot; nothing here tries to
  make Windows shut the guest down gracefully.
- Windows Update reboots the host. With the log-on trigger plus auto-login, that costs
  minutes; without auto-login it costs however long until somebody logs in.
- BIOS: *Restore AC Power Loss → Power On*, so a power cut does not end the day.

## Reaching it from outside (optional, and not the bot's problem)

The bot needs no inbound access — it polls Telegram outward, which is why NAT is enough.
For the *other* uses, SSH from anywhere without touching the router: install Tailscale in
the guest (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`) and the
box becomes reachable by name from your laptop like a cloud VM. Deliberately not part of
`instalar-servidor.sh`: it is an account with a third party, and the bot works without it.

## What was not done, and would be the next thing

No cloud-init, no preseed file, no image to re-flash. The install above is a person
clicking through a netinst once, and it is written down here instead. If this box ever has
to be rebuilt repeatedly, a preseed is the upgrade path — it was skipped because it is more
machinery than three unchecked boxes are worth.
