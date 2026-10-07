@echo off
title bpgen - web UI (keep this window open)
rem (runs from this folder, with the python on PATH)
cd /d "%~dp0"
python -m bpgen serve %*
echo.
echo bpgen stopped. Press any key to close.
pause >nul
