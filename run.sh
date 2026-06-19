#!/usr/bin/env bash
# ============================================================
#  Phoenix demo - one-click launcher (macOS / Linux)
#  Requires only Python 3.10+ installed.
#  First run sets up a venv and installs deps; later runs are instant.
# ============================================================
set -e
cd "$(dirname "$0")"

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || { echo "[Phoenix] Python 3.10+ not found. Install it and re-run."; exit 1; }

if [ ! -x ".venv/bin/python" ]; then
  echo "[Phoenix] First-time setup: creating virtual environment..."
  "$PY" -m venv .venv
  # shellcheck disable=SC1091
  . .venv/bin/activate
  echo "[Phoenix] Installing dependencies..."
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
else
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi

URL="http://127.0.0.1:8000"
echo "[Phoenix] Starting the demo server -> $URL"
( sleep 2; (command -v open >/dev/null && open "$URL") || (command -v xdg-open >/dev/null && xdg-open "$URL") || true ) &
echo "[Phoenix] (To use the LLM, put GEMINI_API_KEY or ANTHROPIC_API_KEY in a .env file, then re-run.)"
echo "[Phoenix] Press Ctrl+C to stop."
python -m orchestrator.server
