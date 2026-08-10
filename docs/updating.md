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

### There are two update mechanisms now, and which one a copy has depends on how it arrived

A Windows friend with no git downloads `instalar-bot.cmd` and double-clicks it (`README.md` §2.2).
That copy has no `.git`, so `git pull` can never reach it. Its update channel is the bootstrap
itself: **every** double-click re-fetches `codeload.github.com/…/tar.gz/refs/heads/main`, unpacks it
over the folder and only then hands off to `run-bot.cmd`.

| How the copy arrived | Its update channel | The friend's everyday file |
|---|---|---|
| `git clone` — macOS, and Windows with git | `git pull --ff-only` at every launch | `run-bot.command` / `run-bot.cmd` |
| the download — Windows, no git | the bootstrap re-fetches the tarball at every launch | `instalar-bot.cmd` |

**`git commit && git push` is still the entire release process for both.** codeload serves the tip of
`main`, so a push reaches a tarball copy on its next double-click exactly like it reaches a clone.
Verified 2026-08-10: that URL answers `HTTP/2 200` with `content-disposition: attachment` and
`content-type: application/x-gzip`, anonymously.

Three things keep the two from contradicting each other, and each one is deliberate:

- **The bootstrap refuses to unpack over a `.git`.** It says so in one Spanish line and hands
  straight to the launcher. Without that, the unpack would leave a clone's working tree dirty,
  `git pull --ff-only` would then refuse it, and the download would have destroyed the update channel
  of the one copy that already had a working one.
- **The bootstrap excludes itself from the unpack**, so a downloaded copy's folder holds exactly one
  `.cmd` — `run-bot.cmd`. The cost is that the bootstrap never updates itself; the downloaded copy is
  what keeps working, and re-downloading the same link is the whole fix if it ever has to change.
  Keep that file small and its behaviour stable, and put anything that has to change on a friend's
  machine in `run-bot.cmd`, which the unpack *does* replace.
- **The bootstrap leaves a `.tarball-install` stamp in the folder.** That is the only thing that
  tells a downloaded copy apart from a zip somebody unpacked by hand: both have no `.git`, and only
  one of them has an updater.

**Nothing about the macOS path changed, and it must not.** A downloaded `.command` cannot run at all
(`README.md` §2.2, measured), so a macOS bootstrap would be a file that cannot be double-clicked.
The asymmetry is the whole reason this exists on Windows only: a `.cmd` needs no exec bit.

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

1. They get the code. **On macOS the only way is the clone** — `EMPEZAR-ACA.md` has the
   copy-pasteable command. **On Windows there are two**, and the download is the shorter one: the
   link to `instalar-bot.cmd`, or the same clone command from Git Bash if they already have git.
2. The owner sends the token **privately, never in the group**. The launcher writes it to `.env`
   (mode 600 on macOS) and never asks again. It is not in git, not in any of the three scripts, and
   not in the copy they just made.
3. They double-click: `run-bot.command` (macOS), `instalar-bot.cmd` (Windows, the download —
   it updates and then launches), or `run-bot.cmd` (Windows, a clone).

**On macOS, insist on the clone**, and there is no macOS equivalent of the Windows download: a
downloaded `.command` needs the exec bit *and* no quarantine, and it arrives with neither (verified —
it is rejected with `userCanceledErr` and never runs, while the cloned file runs on a double-click).
A hand-unpacked zip is worse still on either platform: with no `.git` **and** no `.tarball-install`
stamp it has no updater at all, and it says so.

## Windows: what nobody has verified

**Both Windows files were written on a Mac and neither has ever run on Windows** — `run-bot.cmd` and
`instalar-bot.cmd`. They are checked only for what is checkable from macOS: ASCII-only text, CRLF
line endings, every `goto` has a label, no `if cond a & b` (which chains unconditionally), no
unescaped `&` inside a `for /f`. Say **untested**, in that word, until somebody watches them.

### The bootstrap: what was measured here, and what could not be

Measured on this Mac on 2026-08-10, and these are real:

| Claim | How |
|---|---|
| the tarball URL answers 200, anonymously, as an attachment | `curl -sSI` on `codeload…/tar.gz/refs/heads/main` |
| the archive carries the **tracked** tree only, under one `pibes-laburantes-bot-main/` prefix | listed all 29 members: no `.env`, no `.venv`, no `rejected.jsonl`, no `.gitattributes` to change that |
| the unpack cannot destroy `.env` or `.venv` | ran the bootstrap's exact `tar` line **twice** over a folder holding `.env`, `.venv\Scripts\python.exe`, `.venv\requirements-installed.txt` and `rejected.jsonl`: all four byte-identical afterwards, `bot.py` replaced, exit 0 both times |
| `--exclude` really keeps the installer out | the excluded file kept its old contents through the unpack; three pattern spellings all matched |
| a truncated download or an error page is caught before anything is touched | `tar -tf` exits 1 on both and 0 on the real archive |
| the `curl` line fails loudly instead of saving an error page | same flags against a bad branch: exit 22, one English line, **no file written** |

The implementation was `bsdtar 3.5.3 / libarchive 3.5.3` — the same implementation Windows ships as
`System32\tar.exe`, which is why those results carry over as well as anything can without a Windows
machine. **They are not a Windows run.**

**Nothing about the script's execution is verified.** In particular, watch for:

- **Whether cmd.exe runs the file at all.** Parsing, the `> "file" echo …` redirection idiom, and
  `%DATE% %TIME%` in the stamp are all read from the platform's documentation and from `run-bot.cmd`,
  which is itself untested.
- **The download.** A browser gets `content-type: text/plain` with no `content-disposition` from
  raw.githubusercontent.com (measured), so Chrome and Edge will very likely **render the file as text
  instead of downloading it** and the friend has to save it (Ctrl+S). That is the single most likely
  place a friend gets stuck, and the reply says it in Spanish. If it turns out to be worse than one
  extra step, the fix that does force a download is a release asset
  (`…/releases/latest/download/instalar-bot.cmd`), at the cost of an upload on every change.
- **Whether Windows lets a downloaded `.cmd` run**, and what the warning says. Expected: the
  mark-of-the-web dialog, *Más información → Ejecutar de todas formas*. A `.cmd` needs no exec bit,
  which is the entire reason this path exists, but nobody has seen the dialog.
- **`System32\curl.exe` and `System32\tar.exe` existing on that machine.** Both shipped in Windows 10
  build 17063 / 1803, so anything current has them; Windows 8.1 and older have neither, and the
  script prints one Spanish line pointing at the git path instead of assuming. Both are called by
  **full path** on purpose: a bare `tar` may resolve to Git for Windows' GNU tar, which reads
  `C:\…` after `-f` as a remote host and fails.
- **`%USERPROFILE%\Documents`** being the Documents the friend actually sees. If Documents was
  redirected into OneDrive, this makes a plain local folder beside it — which is exactly what the
  `git clone` command already does, so the two paths at least agree.
- **Windows' `tar.exe` accepting `-C "C:\…"`, `--strip-components` and `--exclude`.** Same codebase
  as the one measured here, different build, never run there.
- **The hand-off.** `call "…\run-bot.cmd"` and coming back to `exit /b`.

### The launcher

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

Until somebody watches all of that, describe **both Windows files** as untested, in that word.
