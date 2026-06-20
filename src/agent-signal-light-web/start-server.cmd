@echo off
setlocal
cd /d "%~dp0"
start "Agent Signal Light Web" /min node server.js
exit /b 0
