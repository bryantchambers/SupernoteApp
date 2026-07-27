#!/usr/bin/env sh
set -eu

mkdir -p \
    /data/.sync \
    /data/ARCHIVE \
    /data/PROCESSED_NOTES \
    /data/Supernote \
    /backups \
    /static

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"
