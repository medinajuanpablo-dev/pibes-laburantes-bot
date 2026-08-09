# Shipping a change, and setting up a new host

Load when: shipping a change to the friends, bumping a pin, yt-dlp has rotted, or a new person is
taking a turn hosting. Ignore when: working on `bot.py` itself — that is `README.md` and `AGENTS.md`.

Audience: the owner. English, like the rest of the developer docs. The friend-facing copy is
`EMPEZAR-ACA.md` and it is Spanish on purpose.

---

## The whole update mechanism

Each launcher runs `git pull --ff-only` before anything else. So:

```sh
git commit -m "…" && git push
```

That is the entire release process. Every friend gets the change the next time they double-click.
Nothing to re-send, no version to track, nobody to chase.

Three outcomes, and the launcher says which one happened, in Spanish:

| It printed | It means |
|---|---|
| `Ya tenías la última versión.` | the pull succeeded and `HEAD` did not move |
| `Listo, lo actualicé a la última versión.` | the pull fast-forwarded |
| `No pude buscar actualizaciones; sigo con la versión que ya tenías.` | no network, a remote asking for credentials, a local edit in the way, or anything else `git pull --ff-only` refuses |
| `Esta copia no se puede actualizar sola.` | there is no `.git` — somebody downloaded a zip. Tell them to clone. |

The third case is deliberately not fatal: a friend must always be able to run the copy they already
have. Credential prompts are disabled (`GIT_TERMINAL_PROMPT=0`, ssh `BatchMode=yes`) so a remote
that wants a password fails fast instead of hanging in a window nobody is reading, and
`http.lowSpeedLimit`/`lowSpeedTime` bound a stalled fetch — there is no `timeout(1)` on macOS.

**The repository has to stay public.** Verified 2026-08-07: `git-upload-pack` on
`github.com/medinajuanpablo-dev/pibes-laburantes-bot` answers **200 anonymously**, so a friend
clones with no GitHub account and no credentials. Make it private and every launcher silently falls
into "no pude buscar actualizaciones" and freezes at whatever it had.

## Bumping a pinned dependency

```sh
.venv/bin/pip install -U yt-dlp          # try it locally first
.venv/bin/python bot.py --self-check     # six real downloads; this is the test that matters
# edit requirements.txt to the version you just proved
git commit -am "Bump yt-dlp to …" && git push
```

The launchers reinstall when `requirements.txt` changes, not only when the venv is missing — macOS
compares a sha256 stamp in `.venv/.requirements-sha256`, Windows compares the file itself against
`.venv\requirements-installed.txt`. So a pushed bump applies on the next double-click.

**The launchers never upgrade anything on their own.** They install exactly what the file pins. That
is the point of pinning: an `install -U` behind everyone's back would mean two friends running
different code with no way to tell. When extraction rots — the actual failure mode of this project,
`README.md` §5 — the owner bumps the pin, proves it with the self-check, and pushes.

## Setting up a new host

1. They clone. `EMPEZAR-ACA.md` has the copy-pasteable command; it is the same one whether they are
   on macOS or Windows.
2. The owner sends the token **privately, never in the group**. The launcher writes it to `.env`
   (mode 600 on macOS) and never asks again. It is not in git, not in either script, and not in the
   clone they just made.
3. They double-click `run-bot.command` (macOS) or `run-bot.cmd` (Windows).

Insist on the clone. A zip is worse in two ways that both look like the bot being broken: it cannot
update itself, and macOS refuses to run a `.command` that arrived quarantined (verified: it is
rejected with `userCanceledErr` and never runs, while the cloned file runs on a double-click).

## Windows: what nobody has verified

`run-bot.cmd` was written on a Mac. It has never run on Windows. It is checked only for what is
checkable from macOS: ASCII-only text, every `goto` has a label, no `if cond a & b` (which chains
unconditionally), no unescaped `&` inside a `for /f`.

Watch the first Windows friend do it, and specifically watch for:

- **The Python check.** `py -3` is tried first. If the machine has no Python at all, plain `python`
  is an App Execution Alias that opens the Microsoft Store — the script should still print its
  sentence, but the Store window appearing will look like a hang.
- **Whether the accents were the right call.** The Spanish there is written without accents or `ñ`
  because cmd.exe reads a `.cmd` in the console's OEM codepage. If it turns out the console renders
  UTF-8 fine, the text can be improved.
- **`fc /b` against a missing file** — the first-run path deletes the stamp and expects `fc` to
  report a difference (errorlevel ≥ 1), not to fail in some other way.
- **`certutil`/`curl` availability.** curl ships with Windows 10 1803 and later; on anything older
  the script skips the one-at-a-time question instead of failing, which means that friend can take
  the bot from somebody without being asked.
- **`winget`.** After `winget install Gyan.FFmpeg`, `PATH` is not refreshed in the running console,
  so the script tells them to reopen it. Confirm that is what actually happens.
- **`.env` has no `chmod 600` equivalent.** It is a plain file readable by that user's other
  programs. Upgrade path if it ever matters: `icacls`.

Until somebody watches all of that, describe the Windows launcher as untested, in those words.
