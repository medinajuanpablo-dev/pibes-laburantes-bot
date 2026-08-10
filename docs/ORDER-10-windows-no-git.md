# PROMPT-ORDER 10 — Windows gets a download that needs no git

> Self-contained. Written 2026-08-10. **Everything here is unverifiable on this machine: there is no
> Windows. Say so, do not pretend otherwise, and do not weaken anything on the macOS side to make
> the Windows side neater.**

## CONTEXT

Read `AGENTS.md`, then `README.md` §2.1 (launchers, baton pass) and §2.2 (`/instalar`).

The owner's call: for **Windows**, `/instalar` should hand over a **download link** instead of a
Terminal command. His reason: it removes the extra step of installing git, and a downloaded `.cmd`
runs without much permission friction.

**The second half of that reason is right and the first half is not, as written.** Measured:

- `run-bot.cmd` needs `git` — it does `git pull`, and more importantly a downloaded launcher lands
  next to **nothing**: no `bot.py`, no `requirements.txt`, no `.git`. It would need git to clone the
  repo, so linking the existing launcher **moves** the git step rather than removing it.
- **A `.cmd` needs no exec bit**, which is the wall on macOS (`README.md` §2.2). So on Windows a
  download genuinely can run — his instinct about permissions holds. This is the one platform where
  it works, and it is why this order is Windows-only.
- **GitHub serves a source tarball with no git and no account** —
  `codeload.github.com/…/tar.gz/refs/heads/main` answers `HTTP/2 200`. That is what makes a
  git-free path possible at all.

So: a **bootstrap** that fetches the tarball, unpacks it, and hands off to the real launcher. Windows
ships PowerShell and `tar`, so nothing needs installing.

## WHY IT MATTERS

A Windows friend today has to install Git for Windows, find Git Bash, and paste a command. That is
three obstacles for someone who was handed a link. This turns it into: click the link, double-click
the file, answer one prompt.

## TERRITORY

Your own worktree, branched from current `main`. You may change:

- **one new bootstrap file** for Windows — name it so nobody confuses it with `run-bot.cmd`;
- **`run-bot.cmd`** — only what slice 3 requires;
- **`bot.py`** — only the Windows branch of `/instalar`;
- **`README.md`, `AGENTS.md`, `EMPEZAR-ACA.md`**.

**Do not touch:** `run-bot.command` or **anything about the macOS path** — it is measured, it works,
and it is not part of this. `requirements.txt`, `.gitignore`, `docs/**` beyond routing.

**The bot is live and I am hosting it**: no second instance, never `getUpdates`, go easy on YouTube.
`.env` does not exist in your worktree.

## THE WORK

### Slice 1 — the Windows bootstrap

A `.cmd` a friend downloads and double-clicks. It must:

- **use only what Windows already ships.** PowerShell for the download, `tar` for the archive (both
  present on Windows 10 1803+ and later). **Never install anything, never download a binary.** If you
  believe a needed tool is not guaranteed present, say so rather than assuming.
- fetch the tarball, unpack it into a fixed predictable folder, and **hand off to `run-bot.cmd`**.
  Do not re-implement Python, ffmpeg, venv, token or the take-over question — the launcher has all of
  it.
- **be safe to double-click twice, and on the tenth time.** The second run is the update path, so it
  overwrites the code — and it **must not destroy `.env` or `.venv`**. Losing `.env` costs the friend
  the token; losing `.venv` costs them a minute. Say in a comment which files you deliberately
  preserve and how you know the unpack cannot clobber them.
- print one Spanish line per failure, like both launchers do.

*Check:* you cannot run it. Assert what you can offline about the constructed URL and the folder
layout, and **state plainly in the report that the script itself is unexecuted.**
*Commit.*

### Slice 2 — `/instalar windows` becomes a link

Two or three lines, same budget discipline as the macOS branch:

- the link to the bootstrap;
- that Windows will ask for confirmation before running it, and that is expected;
- the token comes from the owner, separately.

**The macOS branch does not change.** The bare case now describes two different mechanisms — keep it
inside its line budget and do not let it grow back into a wall.

**The token invariance guard must still cover every string this emits**, including the new link and
any new filename. Re-run its mutations.

*Commit.*

### Slice 3 — stop `run-bot.cmd` from lying

`run-bot.cmd` prints *"Esta copia no se puede actualizar sola"* when there is no `.git`. Under the
bootstrap that sentence is **false** — the bootstrap is the updater. Make it accurate without
weakening the case it was written for (a copy that genuinely cannot update). The cheapest honest
signal is probably something the bootstrap leaves behind; your call, argued in the commit message.

*Commit.*

## DESIGN LAWS

1. **One file until it hurts.** `bot.py`, two launchers, one bootstrap.
2. **Nothing OS-specific in `bot.py`** — describing a platform is text, not behaviour.
3. **No new dependency, and nothing installed on the friend's machine.**
4. **Secrets never enter git and never enter a chat message.**
5. **Code and docs in English; everything the group reads in Spanish.**
6. **`ponytail:` on deliberate shortcuts**, with the ceiling and the upgrade path.
7. **Two update mechanisms now exist** — `git pull` for a clone, re-fetch for the tarball. That is a
   real cost of this order. Write it down where the next person will see it, and do not let the two
   drift into contradicting each other.

## STANDING RULES

- `python -m py_compile bot.py` and `python bot.py --self-check` pass before every commit.
- Re-run the token-guard mutations and the must-stay-red entries you touch. Ship the table.
- Explicit paths; never `git add .` or `-A`.
- **Every Windows claim you cannot test must be labelled as untested**, in the report and in the docs.
  There is already a precedent for this in `docs/updating.md`; extend it rather than starting a new
  list.

## CHECKPOINT

Report:

1. What you ran, and the full list of what you could **not** run.
2. Which files the bootstrap preserves across a re-run, and why that is safe.
3. Your signal for slice 3 and why.
4. Anything in this order that is wrong on fact — including whether PowerShell and `tar` really are
   guaranteed present on the Windows versions this group is likely to have.
5. Every `ponytail:` left, and exactly what the first Windows friend should be watched for.
