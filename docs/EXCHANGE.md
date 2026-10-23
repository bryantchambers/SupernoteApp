# KU Exchange planner connection

The existing planner server now supports one Google and one KU Exchange connection
per owner. Google calendars/tasks and their tokens remain intact. Exchange is
calendar-only: ordinary nonrecurring appointments without attendees are writable;
meetings and recurring occurrences are displayed read only. No Microsoft To Do,
mail, attendee editing, RSVP, series editing, or Mac helper is included.

## Setup

1. Open the planner using its HTTPS address, log in, and open **Connections & settings**.
2. Choose **KU Exchange → Connect work calendar**. Enter the KU email address,
   Exchange login name and password. The login may be a KU username or domain-qualified
   name rather than the mailbox address. Enter credentials only in this form.
3. **Save credential & test connection** verifies access and discovers the default
   calendar and its calendar subfolders. Select the desired calendar, optionally
   set its unique pen alias to `work`, and save the calendar selection. No calendar
   is automatically made the default.
4. Events import in the worker's next cycle (normally 60 seconds). Cached work
   events remain available during outages. Refresh the page after an import and
   run **Sync Planner** on the tablet for its current overlay.

The fixed, verified-TLS endpoint is `https://mail.ku.dk/EWS/Exchange.asmx`, which
advertised NTLM on 23 October 2026. Public endpoint availability does not prove
mailbox access; setup must verify the actual KU login. EWS authentication may be
refused by KU policy even though Apple Calendar works. The app does not bypass
SSO/MFA or automatically try other authentication methods.

The orchestrator provisions `exchangelib==5.6.0` and pinned dependencies through
the container requirements. No host packages are installed. Autodiscover is off;
the connector does not accept arbitrary credential destinations. Credentials are
encrypted with a provider-specific key derived from `DJANGO_SECRET_KEY`. Keep the
secret key and database backup protected; changing the key requires reconnecting.
No Exchange credentials enter tablet data, templates or error messages. An HTTPS
POST, authenticated session and CSRF token are required to save a password.

## Write acceptance

`PLANNER_EXCHANGE_WRITES_ENABLED` defaults to `false`, independently of Google's
existing write flag. Reads can run while writes remain queued. Each Exchange
connection additionally needs a successful operator write check; replacing its
credential clears that acceptance.

After a real read and calendar selection, inspect the owner's Exchange calendar
ID through the Django shell (do not print credentials), then run:

```sh
docker exec supernote-app-web-1 python manage.py planner_test_exchange CALENDAR_ID --confirm-create
```

This explicitly creates one appointment named **Supernote Planner connection test**
tomorrow at 10:00 Copenhagen time, reads it, updates its title and moves it to
Deleted Items. It adds no attendees and sends no invitations. A unique persistent
property is used to find it for cleanup, including after an uncertain create.
Check Outlook if cleanup cannot be verified. Only a successful complete check
records `write_verified_at`; the command does not enable routine writes.

Then set `PLANNER_EXCHANGE_WRITES_ENABLED=true` in the protected deployment
`/config/app.env` and recreate web and planner-worker using the existing deployment
compose variables. With a previously validated A6X, verify a disposable handwritten
`14:00–15:00 Project work @work` and its cancellation in KU/Apple Calendar. Disable
the flag again if the end-to-end acceptance fails. No plugin update, new notebook,
Google reconnection or certificate change is required.

## Sync and failure behavior

- EWS change cursors are stored per calendar. Changed folders trigger expanded
  calendar views across the owned planners' date range, including occurrences.
  Dense views split into smaller ranges; incomplete fetches never reconcile
  missing events or advance the saved cursor. Expired cursors restart safely.
- Remote snapshots contain calendar fields only: no mail bodies or passwords.
  Remote IDs, UIDs, occurrence start and ChangeKeys support identity/conflicts.
- A process lock serializes Exchange setup, tests, disconnects and worker sync.
  Provider routing prevents either connector processing the other's outbox.
- A persistent planner UUID on appointments recovers ambiguous creates; a changed
  or duplicated marker requires review. Updates use ChangeKeys and NeverOverwrite.
  Deleted appointments move to Deleted Items, without meeting notifications.
- Bad authentication suspends automatic attempts. Replace the credential and
  test explicitly. Other failures back off from 60 seconds to one hour, retaining
  queued changes. A failed Exchange cycle does not stop Google sync.
- **Disconnect Exchange** clears its stored credential and write permissions;
  cached selected events and pending edits remain. Reconnecting the same mailbox
  restores discoverable permissions. Switching to another mailbox is unsupported.

## Deployment and tests

Back up SQLite using its backup API and protect the configuration backup before
deploying. Migration 0004 preserves the existing Google connection, calendar and
item IDs and encrypted token blob while allowing a separate Exchange row. Deploy
web and planner-worker together; keep the Exchange write flag false initially.
Collectstatic runs through the existing entrypoint.

Run tests in disposable containers with no live data/config mounts. Coverage
includes legacy migration preservation, both providers with colliding remote IDs,
handwritten aliases, calendar ownership, encrypted credentials, HTTPS enforcement,
all-day dates, occurrences, cursor failures, partial imports, stale revisions,
meeting restrictions, duplicate markers, trash deletion, backoff and disconnect.
Live mailbox and hardware acceptance require the owner's KU sign-in and tablet.
