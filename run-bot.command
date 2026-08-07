#!/bin/bash
#
# The double-click launcher for macOS. Finder hands .command files to Terminal
# (UTI com.apple.terminal.shell-script, verified on macOS 15.1.1), so this file is
# the whole product for the friends who take turns hosting the bot: every line it
# prints is Spanish, and every failure ends in one sentence saying what to do next.
#
# Everything macOS-specific lives here. bot.py stays OS-agnostic -- see AGENTS.md.
# Written for bash 3.2, the system bash on macOS: no mapfile, no ${x,,}, no arrays.

set -u

PYTHON_URL="https://www.python.org/downloads/macos/"
HOMEBREW_URL="https://brew.sh"

say() { printf '%s\n' "$*"; }

die() {
    printf '\n%s\n\n' "$*"
    printf 'Cerrá esta ventana cuando termines de leer.\n'
    exit 1
}

# Finder can launch this from anywhere; the bot must run from its own folder.
HERE="$(cd -- "$(dirname -- "$0")" && pwd)" || die "No pude encontrar la carpeta del bot."
cd "$HERE" || die "No pude entrar a la carpeta del bot."

say "the-bot"
say "-------"

# --- 1. Updates -----------------------------------------------------------------
# The distribution channel is `git clone`, so the update channel is `git pull`: the
# owner pushes and every friend gets it the next time they double-click. Never fatal
# -- no network, no git, or local edits must not stop someone running what they have.
#
# GIT_TERMINAL_PROMPT=0 and ssh BatchMode make a remote that wants credentials fail
# fast instead of hanging on a prompt in a window nobody is reading. The low-speed
# knobs bound a stalled fetch: timeout(1) does not exist on macOS.
#
# ponytail: this never touches the pins in requirements.txt, and must not. yt-dlp is
# pinned on purpose (README.md §5) -- an `install -U` behind everyone's back is
# exactly the non-determinism the pin exists to prevent. Upgrade path when extraction
# rots: the owner bumps the pin, pushes, and this pull carries it to everyone.
say "Buscando actualizaciones..."
if [ -d .git ] && command -v git >/dev/null 2>&1; then
    before="$(git rev-parse HEAD 2>/dev/null || true)"
    if GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes" \
       git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=20 \
           pull --ff-only --quiet >/dev/null 2>&1
    then
        after="$(git rev-parse HEAD 2>/dev/null || true)"
        if [ "$before" = "$after" ]; then
            say "Ya tenías la última versión."
        else
            say "Listo, lo actualicé a la última versión."
        fi
    else
        say "No pude buscar actualizaciones; sigo con la versión que ya tenías."
    fi
else
    say "Esta copia no se puede actualizar sola. Pedile al dueño el link para bajarla con git."
fi

# --- 2. Python ------------------------------------------------------------------
# Newest first, and plain `python3` last on purpose: on a Mac with the Command Line
# Tools installed that is often 3.9, and on one without them it is a stub that pops
# Apple's installer dialog. Asking the interpreter its own version is the only honest
# test -- the name on disk says nothing.
PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
    then
        PYTHON="$candidate"
        break
    fi
done
[ -n "$PYTHON" ] || die "Falta Python 3.11 o más nuevo. Instalalo desde $PYTHON_URL y volvé a abrir este archivo."

# --- 3. ffmpeg ------------------------------------------------------------------
# Not optional: 720p on YouTube arrives as a separate video and audio stream that
# ffmpeg merges. We only ever call a package manager the person already has -- this
# script never downloads or runs a binary from anywhere.
if ! command -v ffmpeg >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        say ""
        say "Falta ffmpeg, que es lo que arma los videos."
        printf '¿Lo instalo ahora con Homebrew? Tarda unos minutos. [s/n] '
        read -r answer
        case "$answer" in
            s|S|si|Si|SI|y|Y|yes) brew install ffmpeg ;;
            *) die "Sin ffmpeg el bot no puede armar los videos. Cuando quieras, abrí la Terminal y escribí: brew install ffmpeg" ;;
        esac
        command -v ffmpeg >/dev/null 2>&1 || die "La instalación de ffmpeg no terminó bien. Probá de nuevo, o pedile una mano al dueño."
    else
        die "Falta ffmpeg, que es lo que arma los videos. Instalá Homebrew desde $HOMEBREW_URL y después abrí la Terminal y escribí: brew install ffmpeg"
    fi
fi

# --- 4. Virtualenv and dependencies ---------------------------------------------
# Reinstall when requirements.txt changed, not only when .venv is missing: otherwise
# a pin the owner bumped and pushed arrives in the pull and silently does not apply.
# The stamp lives inside .venv, which is gitignored, so it never travels.
VENV_PY=".venv/bin/python"
STAMP=".venv/.requirements-sha256"
want="$(shasum -a 256 requirements.txt | awk '{print $1}')"
have=""
[ -f "$STAMP" ] && have="$(cat "$STAMP")"

if [ ! -x "$VENV_PY" ]; then
    say ""
    say "Primera vez acá: estoy preparando el bot. Tarda un minuto, no cierres la ventana."
    "$PYTHON" -m venv .venv || die "No pude preparar el entorno de Python. Reinstalá Python desde $PYTHON_URL y volvé a abrir este archivo."
    have=""
fi

if [ "$want" != "$have" ]; then
    say "Instalando lo que el bot necesita. Puede tardar un minuto."
    "$VENV_PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1
    "$VENV_PY" -m pip install --quiet -r requirements.txt ||
        die "No pude instalar lo que el bot necesita. Fijate que tengas internet y volvé a abrir este archivo."
    printf '%s\n' "$want" > "$STAMP"
fi

# --- 5. Token -------------------------------------------------------------------
# The token is never in this script and never in git: the package has to be safe to
# pass around on its own, with the token sent separately. It is echoed as it is typed
# on purpose -- a friend pasting a 46-character string needs to see that it landed --
# and it is written with umask 077 so it is never briefly readable by anyone else.
ask_token() {
    say ""
    say "Necesito el token del bot: pedíselo al dueño, pegalo acá y apretá Enter."
    printf 'Token: '
    read -r token
    token="$(printf '%s' "$token" | tr -d '[:space:]')"
    [ -n "$token" ] || die "No pegaste nada. Volvé a abrir este archivo y pegá el token."
    ( umask 077; printf 'TELEGRAM_BOT_TOKEN=%s\n' "$token" > .env )
    chmod 600 .env
    say "Guardado. No se lo pases a nadie: es la llave del bot."
}

[ -f .env ] || ask_token
set -a
# shellcheck disable=SC1091
. ./.env
set +a
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] || die "El archivo .env está pero no tiene el token adentro. Borralo y volvé a abrir este archivo."

# --- 6. One at a time -----------------------------------------------------------
# Telegram allows exactly one poller per token: a second one gets HTTP 409,
# "Conflict: terminated by other getUpdates request". Asking is the whole point of
# this step -- without it the friend just sees a bot that answers nothing.
#
# The cost, measured and accepted: this probe momentarily steals the poll from
# whoever is running, so their window logs one line about another instance and
# recovers. There is no side-effect-free way to ask Telegram "is anybody polling?".
# A lock file is not the answer -- it would live on the wrong machine and go stale.
# bot.py tolerates this blip on purpose (see the conflict handling there).
say ""
say "Fijándome si alguien más lo tiene prendido..."
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates?timeout=0&limit=1")"

case "$code" in
    200)
        say "Nadie más lo tiene. Arrancamos."
        ;;
    409)
        say ""
        say "Justo ahora lo tiene prendido otra persona, y Telegram deja UNA SOLA a la vez."
        printf '¿Se lo saco y lo prendo yo? [s/n] '
        read -r answer
        case "$answer" in
            s|S|si|Si|SI|y|Y|yes) say "Dale, se lo saco." ;;
            *) say "Perfecto, no toco nada. Cerrá esta ventana."; exit 0 ;;
        esac
        ;;
    401)
        say ""
        say "Telegram rechazó ese token. Pedile el bueno al dueño."
        ask_token
        set -a
        # shellcheck disable=SC1091
        . ./.env
        set +a
        ;;
    *)
        say "No pude preguntarle a Telegram (¿andará bien tu internet?). Igual lo intento."
        ;;
esac

# --- 7. Run ---------------------------------------------------------------------
say ""
say "El bot está prendido. Dejá esta ventana abierta."
say "Para apagarlo: apretá Control-C, o cerrá la ventana."
say "Ojo: cuando lo apagues, el grupo se queda sin bot hasta que alguien lo prenda."
say ""
exec "$VENV_PY" bot.py
