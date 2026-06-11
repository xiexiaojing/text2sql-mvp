#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
    exec /usr/bin/env bash "$0" "$@"
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ -f app.pid ]; then
    kill "$(cat app.pid)" 2>/dev/null || true
    rm -f app.pid
    echo "Service stopped"
else
    pkill -f "uvicorn text2sql_api.main:app" || true
    echo "Service stopped"
fi
