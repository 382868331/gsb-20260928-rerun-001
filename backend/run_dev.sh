#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/uvicorn app.api:app --reload --host 127.0.0.1 --port 8000 --app-dir backend
