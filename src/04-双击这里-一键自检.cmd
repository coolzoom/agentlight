@echo off
setlocal
title AI Status Light - One Click Self Check
color 1F

set "ROOT=%~dp0"
set "APP_DIR=%ROOT%agent-signal-light-web"
set "HOOKS_FILE=%ROOT%.codex\hooks.json"
set "CLAUDE_FILE=%USERPROFILE%\.claude\settings.json"
set "API_URL=http://127.0.0.1:8787/api/status"
set "BRIDGE_FILE=%ROOT%codex_status_bridge.py"
set "TEST_FILE=%ROOT%agent_light_control.py"

echo.
echo ============================================================
echo             AI Status Light - One Click Self Check
echo ============================================================
echo.

call :check_cmd "Node.js installed" "node --version"
call :check_cmd "npm installed" "npm --version"
call :check_cmd "Python installed" "python --version"
call :check_cmd "pyserial installed" "python -c ""import serial"""
call :check_file "Web server entry exists" "%APP_DIR%\server.js"
call :check_file "Hook installer exists" "%APP_DIR%\install-hooks.js"
call :check_file "Serial bridge exists" "%BRIDGE_FILE%"
call :check_file "Manual test tool exists" "%TEST_FILE%"
call :check_file "Workspace Codex hooks exist" "%HOOKS_FILE%"
call :check_file "Claude settings exist" "%CLAUDE_FILE%"
call :check_port "Port 8787 listening"
call :check_http "Web dashboard API responding" "%API_URL%"

echo.
echo ============================================================
echo Self check finished.
echo If any item shows [FAIL], fix that item first.
echo ============================================================
echo.
pause
exit /b 0

:check_cmd
set "LABEL=%~1"
set "CMD=%~2"
cmd /c "%CMD%" >nul 2>&1
if errorlevel 1 (
  echo [FAIL] %LABEL%
) else (
  echo [ OK ] %LABEL%
)
exit /b 0

:check_file
set "LABEL=%~1"
set "FILEPATH=%~2"
if exist "%FILEPATH%" (
  echo [ OK ] %LABEL%
) else (
  echo [FAIL] %LABEL%
)
exit /b 0

:check_port
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue; if ($c) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo [FAIL] %~1
) else (
  echo [ OK ] %~1
)
exit /b 0

:check_http
set "LABEL=%~1"
set "URL=%~2"
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing '%URL%' -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo [FAIL] %LABEL%
) else (
  echo [ OK ] %LABEL%
)
exit /b 0
