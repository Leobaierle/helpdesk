#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${APP_DB_PATH:-"$ROOT_DIR/db/helpdesk.db"}"
TARGET_DIR="${1:-"$ROOT_DIR/db/backups"}"

mkdir -p "$TARGET_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET_FILE="$TARGET_DIR/$(basename "${DB_PATH%.*}")-$STAMP.db"

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" ".backup '$TARGET_FILE'"
else
  python3 - "$DB_PATH" "$TARGET_FILE" <<'PY'
import sqlite3
import sys

source_path, target_path = sys.argv[1], sys.argv[2]

with sqlite3.connect(source_path) as source_conn:
    target_conn = sqlite3.connect(target_path)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
PY
fi

echo "$TARGET_FILE"
