#!/bin/bash
# Starts the Architectural Plans Tool on macOS (or Linux). On a Mac you can double-click run.command instead.
# Needs Python 3.10 or newer. Everything is installed into a private folder (.venv), so nothing else on the computer changes.
set -e
cd "$(dirname "$0")"
ROOT="$PWD"
PORT="${PORT:-8000}"
URL="http://localhost:$PORT"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer was not found."
  echo "Install it from https://www.python.org/downloads/ (or run: brew install python), then run this again."
  exit 1
fi

# Re-create the private environment if it is missing or was made with an older Python.
if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then rm -rf .venv; fi
if [ ! -x .venv/bin/python ]; then
  echo "Setting up (first run only)..."
  "$PY" -m venv .venv || { echo "Could not create the .venv folder. On Linux you may need: sudo apt install python3-venv"; exit 1; }
fi
echo "Installing requirements (first run takes a minute)..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r backend/requirements.txt

# Close an old copy of the tool that is still running on this port, so updates always take effect.
if command -v lsof >/dev/null 2>&1; then
  for pid in $(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null); do
    case "$(ps -p "$pid" -o comm= 2>/dev/null)" in *[Pp]ython*|*uvicorn*) kill "$pid" 2>/dev/null || true ;; esac
  done
  sleep 1
fi

open_browser() {
  sleep 3
  if [ "$(uname)" = "Darwin" ]; then
    open -a "Google Chrome" "$URL" 2>/dev/null || open "$URL"     # Chrome if it is installed, otherwise the default browser
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
}
[ -z "$NO_BROWSER" ] && open_browser &

echo ""
echo "Starting Architectural Plans Tool at $URL"
echo "Leave this window open while you use it. Close it or press Ctrl+C to stop."
echo ""
cd backend
exec "$ROOT/.venv/bin/python" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
