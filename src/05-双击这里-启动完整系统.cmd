@echo off
setlocal
title AI Status Light - Start Full System
color 1F
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-customer-system.ps1"
if errorlevel 1 (
  echo.
  echo [Failed] Full system start failed.
  pause
)
