#!/usr/bin/env bash
set -euo pipefail

cd /home/bryantchambers/Projects/SupernoteApp
python manage.py sync_supernote "$@"
