#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="/srv/supernote-app"
APP_PORT="8000"
BIND_ADDRESS="0.0.0.0"
IMPORT_ENV=""
IMPORT_DB=""
IMPORT_ARCHIVE=""
IMPORT_PROCESSED=""
IMPORT_RCLONE=""
INITIAL_SYNC=false
ENABLE_TIMERS=false

usage() {
    cat <<USAGE
Usage: sudo deploy/install.sh [options]

  --state-dir PATH       Persistent state directory (default: /srv/supernote-app)
  --port PORT            LAN port (default: 8000)
  --bind ADDRESS         Bind address (default: 0.0.0.0)
  --env FILE             Import current application .env
  --database FILE        Import current db.sqlite3
  --archive DIR          Import current ARCHIVE directory
  --processed DIR        Import current PROCESSED_NOTES directory
  --rclone-config FILE   Import current rclone.conf
  --initial-sync         Pull Supernote content from OneDrive after startup
  --enable-timers        Enable 10-minute sync and hourly periodical timers
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --state-dir) STATE_DIR="$2"; shift 2 ;;
        --port) APP_PORT="$2"; shift 2 ;;
        --bind) BIND_ADDRESS="$2"; shift 2 ;;
        --env) IMPORT_ENV="$2"; shift 2 ;;
        --database) IMPORT_DB="$2"; shift 2 ;;
        --archive) IMPORT_ARCHIVE="$2"; shift 2 ;;
        --processed) IMPORT_PROCESSED="$2"; shift 2 ;;
        --rclone-config) IMPORT_RCLONE="$2"; shift 2 ;;
        --initial-sync) INITIAL_SYNC=true; shift ;;
        --enable-timers) ENABLE_TIMERS=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run the installer with sudo." >&2
    exit 1
fi

for command in docker curl; do
    command -v "${command}" >/dev/null || {
        echo "Missing required command: ${command}" >&2
        exit 1
    }
done

docker compose version >/dev/null
architecture="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case "${architecture}" in
    arm64|aarch64|amd64|x86_64) ;;
    *) echo "Unsupported architecture: ${architecture}" >&2; exit 1 ;;
esac

install -d -m 0750 "${STATE_DIR}" "${STATE_DIR}/data" "${STATE_DIR}/data/ARCHIVE"
install -d -m 0750 "${STATE_DIR}/data/PROCESSED_NOTES" "${STATE_DIR}/data/Supernote"
install -d -m 0750 "${STATE_DIR}/config" "${STATE_DIR}/config/rclone" "${STATE_DIR}/backups"

app_env="${STATE_DIR}/config/app.env"
compose_env="${STATE_DIR}/config/compose.env"

if [[ -n "${IMPORT_ENV}" ]]; then
    install -m 0600 "${IMPORT_ENV}" "${app_env}"
elif [[ ! -f "${app_env}" ]]; then
    install -m 0600 /dev/null "${app_env}"
fi

set_env() {
    local key="$1"
    local value="$2"
    local temp
    temp="$(mktemp)"
    awk -v key="${key}" 'index($0, key "=") != 1 { print }' "${app_env}" >"${temp}"
    printf '%s=%s\n' "${key}" "${value}" >>"${temp}"
    install -m 0600 "${temp}" "${app_env}"
    rm -f "${temp}"
}

secret="$(od -An -N48 -tx1 /dev/urandom | tr -d ' \n')"
host_name="$(hostname)"
host_ips="$(hostname -I 2>/dev/null | xargs | tr ' ' ',' || true)"
allowed_hosts="localhost,127.0.0.1,${host_name}"
[[ -n "${host_ips}" ]] && allowed_hosts="${allowed_hosts},${host_ips}"

if ! grep -q '^DJANGO_SECRET_KEY=' "${app_env}"; then
    set_env DJANGO_SECRET_KEY "${secret}"
fi
set_env DJANGO_DEBUG false
set_env DJANGO_ALLOWED_HOSTS "${allowed_hosts}"
set_env SUPERNOTE_DATA_DIR /data
set_env SUPERNOTE_DATABASE_PATH /data/db.sqlite3
set_env SUPERNOTE_SOURCE /data/Supernote
set_env SUPERNOTE_ARCHIVE_DIR /data/ARCHIVE
set_env SUPERNOTE_PROCESSED_DIR /data/PROCESSED_NOTES
set_env SUPERNOTE_TOOL_COMMAND supernote-tool

cat >"${compose_env}" <<COMPOSE_ENV
STATE_DIR=${STATE_DIR}
APP_PORT=${APP_PORT}
BIND_ADDRESS=${BIND_ADDRESS}
SUPERNOTE_IMAGE=supernote-app:local
CALIBRE_VERSION=9.11.0
RCLONE_RELEASE=1.74.4
COMPOSE_ENV
chmod 0600 "${compose_env}"

if [[ -n "${IMPORT_DB}" ]]; then
    install -m 0640 "${IMPORT_DB}" "${STATE_DIR}/data/db.sqlite3"
fi
if [[ -n "${IMPORT_ARCHIVE}" ]]; then
    cp -a "${IMPORT_ARCHIVE}/." "${STATE_DIR}/data/ARCHIVE/"
fi
if [[ -n "${IMPORT_PROCESSED}" ]]; then
    cp -a "${IMPORT_PROCESSED}/." "${STATE_DIR}/data/PROCESSED_NOTES/"
fi
if [[ -n "${IMPORT_RCLONE}" ]]; then
    install -m 0600 "${IMPORT_RCLONE}" "${STATE_DIR}/config/rclone/rclone.conf"
fi

chown -R 10001:10001 "${STATE_DIR}/data" "${STATE_DIR}/config" "${STATE_DIR}/backups"
chmod 0600 "${app_env}" "${compose_env}"
[[ ! -f "${STATE_DIR}/config/rclone/rclone.conf" ]] || chmod 0600 "${STATE_DIR}/config/rclone/rclone.conf"

export STATE_DIR APP_PORT BIND_ADDRESS
docker compose --env-file "${compose_env}" -f "${PROJECT_ROOT}/compose.yaml" build
docker compose --env-file "${compose_env}" -f "${PROJECT_ROOT}/compose.yaml" up -d

source "${PROJECT_ROOT}/deploy/common.sh"
wait_for_health 120

if [[ "${INITIAL_SYNC}" == true ]]; then
    if [[ ! -f "${STATE_DIR}/config/rclone/rclone.conf" ]]; then
        echo "--initial-sync requires --rclone-config or an existing rclone.conf." >&2
        exit 1
    fi
    compose exec -T web python manage.py sync_supernote --direction pull
fi

if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
    for unit in supernote-app.service supernote-sync.service supernote-sync.timer supernote-periodicals.service supernote-periodicals.timer; do
        sed -e "s|@PROJECT_ROOT@|${PROJECT_ROOT}|g" -e "s|@STATE_DIR@|${STATE_DIR}|g" \
            "${PROJECT_ROOT}/deploy/systemd/${unit}" >"/etc/systemd/system/${unit}"
    done
    systemctl daemon-reload
    systemctl enable supernote-app.service
    if [[ "${ENABLE_TIMERS}" == true ]]; then
        systemctl enable --now supernote-sync.timer supernote-periodicals.timer
    fi
fi

echo "SupernoteApp is running at http://$(hostname -I | awk '{print $1}'):${APP_PORT}/"
echo "Persistent state: ${STATE_DIR}"
echo "Remove safely with: sudo ${PROJECT_ROOT}/deploy/uninstall.sh"
