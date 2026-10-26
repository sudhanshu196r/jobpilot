@echo off
REM Start JobPilot (Windows). Creates a virtualenv on first run.
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv
)
.venv\Scripts\python -m pip install -q -r requirements.txt
.venv\Scripts\python -m app
pause
