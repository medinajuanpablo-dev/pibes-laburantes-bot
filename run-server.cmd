@echo off
rem
rem The always-on host's launcher for Windows: the old machine that hosts the bot
rem when nobody is around. Double-click it once, or point Task Scheduler at it --
rem docs/server.md has the two settings that make it survive a power cut.
rem
rem Everything this file could get wrong lives in serve.py instead, and that is the
rem whole design: this .cmd cannot be tested from the owner's Mac (same warning as
rem run-bot.cmd), so it is kept to a path, a check and a loop, while the supervisor
rem logic sits in Python where the self-check can drive it.
rem
rem It does NOT set the machine up. run-bot.cmd is what installs Python's venv, the
rem dependencies and the token; this file expects that to have happened once, and
rem says so rather than asking a question nobody is there to answer.
rem
rem NOT TESTED ON WINDOWS, like run-bot.cmd. Written on a Mac.
rem
rem The Spanish below carries no accents on purpose: cmd.exe reads this file in the
rem console's OEM codepage and UTF-8 accents come out as mojibake.

setlocal
title the-bot server
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" goto :setupfirst
if not exist ".env" goto :setupfirst

echo the-bot -- servidor
echo -------------------
echo.
echo Este es el modo servidor: el bot se reinicia solo cuando se cae, y vuelve
echo cuando se corta el wifi. Deja esta ventana abierta.
echo Para pararlo del todo: cerra la ventana.
echo El log queda en server.log, al lado de este archivo.
echo.

rem The outer loop is here for one failure only: serve.py itself dying. It supervises
rem the bot, so a crash inside the bot never reaches this line -- but a killed
rem interpreter, an out-of-memory, or a Windows update that closes the process would,
rem and on a machine nobody visits that has to come back too.
rem
rem `ping` and not `timeout`: timeout.exe fails with "input redirection is not
rem supported" whenever the script runs without a real console, which is exactly what
rem Task Scheduler gives it. ping -n 11 waits ten seconds anywhere.
:loop
"%VENV_PY%" serve.py
echo.
echo El supervisor se cayo. Lo vuelvo a levantar en 10 segundos.
ping -n 11 127.0.0.1 >nul
goto :loop

:setupfirst
echo the-bot -- servidor
echo -------------------
echo.
echo Falta preparar esta computadora: no encuentro el entorno de Python o el token.
echo Abri run-bot.cmd una vez, dejalo llegar hasta que el bot arranque, cerralo,
echo y despues abri este archivo.
echo.
pause
exit /b 1
