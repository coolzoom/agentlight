@echo off
setlocal
title AI Status Light - One Click Setup
color 1F
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0customer-setup.ps1"
if errorlevel 1 (
  echo.
  echo [Failed] Setup did not complete.
  pause
)
