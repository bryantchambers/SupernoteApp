#!/usr/bin/env bash
set -u
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

failures=0
check() {
    local label="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        printf 'OK   %s\n' "${label}"
    else
        printf 'FAIL %s\n' "${label}"
        failures=$((failures + 1))
    fi
}

check "Docker" docker version
check "Docker Compose" docker compose version
check "Compose configuration" compose config --quiet
check "State directory" test -d "${STATE_DIR}/data"
check "Application environment" test -f "${STATE_DIR}/config/app.env"
check "Rclone configuration" test -f "${STATE_DIR}/config/rclone/rclone.conf"
check "Web container" compose ps --status running web
check "HTTP health" curl --fail --silent --max-time 5 "http://127.0.0.1:${APP_PORT}/"
check "Django" compose exec -T web python manage.py check
check "Supernote tool" compose exec -T web supernote-tool --help
check "Calibre" compose exec -T web ebook-convert --version
check "Rclone" compose exec -T web rclone version

if [[ "${failures}" -gt 0 ]]; then
    echo "${failures} check(s) failed." >&2
    exit 1
fi
