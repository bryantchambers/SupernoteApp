"""Google REST adapter. No tokens in templates, device payloads, or error messages."""
import base64
import hashlib
import json
import secrets
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode, urlsplit
import requests
from cryptography.fernet import Fernet
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import Book, Calendar, Connection, Device, Item, Operation
from .services import bump

SCOPES = ['https://www.googleapis.com/auth/calendar.calendarlist.readonly', 'https://www.googleapis.com/auth/calendar.events', 'https://www.googleapis.com/auth/tasks']
CALENDAR = 'https://www.googleapis.com/calendar/v3'
TASKS = 'https://tasks.googleapis.com/tasks/v1'


class GoogleError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'Google returned HTTP {status}. Reconnect for 401/403; retry later for 429/5xx.')


def cipher():
    key = hashlib.sha256((settings.SECRET_KEY + ':planner-google-v1').encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def store_credentials(connection, values):
    connection.credentials = cipher().encrypt(json.dumps(values).encode()).decode()
    connection.save(update_fields=['credentials'])


def authorization(request):
    if settings.SECRET_KEY.startswith('django-insecure'):
        raise ValueError('Configure a private DJANGO_SECRET_KEY before connecting Google.')
    if not settings.PLANNER_GOOGLE_CLIENT_ID or not settings.PLANNER_GOOGLE_CLIENT_SECRET:
        raise ValueError('Configure the planner Google OAuth client in app.env first.')
    redirect_host = urlsplit(settings.PLANNER_GOOGLE_REDIRECT_URI).hostname
    request_host = urlsplit('//' + request.get_host()).hostname
    if redirect_host != request_host:
        raise ValueError('Connect Google from the registered callback host so sign-in state is preserved. For this local setup, open http://localhost:8123/planner/ through the SSH tunnel and sign in there first.')
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    request.session['planner_oauth'] = {'state': state, 'verifier': verifier, 'created': timezone.now().timestamp()}
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': settings.PLANNER_GOOGLE_CLIENT_ID, 'redirect_uri': settings.PLANNER_GOOGLE_REDIRECT_URI,
        'response_type': 'code', 'scope': ' '.join(SCOPES), 'state': state, 'access_type': 'offline', 'prompt': 'consent',
        'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='), 'code_challenge_method': 'S256'})


def finish_authorization(request):
    flow = request.session.pop('planner_oauth', {})
    if not flow or not secrets.compare_digest(request.GET.get('state', ''), flow.get('state', '')) or timezone.now().timestamp() - flow['created'] > 600:
        raise ValueError('Google sign-in expired. Start again.')
    response = requests.post('https://oauth2.googleapis.com/token', data={
        'client_id': settings.PLANNER_GOOGLE_CLIENT_ID, 'client_secret': settings.PLANNER_GOOGLE_CLIENT_SECRET,
        'redirect_uri': settings.PLANNER_GOOGLE_REDIRECT_URI, 'grant_type': 'authorization_code',
        'code': request.GET.get('code', ''), 'code_verifier': flow['verifier']}, timeout=30)
    if not response.ok:
        raise GoogleError(response.status_code)
    values = response.json()
    if not values.get('refresh_token'):
        raise ValueError('Google did not grant offline access. Reconnect and grant the requested permissions.')
    values['expires_at'] = timezone.now().timestamp() + values.get('expires_in', 3600)
    connection, _ = Connection.objects.get_or_create(owner=request.user)
    store_credentials(connection, values)
    discover(connection)


class Client:
    def __init__(self, connection):
        self.connection = connection

    def request(self, method, url, **kwargs):
        # Refresh under a short DB transaction so web/worker cannot rotate credentials concurrently.
        with transaction.atomic():
            connection = Connection.objects.select_for_update().get(pk=self.connection.pk)
            if not connection.credentials:
                raise ValueError('Connect Google first.')
            values = json.loads(cipher().decrypt(connection.credentials.encode()))
            if values.get('expires_at', 0) < timezone.now().timestamp() + 60:
                refreshed = requests.post('https://oauth2.googleapis.com/token', data={
                    'client_id': settings.PLANNER_GOOGLE_CLIENT_ID, 'client_secret': settings.PLANNER_GOOGLE_CLIENT_SECRET,
                    'refresh_token': values['refresh_token'], 'grant_type': 'refresh_token'}, timeout=30)
                if not refreshed.ok:
                    raise GoogleError(refreshed.status_code)
                values.update(refreshed.json())
                values['expires_at'] = timezone.now().timestamp() + values.get('expires_in', 3600)
                store_credentials(connection, values)
        headers = kwargs.pop('headers', {})
        response = requests.request(method, url, headers={**headers, 'Authorization': f"Bearer {values['access_token']}"}, timeout=30, **kwargs)
        if not response.ok:
            raise GoogleError(response.status_code)
        return response.json() if response.content else {}

    def pages(self, url, params=None):
        params = dict(params or {})
        while True:
            data = self.request('GET', url, params=params)
            yield data
            if not data.get('nextPageToken'):
                return
            params['pageToken'] = data['nextPageToken']


def discover(connection):
    client = Client(connection)
    for kind, url in [('event', CALENDAR + '/users/me/calendarList'), ('task', TASKS + '/users/@me/lists')]:
        seen = []
        for page in client.pages(url):
            for raw in page.get('items', []):
                seen.append(raw['id'])
                Calendar.objects.update_or_create(connection=connection, remote_id=raw['id'], kind=kind, defaults={
                    'title': raw.get('summary', raw.get('title', 'Untitled'))[:250],
                    'writable': kind == 'task' or raw.get('accessRole') in ('writer', 'owner')})
        Calendar.objects.filter(connection=connection, kind=kind).exclude(remote_id__in=seen).update(selected=False, writable=False, is_default=False)


def list_url(cal):
    remote = quote(cal.remote_id, safe='')
    return f'{CALENDAR}/calendars/{remote}/events' if cal.kind == 'event' else f'{TASKS}/lists/{remote}/tasks'


def remote_values(raw, kind):
    if kind == 'task':
        if not raw.get('due'):
            return None  # Undated tasks remain available in their provider, without inventing a day.
        return {'title': raw.get('title', 'Untitled')[:250], 'day': date.fromisoformat(raw['due'][:10]), 'start_time': None, 'end_time': None, 'end_day': None,
                'completed': raw.get('status') == 'completed', 'needs_review': False}
    start, end = raw.get('start', {}), raw.get('end', {})
    if 'date' in start:
        day = date.fromisoformat(start['date'])
        end_day = date.fromisoformat(end.get('date', start['date'])) - timedelta(days=1)
        fields = {'day': day, 'end_day': max(day, end_day), 'start_time': None, 'end_time': None}
    elif 'dateTime' in start and 'dateTime' in end:
        # Converted into each book's local zone in import_record.
        fields = {'day': datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00')).date(), 'end_day': None, 'start_time': None, 'end_time': None}
    else:
        return None
    return {**fields, 'title': raw.get('summary', 'Untitled')[:250], 'completed': False,
            'needs_review': bool(raw.get('attendees') or raw.get('recurrence') or raw.get('recurringEventId'))}


def import_record(cal, raw):
    from zoneinfo import ZoneInfo
    cancelled = raw.get('status') == 'cancelled' or raw.get('deleted', False)
    if cancelled and cal.kind == 'event':
        for instance in Item.objects.filter(calendar=cal, remote_snapshot__recurringEventId=raw['id'], deleted=False):
            if Operation.objects.filter(item=instance, status__in=['pending', 'review', 'conflict']).exists():
                Operation.objects.filter(item=instance, status='pending').update(status='conflict', message='Google cancelled this recurring series.')
            else:
                instance.deleted = True
                instance.version += 1
                instance.save()
                bump(instance.book)
    for book in Book.objects.filter(owner=cal.connection.owner):
        existing = Item.objects.filter(book=book, calendar=cal, remote_id=raw['id']).first()
        if existing and existing.etag == raw.get('etag') and raw.get('etag'):
            continue
        if existing and Operation.objects.filter(item=existing, status__in=['pending', 'review', 'conflict']).exists():
            if existing.remote_snapshot != raw:
                Operation.objects.filter(item=existing, status='pending').update(status='conflict', message='Google changed this entry while a local change was pending.')
            continue
        if cancelled:
            if existing and not existing.deleted:
                existing.deleted = True
                existing.version += 1
                existing.etag = raw.get('etag', '')
                existing.remote_snapshot = raw
                existing.save()
                bump(book)
            continue
        values = remote_values(raw, cal.kind)
        if not values:
            if existing and not existing.deleted:
                existing.deleted = True
                existing.remote_snapshot = raw
                existing.etag = raw.get('etag', '')
                existing.version += 1
                existing.save()
                bump(book)
            continue
        if cal.kind == 'event' and 'dateTime' in raw.get('start', {}):
            zone = ZoneInfo(book.timezone)
            a = datetime.fromisoformat(raw['start']['dateTime'].replace('Z', '+00:00')).astimezone(zone)
            b = datetime.fromisoformat(raw['end']['dateTime'].replace('Z', '+00:00')).astimezone(zone)
            values.update(day=a.date(), start_time=a.time(), end_time=b.time(), end_day=b.date() if a.date() != b.date() else None)
        in_range = book.start <= values['day'] < date.fromisoformat(book.manifest['end'])
        if not in_range and not existing:
            continue
        values.update(deleted=not in_range, remote_snapshot=raw, etag=raw.get('etag', ''))
        if existing:
            if all(getattr(existing, key) == val for key, val in values.items()):
                continue
            for key, val in values.items():
                setattr(existing, key, val)
            existing.version += 1
            existing.save()
        else:
            Item.objects.create(book=book, calendar=cal, kind=cal.kind, remote_id=raw['id'], **values)
        bump(book)


def pull(cal):
    client = Client(cal.connection)
    full = cal.kind == 'event' and not cal.sync_token or cal.kind == 'task' and (not cal.last_full_at or cal.last_full_at < timezone.now() - timedelta(minutes=15))
    if cal.kind == 'event':
        # Expand instances over the owned books' range. A full reconcile handles recurrence,
        # moved instances and deletion; Tasks has a separate reconciliation policy.
        books = list(Book.objects.filter(owner=cal.connection.owner))
        if not books:
            return
        first = min(b.start for b in books) - timedelta(days=2)
        last = max(date.fromisoformat(b.manifest['end']) for b in books) + timedelta(days=2)
        params = {'singleEvents': 'true', 'showDeleted': 'true', 'timeMin': first.isoformat() + 'T00:00:00Z', 'timeMax': last.isoformat() + 'T00:00:00Z', 'maxResults': 2500}
        if not full:
            params = {'singleEvents': 'true', 'showDeleted': 'true', 'syncToken': cal.sync_token, 'maxResults': 2500}
    else:
        params = {'showDeleted': 'true', 'showCompleted': 'true', 'showHidden': 'true', 'maxResults': 100}
        if not full and cal.watermark:
            params['updatedMin'] = (cal.watermark - timedelta(minutes=2)).isoformat()
    # Do not reconcile partial pagination after a network failure.
    try:
        pages = list(client.pages(list_url(cal), params))
    except GoogleError as exc:
        if exc.status == 410 and cal.kind == 'event' and cal.sync_token:
            cal.sync_token = ''
            cal.save(update_fields=['sync_token'])
            return pull(cal)
        raise
    records = [raw for page in pages for raw in page.get('items', [])]
    seen = [raw['id'] for raw in records]
    with transaction.atomic():
        for raw in records:
            import_record(cal, raw)
        for item in Item.objects.filter(calendar=cal, deleted=False).exclude(remote_id='').exclude(remote_id__in=seen) if full else []:
            if not Operation.objects.filter(item=item, status__in=['pending', 'review', 'conflict']).exists():
                item.deleted = True
                item.version += 1
                item.save()
                bump(item.book)
        cal.watermark = timezone.now()
        if cal.kind == 'event':
            cal.sync_token = pages[-1].get('nextSyncToken', '') if pages else ''
        if full:
            cal.last_full_at = timezone.now()
        cal.save(update_fields=['watermark', 'sync_token', 'last_full_at'])


def push(op):
    item = Item.objects.select_related('book', 'calendar__connection').get(pk=op.item_id)
    if not item.calendar:
        op.status, op.message = 'local', 'Saved in this planner; no Google destination selected.'
        op.save()
        return
    if op.payload.get('version') != item.version:
        op.status = 'superseded'
        op.save(update_fields=['status'])
        return
    cal = item.calendar
    if not cal.selected or not cal.writable:
        raise ValueError('This calendar/task list is no longer selected and writable.')
    if not settings.PLANNER_GOOGLE_WRITES_ENABLED or not Device.objects.filter(book=item.book, validated=True, revoked=False).exists():
        raise ValueError('Calendar writes are gated until device acceptance and explicit deployment enablement.')
    if item.needs_review and not op.payload.get('review_approved'):
        op.status, op.message = 'review', 'Recurring or shared entry: confirm this occurrence in the review drawer.'
        op.save()
        return
    client, url = Client(cal.connection), list_url(cal)
    headers = {'If-Match': item.etag} if item.etag else {}
    if item.deleted:
        if item.remote_id:
            client.request('DELETE', url + '/' + quote(item.remote_id, safe=''), headers=headers,
                **({'params': {'sendUpdates': 'all'}} if item.kind == 'event' and item.remote_snapshot.get('attendees') else {}))
        raw = {}
    else:
        if item.kind == 'event':
            from .recognition import valid_local
            if item.start_time:
                start = {'dateTime': valid_local(item.day, item.start_time, item.book.timezone).isoformat(), 'timeZone': item.book.timezone}
                end = {'dateTime': valid_local(item.end_day or item.day, item.end_time, item.book.timezone).isoformat(), 'timeZone': item.book.timezone}
            else:
                start, end = {'date': item.day.isoformat()}, {'date': ((item.end_day or item.day) + timedelta(days=1)).isoformat()}
            body = {'summary': item.title, 'start': start, 'end': end}
            if not item.remote_id:
                body['id'] = item.id.hex  # Valid base32hex; stable across ambiguous create retries.
        else:
            marker = f'supernote-planner:{item.id}'
            body = {'title': item.title, 'due': item.day.isoformat() + 'T00:00:00.000Z', 'status': 'completed' if item.completed else 'needsAction'}
            if not item.remote_id:
                body['notes'] = marker
                if op.attempts > 1:
                    matches = [r for page in client.pages(url, {'showCompleted': 'true', 'showHidden': 'true'}) for r in page.get('items', []) if r.get('notes') == marker]
                    if matches:
                        item.remote_id, item.etag = matches[0]['id'], matches[0].get('etag', '')
                        headers = {'If-Match': item.etag} if item.etag else {}
        try:
            raw = client.request('PATCH' if item.remote_id else 'POST', url + ('/' + quote(item.remote_id, safe='') if item.remote_id else ''), json=body, headers=headers)
        except GoogleError as exc:
            if exc.status == 409 and not item.remote_id and item.kind == 'event':
                raw = client.request('GET', url + '/' + item.id.hex)
                if raw.get('summary') != item.title or raw.get('start') != body['start']:
                    raise ValueError('Existing Google event differs after a create retry. Review before updating.')
            else:
                raise
    with transaction.atomic():
        latest = Item.objects.get(pk=item.pk)
        latest.remote_id = raw.get('id', item.remote_id)
        latest.etag = raw.get('etag', item.etag)
        latest.remote_snapshot = raw or latest.remote_snapshot
        latest.save(update_fields=['remote_id', 'etag', 'remote_snapshot'])
        op.status, op.message = 'synced', ''
        op.save(update_fields=['status', 'message'])


def sync_connection(connection):
    for cal in Calendar.objects.filter(connection=connection, selected=True):
        pull(cal)
    for op in Operation.objects.filter(book__owner=connection.owner, status='pending').select_related('item').order_by('created_at'):
        if not op.item_id:
            continue
        # A single planner worker holds the process-level lock around this routine.
        op.attempts += 1
        op.save(update_fields=['attempts'])
        try:
            push(op)
        except GoogleError as exc:
            if exc.status in (404, 410) and op.item.deleted:
                op.status, op.message = 'synced', ''
            elif exc.status == 412:
                op.status, op.message = 'conflict', 'Google has a newer revision. Reload and resolve this entry.'
            elif exc.status in (401, 403, 404):
                op.status, op.message = 'review', 'Google rejected this edit. Reconnect or check calendar permissions before retrying.'
            else:
                op.message = str(exc)
            op.save()
        except (ValueError, requests.RequestException) as exc:
            op.message = str(exc)[:200] if isinstance(exc, ValueError) else 'Network error; queued for retry.'
            op.save(update_fields=['message'])
    connection.checked_at, connection.message = timezone.now(), 'Google sync completed.'
    connection.save(update_fields=['checked_at', 'message'])
