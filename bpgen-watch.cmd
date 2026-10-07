@echo off
title bpgen - blueprint watcher
rem (runs from this folder, with the python on PATH)
cd /d "%~dp0"
python -m bpgen watch %*
echo.
echo bpgen stopped. Press any key to close.
pause >nul
