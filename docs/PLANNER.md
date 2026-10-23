# Daily Planner

Integrated Django app at `/planner/`, above Notes. It uses the same database,
container, and storage as the file browser. KU Exchange support uses the container's
pinned EWS library; setup and acceptance are documented in [EXCHANGE.md](EXCHANGE.md).

## Current implementation and release gate

- Dated 12-month linked PDF, DK/US holidays, cached USNO moon data, weekly/monthly/
  yearly web navigation, birthday and notes pages.
- ISO week numbers appear on weekly and monthly pages. PDF month dates open the
  corresponding half of the week; labelled links return to months and switch week
  halves, adjacent weeks/months, and the year overview. Re-download the PDF when
  updating template navigation; existing notebook backgrounds do not update automatically.
- Owned event/task CRUD, Google OAuth/selection, incremental Calendar sync, Tasks
  reconciliation, durable operations, conflicts and operator-gated provider writes.
- Revocable device pairing, versioned sync receipts, handwriting preview upload,
  device acknowledgement, protected native notebooks and immutable mirror history.
- Device plugin built in the approved isolated x86_64 Docker builder on
  `rockpigeon`, under `~/Projects/SupernotePlugin`. Native package registration
  and bundle contents were verified. Installation and hardware acceptance remain required.

Do not describe the hardware workflow as verified until the acceptance checklist
below has passed. The backend deliberately defaults to **no Google writes**.

## Server deployment

Back up SQLite using its backup API and snapshot the device/cloud planner folder
before upgrading. Build the existing image, then apply the compose stack; the web
entrypoint runs migrations and collectstatic. The worker starts after web health.

```sh
docker compose build web
docker compose up -d
docker compose exec web python manage.py check
```

Use your deployment's existing compose environment file/STATE_DIR. Existing
installations need a Django user before planner login. This deployment has the
approved non-administrator `planner-owner` account. Retrieve its random password
locally with `docker exec --user 0 supernote-app-web-1 cat /config/planner-owner-login.txt`;
the file is root-only. No shared default password is installed. Existing file-browser URLs
retain their current access behavior.

Private HTTPS is included in the main compose stack. Set
`PLANNER_HTTPS_HOST` to the hostname/IP the A6X can reach and
`PLANNER_HTTPS_PORT` (default 8443). Add that host to `DJANGO_ALLOWED_HOSTS`
and the HTTPS origin to `DJANGO_CSRF_TRUSTED_ORIGINS` in app.env.

```sh
docker compose up -d
```

Caddy's CA and certificates persist under STATE_DIR/config/planner-caddy. Export
only its **public** `caddy/pki/authorities/local/root.crt` to trusted clients;
never copy `root.key`. Import/trust the public CA on the browser computer. Paste
that public CA PEM into the device plugin, which verifies both CA and hostname.
The plugin allows only the configured HTTPS origin and planner API endpoints.
HTTP stays available for local OAuth setup; no public DNS/Internet ingress needed.

For Tailscale access, set `PLANNER_TAILSCALE_HOST` to the node's full MagicDNS
name and `PLANNER_TAILSCALE_IP` to its Tailscale IPv4 address in `compose.env`.
Also add the full name to `DJANGO_ALLOWED_HOSTS` in `app.env`, then recreate the
web, worker and proxy containers. Caddy issues a private-CA certificate for that
name and redirects its HTTP requests to the same name, avoiding LAN-only URLs.
This deployment uses `https://urvogel.swallow-hake.ts.net:8443`. Trust the same
public Caddy CA on the connecting computer. Localhost HTTP remains available for OAuth.

## Google setup

Create a Google Cloud OAuth **web application** client, enable Calendar and Tasks
APIs, and register exactly `http://localhost:8123/planner/google/callback/`.
Configure in the protected deployment `app.env`:

For the deployed Tailscale setup, the callback is now
`https://urvogel.swallow-hake.ts.net:8443/planner/google/callback/`.
Register that exact URI with Google and authorize from the normal Tailscale app
address; no SSH tunnel is needed. The localhost example below remains an alternative
when configured consistently in Google and `app.env`.

```dotenv
PLANNER_GOOGLE_CLIENT_ID=your-client-id
PLANNER_GOOGLE_CLIENT_SECRET=your-client-secret
PLANNER_GOOGLE_REDIRECT_URI=http://localhost:8123/planner/google/callback/
PLANNER_PUBLIC_ORIGIN=https://192.168.8.206:8443
PLANNER_GOOGLE_WRITES_ENABLED=false
```

Restart web and planner-worker after changing env values. Use an SSH tunnel from
your personal computer, then open the app at localhost and sign in there before
clicking Connect Google:

```sh
ssh -L 8123:127.0.0.1:8123 YOUR_SERVER
```

OAuth uses state, PKCE, a ten-minute flow expiry and offline access. Refresh tokens
are encrypted in SQLite using a key derived from DJANGO_SECRET_KEY. Preserve that
secret with backups; changing it requires reconnecting Google. Tokens/credentials
do not go into the notebook, template, device protocol, or application logs.
Google external apps in Testing can expire these refresh tokens after seven days;
configure the consent screen for your intended personal deployment.

Choose visible calendars/task lists and writable defaults. Optional unique pen
aliases (e.g. `personal`, `work`) support `14:00 Dentist @personal`.
One Google connection per app user is supported. Apple Calendar can display this
Google account directly; iCloud Calendar and Apple Reminders are not integrated.

The worker polls every 60 seconds. Calendar page tokens commit only after complete
pagination; 410 clears the token and rebuilds without discarding pending edits.
Tasks use a two-minute timestamp overlap and full reconciliation every 15 minutes.
Undated Google Tasks are not assigned an invented planner date. Full all-day end
dates are normalized from Google's exclusive end-date representation.

## Conflicts and queued changes

The app saves changes before attempting remote writes. Local entries without a
destination remain local. Google event creates use deterministic IDs. Task creates
carry an application marker; retries search for it before creating another task.
ETag mismatches and simultaneous provider changes enter conflict review.

For a conflict, dismiss the proposal to let the next worker pull the provider's
current version, refresh, then reapply the desired edit. Never approve a stale
conflict directly. Shared/recurring changes require explicit occurrence review.
Unrecognized device captures can be corrected using the event/task form and then
dismissed. Erasure and missing page content do not cause remote deletion.

## A6X setup and acceptance

See `device-planner/README.md` for build/install and the pen grammar. Back up before
updating firmware **2.26.39 → 2.27.42**. Use a disposable notebook and selected test
calendar/task list first.

Planner settings provide authenticated downloads of the built plugin and public
CA certificate. The deployed plugin SHA-256 is
`30b41657d27072d2d0fd37659333dc97f364b37946dfca5c34c86f2bbe106a51` (plugin 0.1.8).
Version 0.1.8 fills blank day space with up to five event rows at the original
font size, respecting ink, day boundaries and navigation. No new PDF/notebook is
needed. It also refreshes overflow captions and handles cross-outs on expanded
rows before OCR.
Version 0.1.7 added bounded authenticated storage, staged verification
before replacing saved state, and bounded legacy reads. Install over the existing
plugin and restart the tablet; try **Sync Planner** first. If authentication still
fails, use confirmed recovery with the original notebook and pairing file.
Do not clear data, uninstall, or reinitialize. See the plugin README for recovery
limits and the remaining on-device Keystore acceptance check.

In settings click **Download pairing file**, then copy it to the tablet's
`Document` folder as exactly `supernote-planner-pairing.json`. In plugin 0.1.4
or newer tap **Import pairing file from Document**. It reads that named file
directly, without a file-picker callback, and imports the server address, planner ID, device credential, and public
CA certificate into encrypted plugin storage. The planner ID selects this dated
planner; the device credential authorizes this tablet. No private CA key or
Google credentials are included. Remove the file from shared/cloud storage after
importing. Version 0.1.4 stores encrypted state separately from installation files,
migrating any legacy state. If the app address is already displayed, re-import is
unnecessary. Read-only bridge operations have a named
timeout; late read results cannot overwrite saved connection data. Android
filesystem behavior remains part of the real-device acceptance checks.

Plugin 0.1.2 adds a fixed status panel, explicit e-ink view refresh, native result
dialogs and progress for initialization/sync. **Check notebook (read only)**
reports the open path, page count and whether initialization completed, without
changing the note. Run this first if the device appears to do nothing. The last
result is kept in encrypted plugin state across reopening the plugin.

Plugin 0.1.3 corrects SDK export names, native-object cache release and integer
TextBox rectangles. Real SDK 0.2.3 contract checks run in every Android build.
See [the plugin review](PLANNER_PLUGIN_REVIEW.md) for findings, regression
coverage and the remaining real-device checks.

1. Confirm plugin permissions, private HTTPS, pairing, and persistent encrypted state.
2. Import every generated PDF page in order into a standard notebook, saved under
   `Note/Planner/`. Initialize anchors and verify PDF links/page coordinates.
3. Insert/update/remove generated text; save/reopen/restart and OneDrive-sync.
   Verify element metadata survives and original strokes remain editable.
4. Reorder pages: identities must follow anchors. Copy/delete an anchor/page: sync
   must stop and request reconciliation, never delete provider events.
5. Write a timed event, all-day event and checkbox task; verify extraction/review.
6. Confirm strike-through detection targets only the intended generated event;
   ordinary handwriting, X marks, erasure and free notes must not delete events.
7. Confirm preview images include the intended handwritten content and click regions.
8. Interrupt network and restart during sync: retry must not duplicate or lose work.
9. Benchmark the full-year notebook on A6X; verify event overflow stays out of ink.

Once device-only tests pass, record acceptance and enable writes **only with test
calendars selected** for the final real-provider checks:

```sh
docker compose exec web python manage.py planner_accept_device DEVICE_ID \
  --confirm-roundtrip
```

The command requires an observed connection and acknowledged revision. Firmware
is optional: pass `--firmware VERSION` only when its installed value is known;
the command accepts A6X releases 2.27.42 and later without guessing a version.

Set `PLANNER_GOOGLE_WRITES_ENABLED=true`, restart web/worker, then test:

- Write event → sync → exactly one Google event.
- Cross it out → sync → remove exactly that event.
- Edit in Google or the app → sync → device updates without altering ink.
- Write/check task → Google Tasks creation/completion.
- Repeat sync/restart/offline recovery; verify no duplicate provider operations.

Select personal calendars only after passing. Revoking the device credential
immediately denies further sync and removes that device's write-gate eligibility.

## Storage and API

`/data/planner/` contains generated immutable PDFs/manifests, USNO snapshots,
versioned preview images and a worker lock. Native `.note` files remain device-owned.
Whole-tree rclone push excludes `/Note/Planner/**`; pull snapshots the existing
mirror into ARCHIVE/planner-versions before replacement. File-browser archive,
restore and recycle refuse active planner paths and their ancestors.

Device requests use `Authorization: Bearer DEVICE_CREDENTIAL`. Tokens are stored
only as SHA-256 digests on the server. Protocol version is 1:

```json
{"schema":1,"book_id":"UUID","acknowledged_revision":0,"operations":[
 {"id":"UUID","action":"capture","day":"2027-01-05","text":"14:00 Dentist"}
]}
```

POST `/planner/api/v1/sync/` accepts at most 100 operations, returning durable
receipts, manifest, complete semantic item state and server revision. Reusing an
operation UUID with different content is rejected. `cancel`/`complete` require
`item_id` and expected `version`, or `item_operation` referencing an earlier capture
receipt. Captures retain `source_region` to associate later pen cancellation with
the original handwriting. Acknowledgement is sent only after all managed
device pages have been applied. Preview PNGs POST to `/planner/api/v1/preview/`
with `X-Planner-Page`, `X-Planner-Revision`, and JSON `X-Planner-Regions` headers.

## Validation and rollback

```sh
python manage.py test planner files --noinput
node --test device-planner/core.test.mjs
python manage.py makemigrations --check --dry-run
```

Disable writes and stop the worker first when rolling back. Keep the current
database, pending operations, template editions, CA and DJANGO_SECRET_KEY backed
up. Roll back the image; restore the pre-migration DB only if abandoning newer
queued work deliberately. Never replace a live notebook with a template PDF or
an older mirror to roll back server code.
