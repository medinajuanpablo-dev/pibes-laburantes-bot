#!/usr/bin/env python3
"""The always-on host's supervisor: keep bot.py running on a machine nobody is at.

Run it with `python serve.py`. On Windows that is what run-server.cmd does; there is
nothing OS-specific in this file, and that is on purpose -- the Windows launchers are
the one part of this project that cannot be tested from the owner's Mac (README.md §7),
so every decision that could be wrong lives here, where the self-check can drive it,
and the .cmd stays a loop and a path.

What it does, in the order it does it, once per cycle:

  1. `git pull --ff-only`, then reinstall the pins if requirements.txt changed. This is
     the whole reason the owner does not have to travel to the machine when yt-dlp rots
     (README.md §5.5): he pushes a bumped pin and the next cycle installs it.
  2. Ask Telegram whether somebody else is already polling this token, with the same
     long-poll probe the launchers use -- and if there is, WAIT. Never take over.
  3. Run bot.py as a child process and stream its log into a rotating file.
  4. When the child exits, for any reason at all, go back to 1.

Two things it deliberately is not:

* not a daemon. It has no PID file, no detach, no signal protocol. Whoever starts it
  owns it: run-server.cmd on Windows, or a terminal window anywhere else. Stopping it
  is closing that window, which is the same gesture as stopping the bot itself.
* not a watchdog over the network. It does not ping the group, does not check whether
  Telegram believes the bot is alive, and cannot tell a muted bot from a busy one. The
  child exiting is the only failure it detects, because it is the only one it can act
  on.

ponytail: the child is a SUBPROCESS rather than run_polling() in a loop inside this
file, and that buys the two things the loop cannot. A yt-dlp or ffmpeg crash that takes
the interpreter down kills one cycle instead of the supervisor, and a `git pull` that
brings a new bot.py takes effect on the next cycle -- an in-process loop would keep
running the code it imported at start-up and quietly never update. The ceiling: this
file itself is only re-read when the supervisor restarts, so a change to THIS file
needs the window closed and reopened. Named in docs/server.md rather than solved,
because solving it means a supervisor over the supervisor.
"""

import logging
import logging.handlers
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_FILE = HERE / "server.log"

# One long poll, and the reason it has to BE a long poll rather than the timeout=0 it
# looks like it could be: a new getUpdates never loses the race, so a probe that does
# not hold the poll always answers 200 and can never see anybody (README.md §4.9).
PROBE_TIMEOUT = 10
PROBE_DEADLINE = 25

# How long to leave the token alone after finding somebody else on it. The owner
# hosting from his laptop is the only somebody there is (nobody else has the token),
# and the whole point is that this machine yields to him and comes back by itself when
# he closes his window -- so this is the worst-case gap between him stopping and the
# group having a bot again. Five minutes: short enough not to be noticed on a chat
# nobody is watching at that moment, long enough that a whole afternoon of hosting
# costs about a hundred probes and no conflict lines in his window.
BUSY_WAIT = 300.0

# A crashed child is restarted almost at once, and a child that keeps crashing is
# backed off up to a minute. Both halves matter on an unreachable machine: the first
# means an ordinary blip costs the group five seconds, the second means a permanently
# broken state (a revoked token, a disk with no space) does not become a hot loop
# hammering Telegram and filling the log all night.
RESTART_DELAY = 5.0
RESTART_DELAY_MAX = 60.0

# A child that lived at least this long counts as a healthy run, so the next crash
# starts the backoff over. Without it, one bad night would leave the machine on the
# maximum delay forever.
HEALTHY_RUN = 300.0

# Its own stamp file, deliberately not either launcher's: run-bot.cmd compares
# requirements.txt against .venv\requirements-installed.txt and run-bot.command keeps a
# sha256 in .venv/.requirements-sha256. Sharing one of those would make this file and a
# launcher each think the other's install was its own.
STAMP = HERE / ".venv" / ".requirements-served"

log = logging.getLogger("the-bot.server")


def configure_logging() -> None:
    """Log to a rotating file AND to the console.

    The file is what the owner reads over VNC or whatever remote he has, months after
    the fact; the console is what he reads the day he sets the machine up. 5 MB times
    two is enough to hold a bad night in full without being a file that has to be
    managed -- an unbounded log on the disk of a machine nobody visits is a slow
    outage, and the bot at INFO writes a line per delivery, forever.
    """
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=1),
        logging.StreamHandler(),
    ]
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO, handlers=handlers
    )


def read_env_file(path: Path) -> dict[str, str]:
    """Parse .env the way the launchers do, so the child gets the same environment.

    Both launchers source this file with the shell before running the bot; nothing
    starts the bot without it. A supervisor that ran python directly would hand the
    child an environment with no token, so it is read here rather than depended upon.

    KEY=VALUE per line, `#` comments, quotes stripped. Not a dotenv implementation and
    not a new dependency for one: the file this reads is written by the launchers, one
    line at a time, in exactly this shape.
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def child_environment() -> dict[str, str]:
    """The environment bot.py runs in: this process's, plus .env, minus buffering.

    PYTHONUNBUFFERED is what makes the child's log arrive line by line instead of in
    8 kB blocks -- without it the log of a bot that hangs would be missing exactly the
    lines that say why.
    """
    env = dict(os.environ)
    env.update(read_env_file(HERE / ".env"))
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run(*command: str, timeout: float = 300.0) -> bool:
    """Run a command, log what it said, and never raise. True if it succeeded.

    Every caller here is housekeeping -- an update, an install -- and none of them is
    worth not starting the bot over. A machine with no network must still run the bot
    it already has.
    """
    try:
        done = subprocess.run(
            command, cwd=HERE, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("%s did not run: %s", command[0], error)
        return False
    output = (done.stdout + done.stderr).strip()
    if done.returncode != 0:
        log.warning("%s exited %d: %s", command[0], done.returncode, output[:400])
        return False
    if output:
        log.info("%s: %s", command[0], output[:400])
    return True


def update_from_git() -> None:
    """Pull the owner's fixes. Never fatal, and quiet when there is nothing to pull.

    GIT_TERMINAL_PROMPT=0 for the same reason the launchers set it: a remote that wants
    credentials must fail rather than block forever on a prompt in a window nobody is
    reading -- and here, nobody is even in the building.
    """
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    run("git", "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=20", "pull", "--ff-only")


def install_requirements_if_changed() -> None:
    """Install the pins when requirements.txt differs from what was last installed.

    Reads the file rather than hashing it: the stamp is the file's own text, so the
    comparison cannot disagree with the thing it is about, and the whole file is a few
    hundred bytes. A pushed pin bump is worth nothing if this does not run -- that is
    the entire remote-maintenance story for this machine.
    """
    requirements = HERE / "requirements.txt"
    try:
        want = requirements.read_text(encoding="utf-8")
    except OSError:
        log.warning("no requirements.txt next to serve.py; running with what is installed")
        return
    have = STAMP.read_text(encoding="utf-8") if STAMP.exists() else ""
    if want == have:
        return
    log.info("requirements.txt changed; installing the pins")
    if run(sys.executable, "-m", "pip", "install", "--quiet", "-r", str(requirements), timeout=900.0):
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(want, encoding="utf-8")
    else:
        # No stamp written, so the next cycle tries again -- which is right: an install
        # that failed on a dropped connection should not be remembered as done.
        log.warning("could not install the pins; running with what is installed")


def somebody_else_is_polling(token: str) -> bool:
    """Whether another instance holds this token's poll right now.

    True only on a real 409 from Telegram. Every other outcome -- 200, a 401, a broken
    network, a timeout -- is False, which starts the bot: the alternative is a machine
    that stays silent all day because one probe could not reach the internet, and a bot
    that starts into a network that is down just retries, which python-telegram-bot
    already does properly.
    """
    url = f"https://api.telegram.org/bot{token}/getUpdates?timeout={PROBE_TIMEOUT}&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=PROBE_DEADLINE):
            return False
    except urllib.error.HTTPError as error:
        if error.code == 409:
            return True
        log.warning("the probe got HTTP %s; starting anyway", error.code)
        return False
    except (urllib.error.URLError, OSError) as error:
        log.warning("could not ask Telegram who is polling (%s); starting anyway", error)
        return False


def run_bot(env: dict[str, str]) -> int:
    """Run bot.py to completion, streaming its output into this log. Returns its code.

    Never `--take-over`: this machine is the fallback host, not a contender. If the
    owner starts his laptop, the bot here yields on its own (README.md §4.9), this
    returns, and the loop waits for him to finish rather than taking the group's bot
    back off him.
    """
    log.info("starting bot.py")
    try:
        child = subprocess.Popen(
            [sys.executable, "bot.py"],
            cwd=HERE,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as error:
        log.error("could not start bot.py: %s", error)
        return -1
    # The child's own lines, already formatted by its logging; re-formatting them here
    # would stamp them twice. Reading to EOF is also how this waits for the exit.
    assert child.stdout is not None
    for line in child.stdout:
        log.info("bot | %s", line.rstrip())
    return child.wait()


def serve() -> None:
    """The supervisor loop. Runs until the window it was started in is closed."""
    configure_logging()
    env = child_environment()
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        # Fail loudly and stop: an endless loop of "no token" would fill the log and
        # look exactly like a bot that is running.
        sys.exit(
            "TELEGRAM_BOT_TOKEN is not set and .env does not carry it.\n"
            "Run run-bot.cmd (Windows) or run-bot.command (macOS) once to set this "
            "machine up, then start serve.py again. See docs/server.md."
        )
    log.info("supervisor up; the bot will be restarted whenever it stops")
    delay = RESTART_DELAY
    while True:
        update_from_git()
        install_requirements_if_changed()
        # Re-read after the pull: the owner can change .env on the machine, and a new
        # TELEGRAM_OWNER_ID must not need the supervisor restarted to take effect.
        env = child_environment()
        if somebody_else_is_polling(token):
            log.info(
                "somebody else is polling this token (the owner's laptop, presumably); "
                "waiting %.0f s and staying out of the way",
                BUSY_WAIT,
            )
            time.sleep(BUSY_WAIT)
            continue
        started = time.monotonic()
        code = run_bot(env)
        lived = time.monotonic() - started
        if lived >= HEALTHY_RUN:
            delay = RESTART_DELAY
        log.warning("bot.py exited %d after %.0f s; restarting in %.0f s", code, lived, delay)
        time.sleep(delay)
        delay = min(delay * 2, RESTART_DELAY_MAX)


if __name__ == "__main__":
    serve()
