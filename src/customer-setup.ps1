$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$WebDir = Join-Path $Root "agent-signal-light-web"

function Write-Step([string]$Text) {
  Write-Host ""
  Write-Host "============================================================"
  Write-Host $Text
  Write-Host "============================================================"
}

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

function Invoke-CommandLine([string]$CommandLine, [string]$WorkDir = $Root) {
  Push-Location $WorkDir
  try {
    & cmd /c $CommandLine
    if ($LASTEXITCODE -ne 0) {
      throw "command failed: $CommandLine"
    }
  } finally {
    Pop-Location
  }
}

function Ensure-WingetPackage([string]$PackageId, [string]$Label) {
  Write-Host "[Install] $Label"
  & winget install -e --id $PackageId --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) {
    throw "winget install failed: $PackageId"
  }
}

Write-Step "AI Status Light - One Click Setup"

$nodeCmd = Resolve-NodeCommand
if (-not $nodeCmd) {
  Write-Host "[Missing] Node.js"
  Ensure-WingetPackage -PackageId "OpenJS.NodeJS.LTS" -Label "Installing Node.js"
  $nodeCmd = Resolve-NodeCommand
}

if (-not $nodeCmd) {
  throw "Node.js installation was not detected. Please restart this installer once."
}

$pythonCmd = Resolve-PythonCommand
if (-not $pythonCmd) {
  Write-Host "[Missing] Python"
  Ensure-WingetPackage -PackageId "Python.Python.3.12" -Label "Installing Python"
  $pythonCmd = Resolve-PythonCommand
}

if (-not $pythonCmd) {
  throw "Python installation was not detected. Please restart this installer once."
}

Write-Step "Checking Versions"
Invoke-CommandLine "`"$nodeCmd`" --version"
Invoke-CommandLine "$pythonCmd --version"

Write-Step "Installing Python Dependency"
Invoke-CommandLine "$pythonCmd -m pip install --user --disable-pip-version-check pyserial"

Write-Step "Installing Codex Hook Configuration"
Invoke-CommandLine "`"$nodeCmd`" install-hooks.js" $WebDir

Write-Step "Running Quick Self Check"
Invoke-CommandLine "$pythonCmd -m py_compile .\agent_light_control.py .\codex_status_bridge.py"

Write-Host ""
Write-Host "[OK] Environment setup finished."
Write-Host "[Next] Double click 05-双击这里-启动完整系统.cmd"
Write-Host "[Test] Double click 06-双击这里-手动测试灯效.cmd"
Write-Host ""
Read-Host "Press Enter to close"
