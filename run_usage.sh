#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOCK_DIR="${ZECTRIX_LOCK_DIR:-$HOME/.config/zectrix}"
LOCK_FILE="$LOCK_DIR/zectrix-usage.lock"
FLOCK_BIN="${FLOCK_BIN:-flock}"

if ! command -v "$FLOCK_BIN" >/dev/null 2>&1; then
    printf 'error: flock is required for the production runner\n' >&2
    exit 1
fi

mkdir -p "$LOCK_DIR"

if [[ "${1:-}" == "--all-pages" ]]; then
    shift
    exec "$FLOCK_BIN" -n "$LOCK_FILE" /bin/bash -c '
        root_dir="$1"
        shift
        /usr/bin/python3 "$root_dir/push_usage.py" \
            --design rotate "$@"
        /usr/bin/python3 "$root_dir/push_usage.py" \
            --design big "$@"
    ' zectrix-usage-runner "$ROOT_DIR" "$@"
fi

exec "$FLOCK_BIN" -n "$LOCK_FILE" \
    /usr/bin/python3 "$ROOT_DIR/push_usage.py" "$@"
