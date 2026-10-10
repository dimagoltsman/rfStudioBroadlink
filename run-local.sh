#!/usr/bin/env bash
# Run the workbench natively (no Docker). Needed on macOS/Windows, where Docker Desktop
# cannot pass the UDP traffic Broadlink devices use.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/uvicorn ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r backend/requirements.txt
fi
export RFW_DATA_DIR="$PWD/data" RFW_STATIC_DIR="$PWD/frontend"
exec .venv/bin/uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port "${PORT:-8333}"
