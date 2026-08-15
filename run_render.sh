#!/usr/bin/env bash
set -euo pipefail

python3 -m uvicorn app.mock_gov:app --host 127.0.0.1 --port 9001 &
MOCK_GOV_PID=$!

cleanup() {
  kill "$MOCK_GOV_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
