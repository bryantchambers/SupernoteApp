#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_root
target="${1:-main}"

if [[ -n "$(git -C "${PROJECT_ROOT}" status --porcelain)" ]]; then
    echo "Refusing to update a dirty installation: ${PROJECT_ROOT}" >&2
    exit 1
fi

backup_path="$("${PROJECT_ROOT}/deploy/backup.sh")"
old_revision="$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"

rollback() {
    trap - ERR
    echo "Update failed; restoring revision ${old_revision}." >&2
    git -C "${PROJECT_ROOT}" checkout --detach "${old_revision}"
    compose build
    compose up -d
    "${PROJECT_ROOT}/deploy/restore.sh" "${backup_path}"
}
trap rollback ERR

git -C "${PROJECT_ROOT}" fetch --tags origin
if [[ "${target}" == "main" ]]; then
    target="origin/main"
fi
git -C "${PROJECT_ROOT}" checkout --detach "${target}"
compose build --pull
compose up -d
wait_for_health 120
trap - ERR

echo "Updated to $(git -C "${PROJECT_ROOT}" rev-parse --short HEAD)"
echo "Rollback backup: ${backup_path}"
