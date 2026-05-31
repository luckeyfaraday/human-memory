<#
install.ps1 -- set up the human-memory transparent shim on Windows.

Windows counterpart of shim/install.sh. Creates ~\.agent-memory\{bin,lib,log},
installs the PowerShell engine + cmd companion + watcher, and copies them under
each agent name (busybox-style -- Windows symlinks need elevation, so we copy).
It then PRINTS the one line to put the shim dir earliest on PATH; it does NOT
modify your environment. Idempotent: safe to re-run.
#>

$ErrorActionPreference = 'Stop'

$agentHome = if ($env:AGENT_MEMORY_HOME) { $env:AGENT_MEMORY_HOME } else { Join-Path $HOME '.agent-memory' }
$srcWin = $PSScriptRoot                          # shim\win
$srcRoot = Split-Path -Parent $srcWin            # shim  (watcher.py lives here)

# Agents to shim. Add a name here to cover a new tool.
$agents = @('claude', 'codex', 'opencode')

$binDir = Join-Path $agentHome 'bin'
$libDir = Join-Path $agentHome 'lib'
$logDir = Join-Path $agentHome 'log'
foreach ($d in @($binDir, $libDir, $logDir)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

# The engine (used directly as the per-agent .ps1, and called by the .cmd) and
# the watcher.
Copy-Item -Force (Join-Path $srcWin 'agent-shim.ps1') (Join-Path $binDir 'agent-shim.ps1')
Copy-Item -Force (Join-Path $srcRoot 'watcher.py')     (Join-Path $libDir 'watcher.py')
Copy-Item -Force (Join-Path $srcRoot 'whiteboard.py')  (Join-Path $libDir 'whiteboard.py')
Copy-Item -Force (Join-Path $srcRoot 'drafter.py')     (Join-Path $libDir 'drafter.py')

# The viewer (stdlib, cross-platform). The script has no extension, so install a
# .cmd wrapper that runs it with python — that's what cmd.exe/PowerShell resolve.
Copy-Item -Force (Join-Path $srcRoot 'human-memory') (Join-Path $libDir 'human-memory')
@'
@ECHO OFF
python "%~dp0..\lib\human-memory" %*
'@ | Set-Content -Encoding ASCII (Join-Path $binDir 'human-memory.cmd')

# Seed config.toml from the example, but never clobber an existing one — the
# user's tuning must survive re-installs.
$configFile = Join-Path $agentHome 'config.toml'
if (-not (Test-Path -LiteralPath $configFile)) {
    Copy-Item -Force (Join-Path $srcRoot 'config.toml.example') $configFile
    Write-Host "  wrote default config: $configFile"
} else {
    Write-Host "  kept existing config: $configFile"
}

foreach ($a in $agents) {
    # .ps1 wins in PowerShell, .cmd wins in cmd.exe -- install both so the shim
    # is picked up whichever shell you launch the agent from.
    Copy-Item -Force (Join-Path $srcWin 'agent-shim.ps1') (Join-Path $binDir "$a.ps1")
    Copy-Item -Force (Join-Path $srcWin 'agent-shim.cmd') (Join-Path $binDir "$a.cmd")
    Write-Host "  shimmed: $a  (.ps1 + .cmd)"
}

# Strip any Mark-of-the-Web (e.g. if the repo was downloaded as a zip) so the
# installed copies run as trusted-local under RemoteSigned -- no -ExecutionPolicy
# Bypass needed, which antivirus heuristics flag.
Unblock-File -Path (Join-Path $binDir '*.ps1') -ErrorAction SilentlyContinue
Unblock-File -Path (Join-Path $binDir '*.cmd') -ErrorAction SilentlyContinue
Unblock-File -Path (Join-Path $libDir '*.py')  -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Installed to $agentHome"
Write-Host ""
Write-Host "Put the shim dir EARLIEST on PATH. For THIS session:"
Write-Host ""
Write-Host "    `$env:Path = `"$binDir;`$env:Path`""
Write-Host ""
Write-Host "To persist across new shells (optional -- run it yourself; edits your USER PATH):"
Write-Host ""
Write-Host "    [Environment]::SetEnvironmentVariable('Path', '$binDir;' + [Environment]::GetEnvironmentVariable('Path','User'), 'User')"
Write-Host ""
Write-Host "Verify:    (Get-Command claude -All | Select-Object -First 1).Source   # should be under $binDir"
Write-Host "Uninstall: remove that PATH entry, then  Remove-Item -Recurse -Force '$agentHome'"
