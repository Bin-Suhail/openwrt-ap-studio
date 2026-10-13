@echo off
chcp 65001 >nul
cd /d "%~dp0"
title OpenWrt AP Studio
where py.exe >nul 2>nul
if not errorlevel 1 (
  py -3 -X utf8 app\server.py %*
  goto done
)
where python.exe >nul 2>nul
if not errorlevel 1 (
  python -X utf8 app\server.py %*
  goto done
)
echo Python 3.10 or later is required. No additional libraries are needed.
echo Install Python from python.org, then open START.cmd again.
:done
pause
