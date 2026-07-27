# Raspberry Pi Deployment

This deployment targets a 64-bit Raspberry Pi 5 and also builds on AMD64 development systems. Application code and containers are disposable; the database, archive, credentials, and backups live under /srv/supernote-app.

The Supernote mirror is not migrated or backed up. It is recreated with the initial OneDrive pull.

## Prerequisites

- Raspberry Pi OS 64-bit or another 64-bit Debian-based OS
- Docker Engine with the Compose plugin
- Git and curl
- Enough storage for the OneDrive mirror, archive, image, and Calibre work
- The existing rclone.conf

Do not expose this deployment directly to the public internet. The current application does not require login. Restrict it to the LAN or a private VPN.

## Export Current State

Run on the heavy-lift system while the application is stopped:

~~~bash
mkdir -p /tmp/supernote-pi-import
sqlite3 db.sqlite3 ".backup '/tmp/supernote-pi-import/db.sqlite3'"
cp .env /tmp/supernote-pi-import/app.env
cp ~/.config/rclone/rclone.conf /tmp/supernote-pi-import/rclone.conf
cp -a ARCHIVE /tmp/supernote-pi-import/
cp -a PROCESSED_NOTES /tmp/supernote-pi-import/ 2>/dev/null || true
chmod 600 /tmp/supernote-pi-import/app.env /tmp/supernote-pi-import/rclone.conf
~~~

Transfer the export without the Supernote directory:

~~~bash
rsync -a /tmp/supernote-pi-import/ PI_USER@PI_HOST:/tmp/supernote-pi-import/
~~~

## Install On The Pi

Clone a clean release into the disposable code directory:

~~~bash
sudo git clone https://github.com/bryantchambers/SupernoteApp.git /opt/supernote-app
cd /opt/supernote-app
~~~

Install, import state, pull the OneDrive mirror, and enable scheduled jobs:

~~~bash
sudo deploy/install.sh \
  --port 8123 \
  --env /tmp/supernote-pi-import/app.env \
  --database /tmp/supernote-pi-import/db.sqlite3 \
  --archive /tmp/supernote-pi-import/ARCHIVE \
  --processed /tmp/supernote-pi-import/PROCESSED_NOTES \
  --rclone-config /tmp/supernote-pi-import/rclone.conf \
  --initial-sync \
  --enable-timers
~~~

Omit --processed when that directory does not exist. The ARM64 image is built locally, migrations run automatically, and the installer waits for health before starting the OneDrive pull.

Verify the result:

~~~bash
sudo /opt/supernote-app/deploy/doctor.sh
sudo docker compose \
  --env-file /srv/supernote-app/config/compose.env \
  -f /opt/supernote-app/compose.yaml logs --tail=200
~~~

Open http://PI_ADDRESS:8123/. Choose any unused host port with `--port`; the container continues to listen internally on port 80/8000 and only the host port is changed. The selected port is retained in `/srv/supernote-app/config/compose.env`.

To change the port later:

~~~bash
sudo sed -i 's/^APP_PORT=.*/APP_PORT=8124/' /srv/supernote-app/config/compose.env
sudo docker compose \
  --env-file /srv/supernote-app/config/compose.env \
  -f /opt/supernote-app/compose.yaml up -d
~~~

Open http://PI_ADDRESS:8124/.

After verification, disable scheduled synchronization on the heavy-lift system. Only one installation should push or automatically synchronize the same remote.

## Back Up And Restore

Backups contain SQLite, ARCHIVE, PROCESSED_NOTES, and configuration. They deliberately exclude the Supernote mirror.

~~~bash
sudo /opt/supernote-app/deploy/backup.sh
sudo /opt/supernote-app/deploy/restore.sh /srv/supernote-app/backups/BACKUP.tar.gz
~~~

Copy backups off the Pi periodically. A backup stored only on the Pi does not protect against storage failure.

## Update

Commit and push development changes from the heavy-lift system, then update the Pi:

~~~bash
sudo /opt/supernote-app/deploy/update.sh main
~~~

A tag can be supplied instead of main. The updater refuses dirty installations, creates a backup, records the previous Git revision, rebuilds the image, and waits for health. A failed update restores the previous revision and backup.

## Remove Or Roll Back

The uninstall script is run on the Raspberry Pi host as root. It is located in the host checkout at `/opt/supernote-app/deploy/uninstall.sh`; it is not inside the application container. It stops the containers, removes systemd units and the container's named static volume, then removes the disposable host checkout. It preserves `/srv/supernote-app` by default:

~~~bash
sudo /opt/supernote-app/deploy/uninstall.sh
~~~

Reinstalling later can reuse the retained state directory.

To stop the stack without removing code:

~~~bash
sudo systemctl disable --now supernote-sync.timer supernote-periodicals.timer
sudo docker compose \
  --env-file /srv/supernote-app/config/compose.env \
  -f /opt/supernote-app/compose.yaml down
~~~

Permanent data deletion is separate and requires typing the full state path:

~~~bash
sudo /opt/supernote-app/deploy/uninstall.sh --purge-data
~~~

Docker itself is never removed because it may be shared by other homelab services.
