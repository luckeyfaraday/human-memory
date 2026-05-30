# HUMAN_MEMORY.md

## Current State
Windows port of the shim is built and verified (PowerShell + cmd.exe), and
hardened against antivirus false positives. The bash shim still covers Unix /
Git Bash / WSL. Nothing committed yet — all changes are in the working tree.

## What Just Happened
- Added `shim/win/`: `agent-shim.ps1` (engine), `agent-shim.cmd` (cmd.exe
  companion), `install.ps1`. Same model as the bash shim, adapted to Windows.
- Fixed a real cross-platform bug in `shim/watcher.py`: `os.kill(pid, 0)` is
  DESTRUCTIVE on Windows (Python maps it to TerminateProcess). Replaced with a
  platform-split `agent_alive()` — Windows uses a non-destructive OpenProcess +
  zero-timeout WaitForSingleObject probe. Verified it detects a live PID without
  killing it.
- Verified on Windows: arg-transparency + exit-code propagation through BOTH the
  PowerShell and cmd.exe paths; watcher spawns (pythonw, window-less), logs, and
  exits with the agent; staleness detect + clear (drove the real `main()` loop
  in-process); watcher.py runs under the shim's exact CLI.
- Hardened against AV after Defender flagged test runs as
  `Trojan:Win32/PowhidSubExec` (false positive): dropped `-WindowStyle Hidden`
  (use pythonw's no-window nature), `.cmd` uses `-ExecutionPolicy RemoteSigned`
  not `Bypass`, installer `Unblock-File`s the copies. Documented in README.

## Pending
- [ ] Commit the Windows port (working tree is clean of commits so far)
- [ ] Code-sign the Windows scripts — the durable fix for AV false positives
- [ ] `config.toml` parsing (thresholds/ignore-globs are constants for now)
- [ ] Upgrade watcher from polling -> native inotify (Unix) / ReadDirectoryChangesW (Win)
- [ ] Implement `draft_update()` — currently stubbed (warn-only)
- [ ] Surface staleness in the terminal, not just the log (passive dashboard)
- [ ] Resolution override / multi-agent config wiring

## Key Decisions
- **No `exec()` on Windows**: the PowerShell shim stays alive as parent and runs
  the real agent in the same console (native TUI/Ctrl+C), propagating exit code.
  Watcher tracks the shim's PID and self-exits one poll after it dies.
- **Both `.ps1` and `.cmd` per agent**: PowerShell prefers `.ps1`, cmd.exe prefers
  `.cmd` (PATHEXT). Busybox-style identity via filename (`.ps1`) or
  `$env:AGENT_SHIM_NAME` (`.cmd`). Symlinks need elevation on Windows, so we copy.
- **AV-shaped by nature**: a PATH shim + hidden watcher looks like a trojan to
  heuristics. Minimize the signal (pythonw, RemoteSigned, Unblock-File); sign for real.
- **Resolution reuses `Get-Command -All`** (honors PATH + .ps1/.cmd precedence)
  then drops candidates in our own shim dir.

## Where I Left Off
Windows port complete and verified; docs updated. Next concrete step: commit it,
then continue feature work (likely `config.toml` parsing or the inotify/
ReadDirectoryChangesW upgrade). Do NOT spawn `powershell.exe` children or hidden
processes from test harnesses — that, not the shim, is what trips Defender.
