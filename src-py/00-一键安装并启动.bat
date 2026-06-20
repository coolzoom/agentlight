@echo off
setlocal EnableExtensions EnableDelayedExpansion
title AI Status Light (Python)
color 1F
cd /d "%~dp0"

set "MODE=all"
if /I "%~1"=="--install-only" set "MODE=install" & goto parse_done
if /I "%~1"=="-i" set "MODE=install" & goto parse_done
if /I "%~1"=="--start-only" set "MODE=start" & goto parse_done
if /I "%~1"=="-s" set "MODE=start" & goto parse_done
if /I "%~1"=="--stop" set "MODE=stop" & goto parse_done
if /I "%~1"=="-k" set "MODE=stop" & goto parse_done
if /I "%~1"=="--kill" set "MODE=stop" & goto parse_done
if /I "%~1"=="-h" goto show_help
if /I "%~1"=="--help" goto show_help
if not "%~1"=="" (
  echo.
  echo [Failed] Unknown option: %~1
  echo.
  goto show_help
)
:parse_done

set "ROOT=%CD%"
set "WEB_DIR=%ROOT%\agent-signal-light-web"
set "WEB_SERVER=%WEB_DIR%\server.py"
set "WEB_URL=http://127.0.0.1:8787"
set "PORT=8787"
set "BRIDGE_SCRIPT=%ROOT%\codex_status_bridge.py"
set "REQUIREMENTS=%ROOT%\requirements.txt"
set "LOG_DIR=%ROOT%\.run"
set "WEB_LOG=%LOG_DIR%\web-server.log"
set "BRIDGE_LOG=%LOG_DIR%\serial-bridge.log"

call :resolve_python
if errorlevel 1 goto failed

set "PY_INVOKE=%PYTHON_EXE%"
if defined PYTHON_ARGS set "PY_INVOKE=%PYTHON_EXE% %PYTHON_ARGS%"

if /I "%MODE%"=="install" goto do_install
if /I "%MODE%"=="start" goto do_start
if /I "%MODE%"=="stop" goto do_stop
goto do_all

:do_all
call :do_install
if errorlevel 1 goto failed
call :do_start
if errorlevel 1 goto failed
goto done

:do_install
echo.
echo ============================================================
echo AI Status Light (Python) - One Click Setup (Windows)
echo ============================================================
echo.
echo [Check] Python version
%PY_INVOKE% --version
if errorlevel 1 goto failed

echo.
echo ============================================================
echo [Install] Python Dependencies
echo ============================================================
%PY_INVOKE% -m pip install --user --disable-pip-version-check -r "%REQUIREMENTS%"
if errorlevel 1 goto failed

echo.
echo ============================================================
echo [Install] Codex / Claude / Cursor Hook Configuration
echo ============================================================
pushd "%WEB_DIR%"
%PY_INVOKE% install_hooks.py
set "HOOK_RC=!ERRORLEVEL!"
popd
if not "!HOOK_RC!"=="0" goto failed

echo.
echo ============================================================
echo [Check] Quick Self Check
echo ============================================================
%PY_INVOKE% -m py_compile "%ROOT%\agent_light_control.py" "%ROOT%\codex_status_bridge.py" "%WEB_DIR%\server.py" "%WEB_DIR%\hook_forwarder.py" "%WEB_DIR%\install_hooks.py"
if errorlevel 1 goto failed

echo.
echo [OK] Environment setup finished.
if /I "%MODE%"=="install" goto done
goto :eof

:do_start
echo.
echo ============================================================
echo AI Status Light (Python) - Start Full System
echo ============================================================

call :is_port_listening
if errorlevel 1 (
  if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
  start "Agent Signal Light Web" /min cmd /c "%PY_INVOKE% "%WEB_SERVER%" >>"%WEB_LOG%" 2>&1"
  echo [Run] Started web dashboard.
) else (
  echo [OK] web dashboard is already running.
)

call :is_bridge_running
if errorlevel 1 (
  if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
  start "Agent Signal Light Bridge" /min cmd /c "%PY_INVOKE% -u "%BRIDGE_SCRIPT%" >>"%BRIDGE_LOG%" 2>&1"
  echo [Run] Started serial bridge.
) else (
  echo [OK] serial bridge is already running.
)

set "WEB_OK=0"
for /L %%I in (1,1,20) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%WEB_URL%/api/status' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (
    set "WEB_OK=1"
    goto web_ready
  )
  timeout /t 1 /nobreak >nul
)
:web_ready
if "%WEB_OK%"=="1" (
  echo [OK] Web dashboard is responding.
) else (
  echo [Warn] Web dashboard did not respond yet. Check %WEB_LOG%
)

start "" "%WEB_URL%" >nul 2>&1

echo.
echo [OK] System is ready.
echo [Web] %WEB_URL%
echo [Log] %WEB_LOG%
echo [Log] %BRIDGE_LOG%
echo [Tip] If ESP32 is plugged in, the bridge will auto-detect the COM port.
echo.
if /I "%MODE%"=="start" goto done
goto :eof

:do_stop
echo.
echo ============================================================
echo AI Status Light (Python) - Stop Full System
echo ============================================================

call :stop_web_server
call :stop_serial_bridge

call :is_port_listening
if errorlevel 1 (
  echo [OK] Port %PORT% is free.
) else (
  echo [Warn] Port %PORT% is still in use.
)

call :is_bridge_running
if errorlevel 1 (
  echo [OK] Serial bridge is stopped.
) else (
  echo [Warn] Serial bridge is still running.
)

echo.
echo [OK] System stopped.
echo.
goto done

:resolve_python
set "PYTHON_EXE="
set "PYTHON_ARGS="
where python >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_EXE=python"
  goto resolve_python_ok
)
where py >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_EXE=py"
  set "PYTHON_ARGS=-3"
  goto resolve_python_ok
)
echo.
echo [Failed] Python 3 not found. Install Python 3.12+ and run this script again.
exit /b 1
:resolve_python_ok
exit /b 0

:is_port_listening
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if ($c) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0

:is_bridge_running
powershell -NoProfile -Command "$root = '%ROOT:\=\\%'; $p = Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -and ($_.CommandLine -like ('*' + $root + '*codex_status_bridge.py*')) }; if ($p) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0

:stop_web_server
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
  echo [Stop] web dashboard (PID: %%P)
  taskkill /PID %%P /T /F >nul 2>&1
)
exit /b 0

:stop_serial_bridge
for /f "usebackq tokens=1" %%P in (`powershell -NoProfile -Command "$root = '%ROOT:\=\\%'; Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -and ($_.CommandLine -like ('*' + $root + '*codex_status_bridge.py*')) } | ForEach-Object { $_.ProcessId }"`) do (
  if not "%%P"=="" (
    echo [Stop] serial bridge (PID: %%P)
    taskkill /PID %%P /T /F >nul 2>&1
  )
)
exit /b 0

:show_help
echo.
echo Usage: %~nx0 [options]
echo.
echo Options:
echo   (no args)        Install dependencies, configure hooks, then start the system
echo   --install-only   Install and configure only
echo   --start-only     Start web dashboard and serial bridge only
echo   --stop           Stop web dashboard and serial bridge
echo   -k, --kill       Same as --stop
echo   -h, --help       Show this help
echo.
echo Manual test:
echo   python "%ROOT%\agent_light_control.py"
echo.
goto done

:failed
echo.
echo [Failed] Setup or start did not complete.
echo.
pause
exit /b 1

:done
if /I "%MODE%"=="stop" pause
if /I "%MODE%"=="start" pause
if /I "%MODE%"=="all" pause
exit /b 0
