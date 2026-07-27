#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_root
backup_path="${1:-}"
[[ -n "${backup_path}" && -f "${backup_path}" ]] || {
    echo "Usage: sudo deploy/restore.sh BACKUP.tar.gz" >&2
    exit 2
}

if tar -tzf "${backup_path}" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
    echo "Backup contains unsafe paths." >&2
    exit 1
fi

stage="$(mktemp -d)"
trap 'rm -rf "${stage}"' EXIT
tar -xzf "${backup_path}" -C "${stage}"

compose down
if [[ -f "${stage}/data/db.sqlite3" ]]; then
    install -m 0640 "${stage}/data/db.sqlite3" "${STATE_DIR}/data/db.sqlite3"
fi
if [[ -d "${stage}/data/ARCHIVE" ]]; then
    cp -a "${stage}/data/ARCHIVE/." "${STATE_DIR}/data/ARCHIVE/"
fi
if [[ -d "${stage}/data/PROCESSED_NOTES" ]]; then
    cp -a "${stage}/data/PROCESSED_NOTES/." "${STATE_DIR}/data/PROCESSED_NOTES/"
fi
if [[ -d "${stage}/config" ]]; then
    cp -a "${stage}/config/." "${STATE_DIR}/config/"
fi

chown -R 10001:10001 "${STATE_DIR}/data" "${STATE_DIR}/config"
compose up -d
wait_for_health 120
echo "Restored ${backup_path}"
