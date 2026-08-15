#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

python3 -c "from app.state_store import STORE; STORE.reset('run_demo.sh')"

python3 -m uvicorn app.mock_gov:app --host 127.0.0.1 --port 9001 &
MOCK_GOV_PID=$!

cleanup() {
  if [[ -n "${DATAMASTER_PID:-}" ]]; then kill "$DATAMASTER_PID" 2>/dev/null || true; fi
  if [[ -n "${MOCK_GOV_PID:-}" ]]; then kill "$MOCK_GOV_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
DATAMASTER_PID=$!

echo "DataMaster judge demo is starting from a clean reset."
echo "Console: http://127.0.0.1:8000"
echo "Permit:  http://127.0.0.1:8000/permit"
echo "Press Ctrl+C to stop both local servers."

wait "$DATAMASTER_PID"
