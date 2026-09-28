#!/usr/bin/env bash
# Start ClipFlow: Python stdlib API (port 8000) + Vite dev server (5173).
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi

echo ">> starting backend on http://127.0.0.1:8000"
$PY backend/app.py &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT

echo ">> starting frontend on http://127.0.0.1:5173"
cd frontend
if [ ! -d node_modules ]; then npm install; fi
npm run dev
