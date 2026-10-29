# Manual test checklist (about 15 minutes, needs Ollama running)

Setup: backend and frontend running as in the README, models pulled, a small project folder opened.

**Basics**
- [ ] Ctrl+O opens a folder; the explorer shows it; relaunch restores it.
- [ ] Clicking files opens tabs with syntax highlighting; typing shows a dot; Ctrl+S saves (check with `git diff`).

**Search and chat**
- [ ] Chat panel: "Index now" completes; running it again is near-instant.
- [ ] Ask "where is X handled?" about your code: the answer cites sources with file:line.
- [ ] Select code, ask "Explain this": the answer is about the selection.

**Editing**
- [ ] Ctrl+K "add a docstring" shows a diff; Accept applies it; Ctrl+Z undoes it in one step.
- [ ] Ctrl+I a two-file change: review, apply selected; a new file appears on disk.
- [ ] Status bar "AI history": roll the change back; the files return to the original.
- [ ] Edit a file after an AI change, then roll back: you get a conflict warning and nothing is overwritten.

**Tools and permissions** (status bar "Tools")
- [ ] `read_file` on `.env` is refused.
- [ ] `run_command` `git --version` asks first; "Allow once" runs it.
- [ ] `run_command` `type .env` (or `cat .env`) is blocked with no prompt.
- [ ] A command with `timeout_seconds: 3` that sleeps longer is killed and reported.

**Agent** (status bar "Agent")
- [ ] A read-only task ("list the files in X and say what each does") finishes with a summary.
- [ ] A task that needs a command shows an approval card.
- [ ] Stop mid-run: it stops, and nothing stays pending.
- [ ] A small edit task shows the review panel; applied files are saved; history lists the change.