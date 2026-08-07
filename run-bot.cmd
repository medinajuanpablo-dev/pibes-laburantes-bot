@echo off
rem
rem The double-click launcher for Windows. Same responsibilities as
rem run-bot.command, written in the idiom of the platform it runs on rather than
rem shared with it: two small scripts a person can read beat one clever one.
rem
rem .cmd and not .ps1: a PowerShell script does not run when it is double-clicked
rem -- Windows opens .ps1 in an editor, and the default execution policy would
rem refuse it anyway. A .cmd runs.
rem
rem NOT TESTED ON WINDOWS. It was written on a Mac and there is no Windows here.
rem Read docs/updating.md before handing it to the first Windows friend.
rem
rem The Spanish below is deliberately written without accents or enye: a .cmd is
rem read by cmd.exe in the console's OEM codepage, where UTF-8 accents come out as
rem mojibake, and `chcp 65001` has its own history of breaking batch parsing.
rem Plain ASCII always reads.

setlocal
title the-bot
cd /d "%~dp0"

set "PYTHON_URL=https://www.python.org/downloads/windows/"
set "FFMPEG_URL=https://www.gyan.dev/ffmpeg/builds/"

echo the-bot
echo -------

rem --- 1. Updates ---------------------------------------------------------------
rem Distribution is `git clone`, so the update channel is `git pull`: the owner
rem pushes and every friend gets it on their next double-click. Never fatal.
rem GIT_TERMINAL_PROMPT=0 keeps a remote that wants credentials from hanging on a
rem prompt in a window nobody is reading.
echo Buscando actualizaciones...
if not exist ".git" goto :nogit
where git >nul 2>&1 || goto :nogit
set "GIT_TERMINAL_PROMPT=0"
for /f %%H in ('git rev-parse HEAD 2^>nul') do set "BEFORE=%%H"
git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=20 pull --ff-only --quiet >nul 2>&1
if errorlevel 1 goto :nopull
for /f %%H in ('git rev-parse HEAD 2^>nul') do set "AFTER=%%H"
if "%BEFORE%"=="%AFTER%" (echo Ya tenias la ultima version.) else (echo Listo, lo actualice a la ultima version.)
goto :python
:nopull
echo No pude buscar actualizaciones; sigo con la version que ya tenias.
goto :python
:nogit
echo Esta copia no se puede actualizar sola. Pedile al dueno el link para bajarla con git.

rem --- 2. Python ----------------------------------------------------------------
rem The py launcher first: plain `python` on a machine without Python is a stub
rem that opens the Microsoft Store, which looks like the script hanging.
:python
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :ffmpeg
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :ffmpeg
echo.
echo Falta Python 3.11 o mas nuevo. Instalalo desde %PYTHON_URL% y volve a abrir este archivo.
echo Cuando lo instales, marca la casilla "Add python.exe to PATH".
goto :fail

rem --- 3. ffmpeg ----------------------------------------------------------------
rem Only ever a package manager the person already has. This script never
rem downloads or runs a binary from anywhere.
:ffmpeg
where ffmpeg >nul 2>&1 && goto :venv
where winget >nul 2>&1 || goto :noffmpeg
echo.
echo Falta ffmpeg, que es lo que arma los videos.
set "ANSWER="
set /p "ANSWER=Lo instalo ahora con winget? Tarda unos minutos. [s/n] "
if /i not "%ANSWER%"=="s" if /i not "%ANSWER%"=="si" if /i not "%ANSWER%"=="y" goto :noffmpeg
winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
where ffmpeg >nul 2>&1 && goto :venv
echo.
echo Instale ffmpeg, pero Windows todavia no lo ve. Cerra esta ventana y volve a abrir este archivo.
goto :fail
:noffmpeg
echo.
echo Sin ffmpeg el bot no puede armar los videos. Instalalo desde %FFMPEG_URL% y volve a abrir este archivo.
goto :fail

rem --- 4. Virtualenv and dependencies -------------------------------------------
rem Reinstall when requirements.txt changed, not only when the venv is missing:
rem otherwise a pin the owner bumped and pushed arrives in the pull and silently
rem does not apply. The copy kept inside .venv is the record of what is installed,
rem and .venv is gitignored, so it never travels.
rem
rem ponytail: like the Mac launcher, this installs exactly what requirements.txt
rem pins and never upgrades yt-dlp on its own. When extraction rots the owner bumps
rem the pin and pushes; the pull above carries it to everyone.
:venv
set "VENV_PY=.venv\Scripts\python.exe"
if exist "%VENV_PY%" goto :deps
echo.
echo Primera vez aca: estoy preparando el bot. Tarda un minuto, no cierres la ventana.
%PY% -m venv .venv
if not exist "%VENV_PY%" (
    echo No pude preparar el entorno de Python. Reinstalalo desde %PYTHON_URL% y volve a abrir este archivo.
    goto :fail
)
del ".venv\requirements-installed.txt" >nul 2>&1
:deps
fc /b requirements.txt ".venv\requirements-installed.txt" >nul 2>&1
if not errorlevel 1 goto :token
echo Instalando lo que el bot necesita. Puede tardar un minuto.
"%VENV_PY%" -m pip install --quiet --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo No pude instalar lo que el bot necesita. Fijate que tengas internet y volve a abrir este archivo.
    goto :fail
)
copy /y requirements.txt ".venv\requirements-installed.txt" >nul

rem --- 5. Token -----------------------------------------------------------------
rem Never in this script and never in git: the package has to be safe to pass
rem around on its own, with the token sent separately.
rem ponytail: there is no chmod 600 here. Windows leaves .env readable by this
rem user's other programs, which the Mac launcher does not. Upgrade path if it ever
rem matters: icacls /inheritance:r /grant:r "%USERNAME%":R after writing it.
:token
if exist ".env" goto :readenv
:asktoken
echo.
echo Necesito el token del bot: pediselo al dueno, pegalo aca y apreta Enter.
set "TOKEN="
set /p "TOKEN=Token: "
if not defined TOKEN (
    echo.
    echo No pegaste nada. Volve a abrir este archivo y pega el token.
    goto :fail
)
> ".env" echo TELEGRAM_BOT_TOKEN=%TOKEN%
echo Guardado. No se lo pases a nadie: es la llave del bot.
:readenv
set "TELEGRAM_BOT_TOKEN="
for /f "usebackq tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="TELEGRAM_BOT_TOKEN" set "TELEGRAM_BOT_TOKEN=%%B"
if not defined TELEGRAM_BOT_TOKEN (
    echo.
    echo El archivo .env esta pero no tiene el token adentro. Borralo y volve a abrir este archivo.
    goto :fail
)

rem --- 6. One at a time ----------------------------------------------------------
rem Telegram allows exactly one poller per token; a second one gets HTTP 409.
rem The cost, accepted: this probe momentarily steals the poll from whoever is
rem running, so their window logs one line and recovers. bot.py tolerates that
rem blip on purpose. curl ships with Windows 10 1803 and later; older machines
rem skip the question rather than fail.
rem The URL carries no & on purpose -- escaping it inside a for /f is a classic
rem way to break a batch file, and the limit parameter buys nothing here.
echo.
where curl >nul 2>&1 || goto :run
echo Fijandome si alguien mas lo tiene prendido...
set "CODE="
for /f %%C in ('curl -s -o NUL -w "%%{http_code}" --max-time 20 "https://api.telegram.org/bot%TELEGRAM_BOT_TOKEN%/getUpdates?timeout=0"') do set "CODE=%%C"
if "%CODE%"=="200" echo Nadie mas lo tiene. Arrancamos.
if "%CODE%"=="401" goto :badtoken
if not "%CODE%"=="409" goto :run
echo.
echo Justo ahora lo tiene prendido otra persona, y Telegram deja UNA SOLA a la vez.
set "ANSWER="
set /p "ANSWER=Se lo saco y lo prendo yo? [s/n] "
if /i "%ANSWER%"=="s" goto :run
if /i "%ANSWER%"=="si" goto :run
if /i "%ANSWER%"=="y" goto :run
echo Perfecto, no toco nada. Podes cerrar esta ventana.
goto :done
:badtoken
echo.
echo Telegram rechazo ese token. Pedile el bueno al dueno.
del ".env" >nul 2>&1
goto :asktoken

rem --- 7. Run ---------------------------------------------------------------------
:run
echo.
echo El bot esta prendido. Deja esta ventana abierta.
echo Para apagarlo: apreta Control-C, o cerra la ventana.
echo Ojo: cuando lo apagues, el grupo se queda sin bot hasta que alguien lo prenda.
echo.
"%VENV_PY%" bot.py

:done
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
