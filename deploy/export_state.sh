#!/usr/bin/env bash
set -euo pipefail

DEST_DIR=""
ENV_PATH=".env"
DB_PATH="db.sqlite3"
ARCHIVE_PATH="ARCHIVE"
PROCESSED_PATH="PROCESSED_NOTES"
RCLONE_CONFIG_PATH="${HOME}/.config/rclone/rclone.conf"
INCLUDE_PROCESSED=1
CREATE_TARBALL=0

usage() {
  cat <<'EOF'
Usage: deploy/export_state.sh DEST_DIR [options]

Exports application state for Raspberry Pi import. The Supernote mirror is excluded.

Options:
  --env PATH            Path to app env file. Default: .env
  --database PATH       Path to SQLite database. Default: db.sqlite3
  --archive PATH        Path to ARCHIVE directory. Default: ARCHIVE
  --processed PATH      Path to PROCESSED_NOTES directory. Default: PROCESSED_NOTES
  --skip-processed      Do not export PROCESSED_NOTES
  --rclone-config PATH  Path to rclone.conf. Default: ~/.config/rclone/rclone.conf
  --tarball             Create DEST_DIR.tar.gz after export
  -h, --help            Show this help
EOF
}

require_path() {
  local label="$1"
  local value="$2"
  if [[ ! -e "$value" ]]; then
    echo "$label not found: $value" >&2
    exit 1
  fi
}

backup_database() {
  local source_db="$1"
  local dest_db="$2"

  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$source_db" ".backup '$dest_db'"
    return
  fi

  local python_bin=""
  if command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  elif command -v python >/dev/null 2>&1; then
    python_bin="python"
  else
    echo "Neither sqlite3 nor python/python3 is available for database export." >&2
    exit 1
  fi

  "$python_bin" - "$source_db" "$dest_db" <<'PYTHON'
import sqlite3
import sys

source_db, dest_db = sys.argv[1], sys.argv[2]
source = sqlite3.connect(source_db)
dest = sqlite3.connect(dest_db)
try:
    source.backup(dest)
finally:
    dest.close()
    source.close()
PYTHON
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV_PATH="$2"
      shift 2
      ;;
    --database)
      DB_PATH="$2"
      shift 2
      ;;
    --archive)
      ARCHIVE_PATH="$2"
      shift 2
      ;;
    --processed)
      PROCESSED_PATH="$2"
      shift 2
      ;;
    --skip-processed)
      INCLUDE_PROCESSED=0
      shift
      ;;
    --rclone-config)
      RCLONE_CONFIG_PATH="$2"
      shift 2
      ;;
    --tarball)
      CREATE_TARBALL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      if [[ -z "$DEST_DIR" ]]; then
        DEST_DIR="$1"
        shift
      else
        echo "Unexpected argument: $1" >&2
        usage >&2
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$DEST_DIR" ]]; then
  usage >&2
  exit 1
fi

require_path "Env file" "$ENV_PATH"
require_path "Database" "$DB_PATH"
require_path "Archive directory" "$ARCHIVE_PATH"
require_path "rclone config" "$RCLONE_CONFIG_PATH"
if [[ "$INCLUDE_PROCESSED" == "1" && -e "$PROCESSED_PATH" && ! -d "$PROCESSED_PATH" ]]; then
  echo "Processed path is not a directory: $PROCESSED_PATH" >&2
  exit 1
fi

mkdir -p "$DEST_DIR"
rm -f "$DEST_DIR/db.sqlite3" "$DEST_DIR/app.env" "$DEST_DIR/rclone.conf" "$DEST_DIR/export-manifest.txt"
rm -rf "$DEST_DIR/ARCHIVE" "$DEST_DIR/PROCESSED_NOTES"

backup_database "$DB_PATH" "$DEST_DIR/db.sqlite3"
cp "$ENV_PATH" "$DEST_DIR/app.env"
cp "$RCLONE_CONFIG_PATH" "$DEST_DIR/rclone.conf"
cp -a "$ARCHIVE_PATH" "$DEST_DIR/ARCHIVE"

if [[ "$INCLUDE_PROCESSED" == "1" && -d "$PROCESSED_PATH" ]]; then
  cp -a "$PROCESSED_PATH" "$DEST_DIR/PROCESSED_NOTES"
fi

chmod 600 "$DEST_DIR/app.env" "$DEST_DIR/rclone.conf" "$DEST_DIR/db.sqlite3"

cat > "$DEST_DIR/export-manifest.txt" <<EOF
Exported: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Environment: $ENV_PATH
Database: $DB_PATH
Archive: $ARCHIVE_PATH
Processed: $([[ "$INCLUDE_PROCESSED" == "1" && -d "$PROCESSED_PATH" ]] && printf '%s' "$PROCESSED_PATH" || printf '%s' 'excluded')
rclone config: $RCLONE_CONFIG_PATH
Supernote mirror: excluded
EOF

if [[ "$CREATE_TARBALL" == "1" ]]; then
  tar -C "$(dirname "$DEST_DIR")" -czf "$DEST_DIR.tar.gz" "$(basename "$DEST_DIR")"
  echo "Created tarball: $DEST_DIR.tar.gz"
fi

echo "Export complete: $DEST_DIR"
