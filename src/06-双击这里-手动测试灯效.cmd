@echo off
setlocal
title AI Status Light - Manual Test
color 1F
cd /d "%~dp0"
python .\agent_light_control.py
if errorlevel 1 (
  echo.
  echo [Failed] Manual test tool did not start.
  echo Please run 00-双击这里-一键安装环境.cmd first.
  pause
)
