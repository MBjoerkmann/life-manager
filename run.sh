#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env if present (for ANTHROPIC_API_KEY etc.)
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

VENV_DIR="$SCRIPT_DIR/.venv"

# Create virtualenv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
  echo "[life_manager] Creating virtual environment..."
  python3 -m venv "$VENV_DIR"
fi

# Activate
source "$VENV_DIR/bin/activate"

# Install / upgrade requirements
echo "[life_manager] Installing requirements..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "[life_manager] Starting server on http://localhost:8001"
exec uvicorn main:app --host 0.0.0.0 --port 8001 --reload
