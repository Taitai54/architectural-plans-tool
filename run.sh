#!/bin/bash
cd "$(dirname "$0")/backend"
pip install -q -r requirements.txt
echo ""
echo "Starting Architectural Plans Tool..."
echo "Open your browser at: http://localhost:8000"
echo ""
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
