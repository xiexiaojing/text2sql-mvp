#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
    exec /usr/bin/env bash "$0" "$@"
fi

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

resolve_venv_dir() {
    for dir in .venv venv; do
        if [ -x "$ROOT/$dir/bin/python" ]; then
            echo "$ROOT/$dir"
            return 0
        fi
    done
    return 1
}

if ! VENV_DIR="$(resolve_venv_dir)"; then
    echo "Virtualenv not found, creating .venv ..."
    python3 -m venv "$ROOT/.venv"
    VENV_DIR="$ROOT/.venv"
    "$VENV_DIR/bin/pip" install -q -U pip
    "$VENV_DIR/bin/pip" install -q -e "$ROOT"
fi

UVICORN="$VENV_DIR/bin/uvicorn"
if [ ! -x "$UVICORN" ]; then
    echo "Installing project dependencies ..."
    "$VENV_DIR/bin/pip" install -q -e "$ROOT"
fi

export PYTHONPATH=apps/api/src:packages/text2sql_runtime/src
mkdir -p logs
nohup "$UVICORN" text2sql_api.main:app --host 0.0.0.0 --port 8777 > logs/api.log 2>&1 &
echo $! > app.pid
echo "Service started with PID: $(cat app.pid)"
