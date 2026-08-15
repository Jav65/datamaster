#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

port_is_available() {
  python3 - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
    try:
        server_socket.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(1)
PY
}

for port in 8000 9001; do
  if ! port_is_available "$port"; then
    echo "DataMaster cannot start because 127.0.0.1:$port is already in use."
    echo "Stop the earlier DataMaster process with Ctrl+C, then run ./run_demo.sh again."
    exit 1
  fi
done

python3 -c "from app.state_store import STORE; STORE.reset('run_demo.sh')"

python3 -m uvicorn app.mock_gov:app --host 127.0.0.1 --port 9001 &
MOCK_GOV_PID=$!

sleep 0.4
if ! kill -0 "$MOCK_GOV_PID" 2>/dev/null; then
  echo "The mock government server failed to start. Review the error above."
  wait "$MOCK_GOV_PID"
fi

cleanup() {
  if [[ -n "${DATAMASTER_PID:-}" ]]; then kill "$DATAMASTER_PID" 2>/dev/null || true; fi
  if [[ -n "${MOCK_GOV_PID:-}" ]]; then kill "$MOCK_GOV_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
DATAMASTER_PID=$!

sleep 0.4
if ! kill -0 "$DATAMASTER_PID" 2>/dev/null; then
  echo "The DataMaster server failed to start. Review the error above."
  wait "$DATAMASTER_PID"
fi

echo "DataMaster judge demo is starting from a clean reset."
echo "Console: http://127.0.0.1:8000"
echo "Permit:  http://127.0.0.1:8000/permit"
echo "Disdukcapil: http://127.0.0.1:8000/disdukcapil"
echo "Press Ctrl+C to stop both local servers."

wait "$DATAMASTER_PID"
