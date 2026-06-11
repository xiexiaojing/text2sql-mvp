#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="apps/api/src:packages/text2sql_runtime/src"
exec "$ROOT/.venv/bin/uvicorn" text2sql_api.main:app --host 127.0.0.1 --port 8777 "$@"
