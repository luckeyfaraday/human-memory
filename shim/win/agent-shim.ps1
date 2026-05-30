<#
agent-shim.ps1 -- transparent shim for human-memory (Windows: PowerShell + cmd).

Windows counterpart of shim/agent-shim (bash). The bash shim's whole model is
exec(): it replaces itself with the real agent so the terminal talks straight to
it. Windows has no exec(), so this process instead stays alive as the PARENT,
runs the real agent in the SAME console window (so the TUI, ANSI colours, raw
mode and Ctrl+C are all native -- no PTY proxying), then exits with the agent's
exit code. A detached, window-less watcher (pythonw) is spawned first and tracks
THIS process's PID, so it lives exactly as long as the agent session does.

busybox-style identity: at install this file is copied to claude.ps1 / codex.ps1
/ opencode.ps1 and self-identifies from its own filename. When reached through
the .cmd companion (cmd.exe), identity arrives via $env:AGENT_SHIM_NAME instead.
Everything else on the command line is forwarded to the real agent untouched
($args), and no param() block is declared precisely so PowerShell never tries to
bind the agent's own flags (e.g. --resume) as parameters of this shim.
#>

# --- Identity: who are we impersonating? --------------------------------------
$selfName = $env:AGENT_SHIM_NAME
if (-not $selfName) {
    $selfName = [System.IO.Path]::GetFileNameWithoutExtension($MyInvocation.MyCommand.Name)
}
# Our own bin dir -- skipped during real-binary resolution so we never re-run
# ourselves. $PSScriptRoot is this script's directory in both invocation styles.
$shimDir = $PSScriptRoot

$agentHome = if ($env:AGENT_MEMORY_HOME) { $env:AGENT_MEMORY_HOME } else { Join-Path $HOME '.agent-memory' }
$watcher = Join-Path $agentHome 'lib\watcher.py'
$logDir  = Join-Path $agentHome 'log'

# --- 1. Resolve the REAL binary -----------------------------------------------
# An explicit absolute override wins: AGENT_MEMORY_REAL_CLAUDE=C:\path\claude.exe
$overrideVar = "AGENT_MEMORY_REAL_$($selfName.ToUpper())"
$override = [Environment]::GetEnvironmentVariable($overrideVar)
$realBin = $null
if ($override -and (Test-Path -LiteralPath $override)) {
    $realBin = $override
} else {
    # Let PowerShell resolve exactly as it would for the user (this honours
    # PATH order AND the .ps1>.cmd>exe precedence PowerShell applies), then drop
    # any candidate that lives in our own shim dir.
    $realBin = Get-Command $selfName -All -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandType -in 'Application','ExternalScript' -and
            $_.Source -and
            (Split-Path -Parent $_.Source) -ne $shimDir
        } |
        Select-Object -ExpandProperty Source -First 1
}

if (-not $realBin) {
    [Console]::Error.WriteLine("agent-shim: could not find real '$selfName' on PATH (after skipping shim dir)")
    exit 127
}

# --- 2. Spawn the detached, window-less watcher (best-effort) -----------------
# Use pythonw.exe (no console subsystem) so there is no window and no flash --
# and deliberately WITHOUT -WindowStyle Hidden, which antivirus heuristics treat
# as a "hidden subprocess" signal (Trojan:Win32/PowhidSubExec and friends). If
# only console python is on PATH, derive pythonw next to it; if there is no
# pythonw at all, skip the watcher rather than flash a console window every
# launch. Start-Process returns immediately, so the watcher is detached; it
# tracks THIS process's PID and exits when the agent session ends. Best-effort
# throughout -- it must never break the agent.
# N1 -- shim-recursion guard: when our own tooling invokes a shimmed agent
# (e.g. a future watcher-driven draft_update calling `claude -p`), skip the
# watcher so we don't spawn a nested one on every internal call.
$python = $null
if (-not $env:AGENT_MEMORY_INTERNAL) {
    $python = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    if (-not $python) {
        $py = (Get-Command python -ErrorAction SilentlyContinue).Source
        if ($py) {
            $cand = Join-Path (Split-Path -Parent $py) 'pythonw.exe'
            if (Test-Path -LiteralPath $cand) { $python = $cand }
        }
    }
}
if ($python -and (Test-Path -LiteralPath $watcher)) {
    try {
        New-Item -ItemType Directory -Force -Path $logDir -ErrorAction SilentlyContinue | Out-Null
        Start-Process -FilePath $python -ArgumentList @(
            $watcher,
            '--agent',     $selfName,
            '--agent-pid', $PID,
            '--cwd',       $PWD.Path,
            '--log-dir',   $logDir
        ) | Out-Null
    } catch {
        # swallow -- observation is best-effort and stays out of the agent's way
    }
}

# --- 3. Hand off to the real agent (same console) -----------------------------
# Default ErrorActionPreference (Continue) is intentional: a non-zero exit or
# stderr from the agent must pass through, not raise here.
& $realBin @args
exit $LASTEXITCODE
