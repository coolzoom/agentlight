$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$WebDir = Join-Path $Root "agent-signal-light-web"
$WebUrl = "http://127.0.0.1:8787"

function Resolve-NodeCommand {
  $cmd = Get-Command node -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }

  $candidate = "C:\Program Files\nodejs\node.exe"
  if (Test-Path $candidate) { return $candidate }

  return $null
}

function Resolve-PythonCommand {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }

  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) { return "$($py.Source) -3" }

  $candidates = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending
  foreach ($candidate in $candidates) {
    $pythonExe = Join-Path $candidate.FullName "python.exe"
    if (Test-Path $pythonExe) { return $pythonExe }
  }

  return $null
}

function Start-IfMissing([string]$Name, [scriptblock]$Condition, [scriptblock]$Starter) {
  if (& $Condition) {
    Write-Host "[OK] $Name is already running."
    return
  }

  & $Starter
  Write-Host "[Run] Started $Name."
}

$nodeCmd = Resolve-NodeCommand
if (-not $nodeCmd) { throw "Node.js not found. Please run 00-双击这里-一键安装环境.cmd first." }

$pythonCmd = Resolve-PythonCommand
if (-not $pythonCmd) { throw "Python not found. Please run 00-双击这里-一键安装环境.cmd first." }

Write-Host ""
Write-Host "============================================================"
Write-Host "AI Status Light - Start Full System"
Write-Host "============================================================"

Start-IfMissing "web dashboard" {
  $conn = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
  return [bool]$conn
} {
  Start-Process -FilePath $nodeCmd -ArgumentList "server.js" -WorkingDirectory $WebDir -WindowStyle Hidden
}

Start-IfMissing "serial bridge" {
  $proc = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -match "codex_status_bridge.py"
  }
  return [bool]$proc
} {
  Start-Process -FilePath "cmd.exe" -ArgumentList "/c $pythonCmd -u .\codex_status_bridge.py" -WorkingDirectory $Root -WindowStyle Hidden
}

Start-Sleep -Seconds 2
Start-Process $WebUrl

Write-Host ""
Write-Host "[OK] System is ready."
Write-Host "[Web] $WebUrl"
Write-Host "[Tip] If ESP32 is plugged in, the bridge will auto-detect the COM port."
Write-Host ""
Read-Host "Press Enter to close"
