@echo off
setlocal
cd /d "%~dp0"
start "Agent Signal Light Web" /min python server.py
exit /b 0
