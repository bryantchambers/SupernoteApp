"""KU EWS adapter. Calendar-only access; no authentication material in snapshots."""
import base64
import hashlib
import json
import fcntl
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from exchangelib import Account, CalendarItem, Configuration, Credentials, DELEGATE, EWSDateTime, EWSTimeZone, ExtendedProperty, NTLM
from exchangelib.errors import (
    ErrorAccessDenied, ErrorInvalidChangeKey, ErrorInvalidSyncStateData,
    ErrorItemNotFound, ErrorIrresolvableConflict, ErrorServerBusy, UnauthorizedError,
    ErrorCalendarViewRangeTooBig, ErrorExceededFindCountLimit,
)
from exchangelib.folders import Calendar as EWSCalendar
from exchangelib.properties import CalendarView
from exchangelib.protocol import FailFast, Protocol
from exchangelib.services import FindItem

from .models import Book, Calendar, Connection, Device, Item, Operation
from .recognition import valid_local
from .services import bump
from .artifacts import root
from .providers import writes_enabled

Protocol.TIMEOUT = 30
PENDING = ['pending', 'review', 'conflict']
FIELDS = ['subject', 'start', 'end', 'is_all_day', 'is_meeting', 'is_recurring', 'type',
          'uid', 'original_start', 'is_cancelled', 'planner_id', 'effective_rights']


class PlannerId(ExtendedProperty):
    property_set_id = '9ec10a0f-e722-46b4-861c-238d296223bb'
    property_name = 'SupernotePlannerId'
    property_type = 'String'


CalendarItem.register('planner_id', PlannerId)


class ExchangeError(ValueError):
    pass


class ExchangeAuthError(ExchangeError):
    pass


class IncompleteView(ExchangeError):
    pass


@contextmanager
def connection_lock(connection):
    with (root() / f'exchange-{connection.pk}.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExchangeError('Exchange is busy syncing. Try again shortly.') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class CompleteFindItem(FindItem):
    @staticmethod
    def _get_paging_values(element):
        # CalendarView cannot safely use ordinary indexed paging. Split the
        # time range instead of treating a truncated response as a full import.
        if element.get('IncludesLastItemInRange', '').lower() != 'true':
            raise IncompleteView('Exchange returned an incomplete calendar view.')
        return int(element.get('TotalItemsInView', '0')), None


def cipher():
    key = hashlib.sha256((settings.SECRET_KEY + ':planner-exchange-v1').encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def credentials(connection):
    if connection.provider != 'exchange' or not connection.credentials:
        raise ExchangeError('Connect KU Exchange first.')
    try:
        return json.loads(cipher().decrypt(connection.credentials.encode()))
    except (InvalidToken, ValueError, KeyError):
        raise ExchangeAuthError('Saved Exchange credentials cannot be read. Replace the credential.') from None


def store_credentials(connection, values):
    if connection.provider != 'exchange':
        raise ExchangeError('This is not an Exchange connection.')
    if settings.SECRET_KEY.startswith('django-insecure'):
        raise ExchangeError('Configure a private DJANGO_SECRET_KEY before connecting Exchange.')
    connection.credentials = cipher().encrypt(json.dumps(values).encode()).decode()
    connection.suspended, connection.retry_at, connection.failures = True, None, 0
    connection.write_verified_at = None
    connection.message = 'Credential saved. Test the connection before syncing.'
    connection.save()


class Client:
    def __init__(self, connection):
        values = credentials(connection)
        self.account = Account(primary_smtp_address=values['mailbox'], autodiscover=False,
            config=Configuration(service_endpoint=settings.PLANNER_EXCHANGE_ENDPOINT, auth_type=NTLM,
                credentials=Credentials(username=values['username'], password=values['password']),
                retry_policy=FailFast(), max_connections=1),
            access_type=DELEGATE, default_timezone=EWSTimeZone('Europe/Copenhagen'))

    def folders(self):
        primary = self.account.calendar
        folders = [primary] + list(primary.walk())
        for folder in folders:
            if isinstance(folder, EWSCalendar):
                folder.refresh()
                yield folder

    def folder(self, calendar):
        # Folder.account is read-only and is resolved through its root.
        folder = EWSCalendar(root=self.account.root, id=calendar.remote_id)
        folder.refresh()
        return folder

    def changes(self, folder, state):
        folder.item_sync_state = None
        changed = False
        try:
            for change in folder.sync_items(sync_state=state or None, only_fields=['subject']):
                changed = True
        except ErrorInvalidSyncStateData:
            folder.item_sync_state = None
            for change in folder.sync_items(only_fields=['subject']):
                pass
            changed = True
        if not folder.item_sync_state:
            raise ExchangeError('Exchange did not finish the change scan. Previous entries were retained.')
        return changed, folder.item_sync_state

    def window(self, folder, start, end, depth=0):
        try:
            ids = list(CompleteFindItem(account=self.account).call(
                folders=[folder], additional_fields=None, restriction=None, order_fields=None,
                shape='IdOnly', query_string=None, depth='Shallow',
                calendar_view=CalendarView(start=EWSDateTime.from_datetime(start), end=EWSDateTime.from_datetime(end)),
                max_items=None, offset=0))
        except (IncompleteView, ErrorCalendarViewRangeTooBig, ErrorExceededFindCountLimit):
            if depth >= 16 or end - start <= timedelta(minutes=1):
                raise ExchangeError('The calendar is too dense to import completely. Previous entries were retained.')
            midpoint = start + (end - start) / 2
            return self.window(folder, start, midpoint, depth + 1) + self.window(folder, midpoint, end, depth + 1)
        for value in ids:
            if isinstance(value, Exception):
                raise value
        records = list(self.account.fetch(ids=ids, folder=folder, only_fields=FIELDS)) if ids else []
        for value in records:
            if isinstance(value, Exception):
                raise value
        return records

    def get(self, folder, remote_id):
        return folder.all().only(*FIELDS).get(id=remote_id)

    def markers(self, folder, marker):
        return list(folder.filter(planner_id=marker).only(*FIELDS))


def writable(folder):
    rights = folder.effective_rights
    return bool(rights and rights.create_contents and rights.modify and rights.delete)


def discover(connection):
    folders = list(Client(connection).folders())
    with transaction.atomic():
        seen = []
        for folder in folders:
            seen.append(folder.id)
            Calendar.objects.update_or_create(connection=connection, remote_id=folder.id, kind='event',
                defaults={'title': folder.name[:250], 'writable': writable(folder)})
        Calendar.objects.filter(connection=connection).exclude(remote_id__in=seen).update(selected=False, writable=False, is_default=False)
        connection.suspended, connection.failures, connection.retry_at = False, 0, None
        connection.checked_at, connection.message = timezone.now(), 'KU Exchange connected. Select your work calendar.'
        connection.save()


def read_only(raw):
    rights = raw.effective_rights
    return bool(raw.is_meeting or raw.is_recurring or raw.type != 'Single' or
                not rights or not rights.modify or not rights.delete)


def normalized(raw):
    return {'id': raw.id, 'etag': raw.changekey, 'uid': raw.uid or '',
            'original_start': raw.original_start.isoformat() if raw.original_start else '',
            'title': (raw.subject or 'Untitled')[:250], 'start': raw.start.isoformat(), 'end': raw.end.isoformat(),
            'all_day': bool(raw.is_all_day), 'read_only': read_only(raw), 'cancelled': bool(raw.is_cancelled),
            'planner_id': raw.planner_id or ''}


def import_record(cal, raw):
    for book in Book.objects.filter(owner=cal.connection.owner):
        existing = Item.objects.filter(book=book, calendar=cal, remote_id=raw['id']).first()
        if not existing and raw['planner_id']:
            # A create may have reached Exchange before its response was lost.
            # Recover the original local item rather than importing a duplicate.
            try:
                marker = uuid.UUID(raw['planner_id'])
            except ValueError:
                marker = None
            if marker:
                existing = Item.objects.filter(book=book, calendar=cal, pk=marker).first()
        if not existing and raw['uid']:
            existing = Item.objects.filter(book=book, calendar=cal, remote_snapshot__uid=raw['uid'],
                                           remote_snapshot__original_start=raw['original_start']).first()
        if existing and existing.etag == raw['etag'] and existing.remote_id == raw['id'] and existing.etag:
            continue
        if existing and Operation.objects.filter(item=existing, status__in=PENDING).exists():
            if existing.remote_snapshot and existing.remote_snapshot != raw:
                Operation.objects.filter(item=existing, status='pending').update(status='conflict', message='Exchange changed this entry while a local edit was pending.')
            # Keep the old ChangeKey for conflict detection; create recovery is
            # performed by the marker lookup in push, without overwriting ink.
            continue
        start = datetime.fromisoformat(raw['start']).astimezone(ZoneInfo(book.timezone))
        end = datetime.fromisoformat(raw['end']).astimezone(ZoneInfo(book.timezone))
        day = start.date()
        in_range = book.start <= day < date.fromisoformat(book.manifest['end'])
        if not existing and not in_range:
            continue
        values = {'title': raw['title'], 'day': day, 'start_time': None if raw['all_day'] else start.time(),
                  'end_time': None if raw['all_day'] else end.time(),
                  'end_day': max(day, end.date() - timedelta(days=1)) if raw['all_day'] else end.date() if end.date() != day else None,
                  'deleted': raw['cancelled'] or not in_range, 'needs_review': raw['read_only'],
                  'remote_id': raw['id'], 'etag': raw['etag'], 'remote_snapshot': raw}
        if existing:
            for name, value in values.items():
                setattr(existing, name, value)
            existing.version += 1
            existing.save()
        else:
            Item.objects.create(book=book, calendar=cal, kind='event', **values)
        bump(book)


def pull(cal, client):
    books = list(Book.objects.filter(owner=cal.connection.owner))
    if not books:
        return
    start = datetime.combine(min(b.start for b in books) - timedelta(days=2), time(), dt_timezone.utc)
    end = datetime.combine(max(date.fromisoformat(b.manifest['end']) for b in books) + timedelta(days=2), time(), dt_timezone.utc)
    signature = f'{start.isoformat()}/{end.isoformat()}'
    folder = client.folder(cal)
    cursor = json.loads(cal.sync_token) if cal.sync_token else {}
    changed, state = client.changes(folder, cursor.get('state', ''))
    if changed or cursor.get('window') != signature or not cal.last_full_at:
        records = {r.id: normalized(r) for r in client.window(folder, start, end)}
        with transaction.atomic():
            for raw in records.values():
                import_record(cal, raw)
            for item in Item.objects.filter(calendar=cal, deleted=False).exclude(remote_id='').exclude(remote_id__in=records):
                if Operation.objects.filter(item=item, status__in=PENDING).exists():
                    Operation.objects.filter(item=item, status='pending').update(status='conflict', message='Exchange removed or moved this entry while a local edit was pending.')
                else:
                    item.deleted, item.version = True, item.version + 1
                    item.save()
                    bump(item.book)
            cal.last_full_at = timezone.now()
            cal.sync_token, cal.watermark = json.dumps({'state': state, 'window': signature}), timezone.now()
            cal.save(update_fields=['sync_token', 'last_full_at', 'watermark'])
    else:
        cal.sync_token, cal.watermark = json.dumps({'state': state, 'window': signature}), timezone.now()
        cal.save(update_fields=['sync_token', 'watermark'])


def appointment_fields(item):
    if item.start_time:
        start = valid_local(item.day, item.start_time, item.book.timezone)
        end = valid_local(item.end_day or item.day, item.end_time, item.book.timezone)
    else:
        start = datetime.combine(item.day, time(), ZoneInfo(item.book.timezone))
        end = datetime.combine((item.end_day or item.day) + timedelta(days=1), time(), ZoneInfo(item.book.timezone))
    return {'subject': item.title, 'start': EWSDateTime.from_datetime(start), 'end': EWSDateTime.from_datetime(end),
            'is_all_day': item.start_time is None}


def push(op, client):
    item = Item.objects.select_related('book', 'calendar__connection').get(pk=op.item_id)
    if item.calendar.connection.provider != 'exchange':
        raise ExchangeError('This is not an Exchange operation.')
    if op.payload.get('version') != item.version:
        op.status = 'superseded'
        op.save(update_fields=['status'])
        return
    if not writes_enabled(item.calendar) or not Device.objects.filter(book=item.book, validated=True, revoked=False).exists():
        raise ExchangeError('Exchange writes await connection and device acceptance.')
    if item.kind != 'event' or item.provider_read_only:
        raise ExchangeError('Change Exchange meetings and recurring entries in Outlook.')
    cal = item.calendar
    if not cal.selected or not cal.writable:
        raise ExchangeError('This work calendar is read only or no longer selected.')
    folder = client.folder(cal)
    if not writable(folder):
        raise ErrorAccessDenied('Calendar permissions changed.')
    remote = client.get(folder, item.remote_id) if item.remote_id else None
    if not remote:
        matches = client.markers(folder, str(item.pk))
        if len(matches) > 1:
            raise ExchangeError('Duplicate planner markers found in Exchange. Review in Outlook.')
        remote = matches[0] if matches else None
        if remote:
            expected = appointment_fields(item)
            # When a create response was lost, only adopt an identical original
            # appointment. A subsequent remote edit must never be overwritten.
            if any(getattr(remote, name) != value for name, value in expected.items()):
                raise ErrorIrresolvableConflict('Exchange changed an appointment after creation.')
    if remote and read_only(remote):
        raise ExchangeError('This entry is now a meeting or recurring event. Change it in Outlook.')
    if remote and item.etag and remote.changekey != item.etag:
        raise ErrorIrresolvableConflict('Exchange has a newer appointment revision.')
    if item.deleted:
        if remote:
            remote.move_to_trash(send_meeting_cancellations='SendToNone')
        raw = None
    else:
        values = appointment_fields(item)
        if remote:
            # Only these fields may change; attendee/body/recurrence data is never written.
            for name, value in values.items():
                setattr(remote, name, value)
            remote.save(update_fields=list(values), conflict_resolution='NeverOverwrite', send_meeting_invitations='SendToNone')
        else:
            remote = CalendarItem(account=client.account, folder=folder, planner_id=str(item.pk), **values)
            remote.save(send_meeting_invitations='SendToNone')
        remote.refresh()
        raw = normalized(remote)
    with transaction.atomic():
        latest = Item.objects.get(pk=item.pk)
        if raw:
            latest.remote_id, latest.etag, latest.remote_snapshot = raw['id'], raw['etag'], raw
            latest.save(update_fields=['remote_id', 'etag', 'remote_snapshot'])
        op.status, op.message = 'synced', ''
        op.save(update_fields=['status', 'message'])


def record_failure(connection, error):
    connection.checked_at = timezone.now()
    if isinstance(error, (UnauthorizedError, InvalidToken, ExchangeAuthError)):
        connection.suspended = True
        connection.message = 'KU sign-in failed. Automatic attempts stopped; replace the credential and test again.'
    else:
        connection.failures += 1
        delay = min(3600, 60 * 2 ** min(connection.failures - 1, 6))
        if isinstance(error, ErrorServerBusy):
            delay = max(delay, min(3600, error.back_off or 60))
        connection.retry_at = timezone.now() + timedelta(seconds=delay)
        connection.message = 'Exchange is unavailable or access was refused. Pending changes retained; retry scheduled.'
    connection.save()


def sync_connection(connection):
    try:
        with connection_lock(connection):
            connection.refresh_from_db()
            _sync_connection(connection)
    except ExchangeError:
        # Another web/worker operation owns the connection; retry next cycle.
        return


def _sync_connection(connection):
    if connection.provider != 'exchange' or connection.suspended or not connection.credentials:
        return
    if connection.retry_at and connection.retry_at > timezone.now():
        return
    try:
        client = Client(connection)
        for cal in Calendar.objects.filter(connection=connection, selected=True):
            pull(cal, client)
        if settings.PLANNER_EXCHANGE_WRITES_ENABLED and connection.write_verified_at:
            for op in Operation.objects.filter(item__calendar__connection=connection, status='pending').select_related('item', 'book').order_by('created_at'):
                if not Device.objects.filter(book=op.book, validated=True, revoked=False).exists():
                    continue
                op.attempts += 1
                op.save(update_fields=['attempts'])
                try:
                    push(op, client)
                except (ErrorInvalidChangeKey, ErrorIrresolvableConflict):
                    op.status, op.message = 'conflict', 'Exchange has a newer revision. Dismiss to reload before reapplying.'
                    op.save()
                except ErrorItemNotFound:
                    op.status, op.message = ('synced', '') if op.item.deleted else ('conflict', 'Exchange removed this appointment. Reload before reapplying.')
                    op.save()
                except (ExchangeError, ErrorAccessDenied):
                    op.status, op.message = 'review', 'Exchange edit blocked. Check calendar permissions; meetings and recurring entries must be changed in Outlook.'
                    op.save()
        connection.checked_at, connection.message = timezone.now(), 'KU Exchange sync completed.'
        connection.failures, connection.retry_at = 0, None
        connection.save()
    except Exception as exc:
        # Never reflect EWS XML, request headers, passwords or library exceptions.
        record_failure(connection, exc)
