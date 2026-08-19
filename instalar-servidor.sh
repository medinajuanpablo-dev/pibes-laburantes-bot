#!/usr/bin/env bash
#
# The always-on host's installer, for a headless Debian (or Ubuntu) box. Run it once:
#
#     bash instalar-servidor.sh
#
# It installs what apt owns, clones or updates the repository, builds the venv from the
# pins, asks for the token, and installs a systemd unit that keeps serve.py running
# across reboots. Re-runnable: everything it does, it checks first.
#
# This is the Linux counterpart of run-server.cmd, and it can do what that file cannot
# -- systemd replaces its outer "restart the supervisor" loop, so this installs the unit
# and gets out of the way. serve.py itself is unchanged and OS-agnostic; that is what
# made this file 100 lines instead of a rewrite. Setup context: docs/server-vm.md.
#
# NOT RUN BEFORE SHIPPING. Written on a Mac with no Linux and no container runtime
# available to try it in, so `set -euo pipefail` is doing real work here: every step
# stops the script the moment it fails, and nothing later runs on a broken assumption.
# Read what it prints.
#
# Spanish for what the person reads, English for the code and comments, like the rest
# of the project.
set -euo pipefail

REPO_URL="https://github.com/medinajuanpablo-dev/pibes-laburantes-bot.git"
REPO_DIR="$HOME/pibes-laburantes-bot"
SERVICE="the-bot"
PYTHON_MIN="3.11"

say() { printf '%s\n' "$*"; }
die() { printf '\n%s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "No lo corras como root: la instalación vive en el home de tu usuario. Corrélo como el usuario que va a hostear el bot (usa sudo solo donde hace falta)."
command -v sudo >/dev/null 2>&1 || die "Falta sudo. Instalalo (apt install sudo) y agregá tu usuario al grupo sudo."

say "the-bot -- instalador del servidor"
say "=================================="
say ""

# --- 1. What apt owns -----------------------------------------------------------------
# ffmpeg is not optional: 720p on YouTube arrives as separate video and audio streams
# and ffmpeg is what merges them. python3-venv is separate from python3 on Debian, and
# forgetting it is the classic "python3 -m venv does nothing" hour.
#
# unattended-upgrades is here because of WHERE this machine lives: nobody logs into it
# for weeks, and a box on the internet that never gets security patches is the failure
# nobody notices until it matters.
say "Instalando lo que Debian trae (git, python3, ffmpeg, parches automáticos)..."
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-venv ffmpeg unattended-upgrades
# Installing the package is not enabling it on Debian -- it ships the policy and nothing
# that runs it. This is what a cloud image does for you and a netinst does not.
sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'PERIODIC'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
PERIODIC

# Ask the interpreter its own version: the name on disk says nothing. Same rule as both
# launchers, and the same floor -- 3.11 -- because yt-dlp and python-telegram-bot both
# require >= 3.10 and this file should not be the place that discovers that.
python3 - <<'PY' || die "El python3 de esta máquina es más viejo que $PYTHON_MIN. En Debian 12 o 13 no debería pasar: fijate qué distro es."
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
say "  python3 $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'), ffmpeg $(ffmpeg -version 2>/dev/null | sed -n '1s/.*version \([^ ]*\).*/\1/p')"
say ""

# --- 2. The repository ----------------------------------------------------------------
# A clone and not a tarball, on purpose: serve.py updates this machine with `git pull`,
# which is the only way a fix reaches a computer nobody visits. The repository is public
# (verified 2026-08-10, README.md §2.2), so no credentials are involved -- and that is
# also why the pull keeps working unattended, forever, with nothing stored here.
if [ -d "$REPO_DIR/.git" ]; then
    say "Ya está clonado en $REPO_DIR; lo actualizo."
    git -C "$REPO_DIR" pull --ff-only
else
    say "Clonando en $REPO_DIR"
    git clone --quiet "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"
say ""

# --- 3. The venv and the pins ---------------------------------------------------------
[ -x .venv/bin/python ] || python3 -m venv .venv
say "Instalando las dependencias pinneadas. Puede tardar un par de minutos."
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

# serve.py reinstalls whenever requirements.txt differs from this stamp. Written here so
# the first supervised cycle does not repeat the install that just happened; the format
# is the file's own text, which is serve.py's contract (see STAMP there).
cp requirements.txt .venv/.requirements-served
say ""

# --- 4. The token, and the owner ------------------------------------------------------
# Never in the repository, never in this script: the same contract as every other host.
# Written with umask 077 so it is not briefly readable by anybody else on the machine.
if [ -f .env ]; then
    say "Ya hay un .env; no lo toco."
else
    say "Necesito el token del bot (te lo da @BotFather, o ya lo tenés del otro host)."
    printf 'Token: '
    read -r token
    token="$(printf '%s' "$token" | tr -d '[:space:]')"
    [ -n "$token" ] || die "No pegaste nada. Volvé a correr este archivo."
    say ""
    say "Y tu id de usuario de Telegram, para que /apagar y /prender funcionen solo para vos."
    say "Si no lo tenés a mano, dejalo vacío y agregalo después: sin él esos dos comandos no existen."
    printf 'TELEGRAM_OWNER_ID (Enter para saltearlo): '
    read -r owner
    owner="$(printf '%s' "$owner" | tr -d '[:space:]')"
    (
        umask 077
        printf 'TELEGRAM_BOT_TOKEN=%s\n' "$token" > .env
        if [ -n "$owner" ]; then
            printf 'TELEGRAM_OWNER_ID=%s\n' "$owner" >> .env
        fi
    )
    chmod 600 .env
    say "Guardado en $REPO_DIR/.env (solo tu usuario lo puede leer)."
fi
say ""

# --- 5. systemd ------------------------------------------------------------------------
# A system unit rather than a user one: a user unit needs `loginctl enable-linger` to
# survive a logout and this machine has nobody logging in at all. It runs AS the user
# who ran this script, so the venv, the repo and .env keep their ownership and the bot
# never runs as root.
#
# Restart=always is the only thing systemd does that serve.py cannot do for itself: it
# supervises the SUPERVISOR. Everything else -- the bot's own restarts, the git pull, the
# yielding to another host -- stays inside serve.py, where it is tested.
#
# network-online.target, not network.target: the first probe would otherwise run before
# there is a route, log its "could not ask Telegram" line, and start the bot into a
# network that is not up yet. It recovers either way; this just keeps the log honest.
say "Instalando el servicio systemd '$SERVICE'..."
sudo tee "/etc/systemd/system/$SERVICE.service" >/dev/null <<UNIT
[Unit]
Description=the-bot supervisor (serve.py)
Documentation=file://$REPO_DIR/docs/server.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/.venv/bin/python $REPO_DIR/serve.py
Restart=always
RestartSec=10
# serve.py already writes its own rotating server.log next to itself; the journal keeps
# the same lines for `journalctl -u $SERVICE`, which is where a start-up failure that
# never reaches the log file shows up.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE"
say ""
say "=========================================================="
say "Listo. El bot ya está corriendo y va a volver solo después de cada reinicio."
say ""
say "  ver estado:        systemctl status $SERVICE"
say "  ver el log vivo:   tail -f $REPO_DIR/server.log"
say "  o desde systemd:   journalctl -u $SERVICE -f"
say "  pararlo del todo:  sudo systemctl stop $SERVICE"
say ""
say "Probalo antes de dar por hecho que anda: mandá un link al grupo, y después"
say "probá /apagar y /prender desde tu Telegram."
say ""
say "El test completo (seis descargas reales) es:"
say "  cd $REPO_DIR && .venv/bin/python bot.py --self-check"
say "=========================================================="
