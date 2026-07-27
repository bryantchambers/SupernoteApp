#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_root
purge_data=false
keep_code=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge-data) purge_data=true; shift ;;
        --keep-code) keep_code=true; shift ;;
        --help|-h)
            echo "Usage: sudo deploy/uninstall.sh [--purge-data] [--keep-code]"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

if command -v systemctl >/dev/null; then
    systemctl disable --now supernote-sync.timer supernote-periodicals.timer 2>/dev/null || true
    systemctl disable --now supernote-app.service 2>/dev/null || true
fi

if [[ -f "${COMPOSE_ENV}" && -f "${COMPOSE_FILE}" ]]; then
    compose down --remove-orphans --volumes
fi

if command -v systemctl >/dev/null; then
    rm -f \
        /etc/systemd/system/supernote-app.service \
        /etc/systemd/system/supernote-sync.service \
        /etc/systemd/system/supernote-sync.timer \
        /etc/systemd/system/supernote-periodicals.service \
        /etc/systemd/system/supernote-periodicals.timer
    systemctl daemon-reload
fi

if [[ "${purge_data}" == true ]]; then
    printf 'Type exactly "DELETE %s" to remove all retained state: ' "${STATE_DIR}"
    read -r confirmation
    if [[ "${confirmation}" != "DELETE ${STATE_DIR}" ]]; then
        echo "State purge cancelled." >&2
        exit 1
    fi
    [[ -n "${STATE_DIR}" && "${STATE_DIR}" != "/" ]] || {
        echo "Unsafe state directory." >&2
        exit 1
    }
    rm -rf --one-file-system "${STATE_DIR}"
else
    echo "Persistent state retained at ${STATE_DIR}"
fi

if [[ "${keep_code}" == false && "${PROJECT_ROOT}" == "/opt/supernote-app" ]]; then
    rm -rf --one-file-system "${PROJECT_ROOT}"
elif [[ "${keep_code}" == false ]]; then
    echo "Code retained because project is outside /opt/supernote-app: ${PROJECT_ROOT}"
fi

echo "SupernoteApp containers and services removed."
