@echo off
REM ============================================================
REM  Phoenix demo - one-click launcher (Windows)
REM  Requires only Python 3.10+ installed and on PATH.
REM  First run sets up a virtual env and installs deps (1-2 min);
REM  later runs start instantly.
REM ============================================================
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 ( set "PY=py" ) else ( set "PY=python" )

%PY% --version >nul 2>nul
if not %errorlevel%==0 (
  echo [Phoenix] Python not found. Install Python 3.10+ from python.org and re-run.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [Phoenix] First-time setup: creating virtual environment...
  %PY% -m venv .venv
  call ".venv\Scripts\activate.bat"
  echo [Phoenix] Installing dependencies...
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
) else (
  call ".venv\Scripts\activate.bat"
)

echo [Phoenix] Starting the demo server...
start "" http://127.0.0.1:8000
echo [Phoenix] Open in your browser: http://127.0.0.1:8000
echo [Phoenix] (To use the LLM, put GEMINI_API_KEY or ANTHROPIC_API_KEY in a .env file, then re-run.)
echo [Phoenix] Press Ctrl+C in this window to stop.
python -m orchestrator.server

endlocal
