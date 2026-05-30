@ECHO OFF
REM agent-shim.cmd -- cmd.exe companion to agent-shim.ps1 (human-memory).
REM
REM In cmd.exe, .ps1 is not on PATHEXT, so PowerShell's shim wouldn't be picked
REM up; .cmd is. At install this file is copied to claude.cmd / codex.cmd /
REM opencode.cmd, so %~n0 is the agent name. All the real logic lives once, in
REM agent-shim.ps1 -- this just forwards to it, passing identity via the env var.
REM
REM The "goto #_undefined_# 2>NUL || <cmd>" idiom is borrowed from npm's
REM cmd-shim: jumping to a missing label leaves cmd at end-of-script, so when
REM Ctrl+C reaches the foreground child the usual "Terminate batch job (Y/N)?"
REM prompt never appears -- the TUI stays transparent. No SETLOCAL/ENDLOCAL is
REM used so AGENT_SHIM_NAME survives into the child PowerShell process.
REM
REM -ExecutionPolicy RemoteSigned (not Bypass) runs our local, Unblock-File'd
REM engine while avoiding the "Bypass" token that antivirus heuristics flag.
SET "AGENT_SHIM_NAME=%~n0"
goto #_undefined_# 2>NUL || powershell -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0agent-shim.ps1" %*
