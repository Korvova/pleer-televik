@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Plixus: запись и плеер
python -c "import websockets" 2>nul || python -m pip install --quiet websockets
python plixus_recorder.py
pause
