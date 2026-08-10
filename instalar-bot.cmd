@echo off
rem
rem The Windows bootstrap: the file a friend downloads and double-clicks when they
rem do not have git. It fetches the repository as a source tarball, unpacks it into
rem the same folder a clone would land in, and hands off to run-bot.cmd, which owns
rem everything else -- Python, ffmpeg, the venv, the token and the take-over
rem question. Nothing here duplicates any of that.
rem
rem WHY THIS EXISTS ONLY ON WINDOWS: a .cmd needs no exec bit, so a downloaded one
rem runs. A downloaded .command on macOS needs the exec bit *and* no quarantine and
rem a download has neither -- measured, README.md 2.2. That asymmetry is the whole
rem reason this file is Windows-only. Do not write a macOS twin of it.
rem
rem NOT TESTED ON WINDOWS. Written on a Mac; there is no Windows in this project.
rem What was verified, and how, is in docs/updating.md. Read it before handing this
rem to the first Windows friend.
rem
rem Only what Windows already ships, and never an install of anything:
rem   curl.exe and tar.exe, both in System32 since Windows 10 1803 (build 17063).
rem Called by full path on purpose. `tar` on PATH may be Git for Windows' GNU tar,
rem which reads "C:\..." after -f as a remote host and fails; System32's tar is
rem bsdtar, which does not. The same full-path rule removes any question about what
rem a broken or hijacked PATH resolves to. PowerShell would also work and is not
rem used: tar is required either way and curl shipped in the same Windows build, so
rem a second downloader adds a mechanism without widening the set of machines this
rem runs on -- and it would add PowerShell's execution policy and TLS defaults to
rem the list of things nobody here can test.
rem
rem The Spanish below is written without accents or enye, for the same reason as
rem run-bot.cmd: cmd.exe reads a .cmd in the console's OEM codepage and UTF-8
rem accents come out as mojibake there.

setlocal
title the-bot - instalador

rem No `cd /d "%~dp0"` on purpose, unlike run-bot.cmd: every path below is
rem absolute, so the only thing it could still do is print cmd.exe's English
rem complaint about UNC paths for a friend who opened this from a share.

rem GitHub serves this with no git, no account and no credentials: verified
rem 2026-08-10, HTTP/2 200 with `content-disposition: attachment` and
rem `content-type: application/x-gzip`. It is the tree of `main`, so it carries the
rem tracked files and nothing else.
rem The repository is named in three places now -- here, bot.py's CLONE_URL and
rem EMPEZAR-ACA.md. This one cannot drift silently: bot.py's self-check builds this
rem URL from CLONE_URL and asserts the string is in this file.
set "ARCHIVE_URL=https://codeload.github.com/medinajuanpablo-dev/pibes-laburantes-bot/tar.gz/refs/heads/main"

rem The folder a `git clone` into ~/Documents makes, so the two paths land in the
rem same place and nobody ends up with two copies of the bot.
rem ponytail: %USERPROFILE%\Documents is where Git Bash's ~/Documents points too,
rem which is what makes them agree. On a machine whose Documents was redirected
rem into OneDrive this creates a plain local folder next to the redirected one --
rem the same thing the clone command already does. Ceiling: a friend may not find
rem the folder in Explorer's "Documents". Upgrade path if that bites, for both
rem paths at once: read the real Shell Folders path out of the registry.
set "TARGET=%USERPROFILE%\Documents\pibes-laburantes-bot"

set "ARCHIVE=%TEMP%\the-bot-main.tar.gz"
set "CURL=%SystemRoot%\System32\curl.exe"
set "TAR=%SystemRoot%\System32\tar.exe"

rem What run-bot.cmd reads to know this copy has an updater that is not git.
set "STAMP=.tarball-install"

echo the-bot
echo -------
echo Carpeta: %TARGET%
echo.

rem --- 1. What this needs, and what happens when it is missing -------------------
if not exist "%CURL%" goto :notools
if not exist "%TAR%" goto :notools

rem --- 2. A clone updates itself, so never unpack over one -----------------------
rem Overwriting a clone's files with the tarball would leave a dirty working tree
rem that `git pull --ff-only` then refuses, which would break the very update
rem channel that copy already has. Two update mechanisms exist now (git pull for a
rem clone, this re-fetch for a download) and this is the line that keeps them from
rem fighting: whoever has .git keeps git.
if not exist "%TARGET%\.git" goto :download
echo Esa carpeta la bajaste con git, asi que no la toco: se actualiza sola.
goto :handoff

rem --- 3. Download --------------------------------------------------------------
rem Every run downloads: the second double-click is the update path. -f so an HTTP
rem error is an error and not a saved error page, and both timeouts so a stalled
rem transfer cannot hang a window nobody is reading. -S and not a plain -s: curl
rem keeps its own error line, in English, above the Spanish one. Deliberate while
rem nobody has ever watched this run on Windows -- it is the only diagnosis the
rem first friend can read out loud.
:download
echo Bajando la ultima version...
del "%ARCHIVE%" >nul 2>&1
"%CURL%" -fsS -L --connect-timeout 20 --max-time 300 -o "%ARCHIVE%" "%ARCHIVE_URL%"
if errorlevel 1 goto :nodownload
if not exist "%ARCHIVE%" goto :nodownload

rem Read the archive before letting it near the friend's folder: a truncated
rem download and an error page in place of an archive both fail here, where nothing
rem has been touched yet, instead of half-unpacking over a working copy. Measured
rem with bsdtar 3.5.3: exit 1 on a truncated archive and on an HTML page, 0 on the
rem real one.
"%TAR%" -tf "%ARCHIVE%" >nul 2>&1
if errorlevel 1 goto :badarchive

rem --- 4. Unpack ----------------------------------------------------------------
rem WHAT SURVIVES A RE-RUN, AND WHY IT IS NOT LUCK. tar extracts the members of the
rem archive and nothing else: it overwrites a file that is in the archive and never
rem removes or touches a file that is not. The archive is the *tracked* tree of
rem main, so the friend's own files are absent from it by construction --
rem .env (the token) and .venv/ are both in .gitignore, and so are rejected.jsonl,
rem insults.jsonl and __pycache__. There is no .gitattributes, so no export-ignore
rem can change what is in there either. No member's path can therefore be .env or
rem .venv\..., which is why the overwrite cannot reach them. Verified on 2026-08-10
rem against the real archive with the same implementation Windows ships (bsdtar /
rem libarchive): extracted twice over a folder holding .env, .venv\Scripts and
rem rejected.jsonl, and all three came out byte-identical while bot.py was replaced.
rem Losing .env would cost the friend the token, losing .venv a minute.
rem
rem --strip-components=1 drops the single "pibes-laburantes-bot-main/" prefix
rem codeload puts on everything, so the files land in %TARGET% and not in a folder
rem inside it.
rem
rem --exclude keeps this installer out of the unpack, which is deliberate twice
rem over: cmd.exe reads a .cmd incrementally while it runs it, so overwriting the
rem file being executed is undefined behaviour, and the folder is left with exactly
rem one .cmd in it -- run-bot.cmd, the one the friend is told to double-click from
rem then on.
rem ponytail: the consequence is that this installer never updates itself. It is
rem the downloaded copy that keeps working, so keep this file small and its
rem behaviour stable; anything that needs to change on the friend's machine belongs
rem in run-bot.cmd, which the unpack does replace. Upgrade path if it ever has to
rem change: the link is the same link, so re-downloading it is the whole fix.
if not exist "%TARGET%" mkdir "%TARGET%"
if not exist "%TARGET%" goto :nofolder
"%TAR%" -x -f "%ARCHIVE%" --exclude "*/instalar-bot.cmd" --strip-components=1 -C "%TARGET%"
if errorlevel 1 goto :nounpack
del "%ARCHIVE%" >nul 2>&1

rem The one thing this leaves behind that git would not: it is what stops
rem run-bot.cmd from telling a friend with a working updater that this copy cannot
rem update itself. Rewritten on every run, and it holds nothing private.
> "%TARGET%\%STAMP%" echo %ARCHIVE_URL%
>>"%TARGET%\%STAMP%" echo %DATE% %TIME%
echo Listo, ya tengo la ultima version.
rem The payoff of the whole file, said once where it cannot be missed: this is
rem also the everyday file for this copy. run-bot.cmd in that folder still works
rem and would simply never update -- which is what run-bot.cmd now says out loud
rem when it finds the stamp instead of a .git.
echo De ahora en mas abri este mismo archivo: actualizo el bot y lo prendo.

rem --- 5. Hand off --------------------------------------------------------------
rem run-bot.cmd does its own `cd /d "%~dp0"`, so calling it by path is enough. It
rem ends in `exit /b`, which comes back here; `exit /b` without a number keeps its
rem exit code.
:handoff
if not exist "%TARGET%\run-bot.cmd" goto :nolauncher
echo.
call "%TARGET%\run-bot.cmd"
exit /b

rem --- One Spanish line per failure ----------------------------------------------
:notools
echo A esta compu le falta algo que trae Windows 10 desde 2018 y no puedo bajar el bot; pedile al dueno el otro camino, el que usa git.
goto :fail
:nodownload
echo No pude bajar el bot. Fijate que tengas internet y volve a abrir este archivo.
goto :fail
:badarchive
echo Lo que baje llego cortado. Volve a abrir este archivo y probamos de nuevo.
goto :fail
:nofolder
echo No pude crear la carpeta %TARGET%. Fijate que tengas lugar en el disco.
goto :fail
:nounpack
echo No pude desempaquetar el bot en %TARGET%. Volve a abrir este archivo y probamos de nuevo.
goto :fail
:nolauncher
echo Baje el bot pero falta run-bot.cmd, que es lo que lo prende. Avisale al dueno.
goto :fail

:fail
echo.
pause
exit /b 1
