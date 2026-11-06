#!/bin/bash
# Double-click this file in Finder to install (first run) and open the tool.
cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="${ROOT}/.venv/bin/python"

hold() {
  echo ""
  echo "Press Return to close this window."
  read -r _
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found."
  echo "Install it with: brew install python"
  hold
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 or newer is required. This machine has: $(python3 --version 2>&1)"
  hold
  exit 1
fi

if [[ ! -x "${PY}" ]]; then
  echo "Creating a local Python environment (first run only)..."
  python3 -m venv "${ROOT}/.venv" || { echo "Could not create .venv."; hold; exit 1; }
fi

echo "Installing requirements (first run takes a minute)..."
"${PY}" -m pip install -q -r backend/requirements.txt || {
  echo "Install failed. See the message above."
  hold
  exit 1
}

echo "Closing any copy of the tool that is still running on port 8000..."
if command -v lsof >/dev/null 2>&1; then
  while read -r pid; do
    [[ -z "${pid}" ]] && continue
    comm=$(ps -p "${pid}" -o comm= 2>/dev/null || true)
    case "${comm}" in
      *python*|*Python*|*uvicorn*)
        kill "${pid}" 2>/dev/null || true
        ;;
    esac
  done < <(lsof -nP -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true)
  sleep 1
fi

echo ""
echo "Starting Architectural Plans Tool at http://localhost:8000"
echo "Leave this window open while you use it. Close it or press Ctrl+C to stop."
echo ""

(sleep 2 && open "http://localhost:8000") >/dev/null 2>&1 &

cd "${ROOT}/backend"
"${PY}" -m uvicorn main:app --host 127.0.0.1 --port 8000
status=$?
echo ""
echo "Server stopped."
hold
exit "${status}"
