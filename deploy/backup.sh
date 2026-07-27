#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_root
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="${STATE_DIR}/backups/supernote-app-${timestamp}.tar.gz"
stage="$(mktemp -d "${STATE_DIR}/backups/.stage.XXXXXX")"
trap 'rm -rf "${stage}"' EXIT

install -d -m 0700 "${stage}/data"
if compose ps --status running web --quiet | grep -q .; then
    compose exec -T web sqlite3 /data/db.sqlite3 ".backup '/backups/.database-${timestamp}.sqlite3'"
    cp "${STATE_DIR}/backups/.database-${timestamp}.sqlite3" "${stage}/data/db.sqlite3"
    rm -f "${STATE_DIR}/backups/.database-${timestamp}.sqlite3"
elif [[ -f "${STATE_DIR}/data/db.sqlite3" ]]; then
    cp "${STATE_DIR}/data/db.sqlite3" "${stage}/data/db.sqlite3"
fi

[[ ! -d "${STATE_DIR}/data/ARCHIVE" ]] || cp -a "${STATE_DIR}/data/ARCHIVE" "${stage}/data/"
[[ ! -d "${STATE_DIR}/data/PROCESSED_NOTES" ]] || cp -a "${STATE_DIR}/data/PROCESSED_NOTES" "${stage}/data/"
cp -a "${STATE_DIR}/config" "${stage}/"

tar -C "${stage}" -czf "${backup_path}" .
chmod 0600 "${backup_path}"
echo "${backup_path}"
