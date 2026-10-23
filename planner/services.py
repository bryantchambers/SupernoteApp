import base64
import hashlib
import json
import secrets
import uuid
from datetime import date, datetime
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import Book, Calendar, Device, Item, Operation
from .recognition import parse_entry
from .providers import validate_edit


def encode_item(item):
    return {'id': str(item.id), 'kind': item.kind, 'title': item.title, 'day': item.day.isoformat(),
            'end_day': item.end_day.isoformat() if item.end_day else None,
            'start_time': item.start_time.isoformat() if item.start_time else None,
            'end_time': item.end_time.isoformat() if item.end_time else None,
            'completed': item.completed, 'deleted': item.deleted or bool(item.calendar_id and not item.calendar.selected), 'version': item.version,
            'calendar': item.calendar_id, 'needs_review': item.needs_review}


def bump(book):
    Book.objects.filter(pk=book.pk).update(revision=F('revision') + 1)
    book.refresh_from_db(fields=['revision'])


@transaction.atomic
def save_item(item, expected=None, source='web', operation_id=None):
    book = Book.objects.select_for_update().get(pk=item.book_id)
    current = None
    if item.pk and Item.objects.filter(pk=item.pk).exists():
        current = Item.objects.get(pk=item.pk)
        if expected is None or current.version != int(expected):
            raise ValueError('This entry changed elsewhere. Reload before saving.')
        item.version = current.version + 1
    validate_edit(item, current)
    item.save()
    bump(book)
    Operation.objects.create(id=operation_id or uuid.uuid4(), book=book, item=item, source=source, payload=encode_item(item), status='pending' if item.calendar_id else 'local')
    return item


def pair(book, name='A6X'):
    token = secrets.token_urlsafe(32)
    device = Device.objects.create(book=book, name=name, token_hash=hashlib.sha256(token.encode()).hexdigest())
    return device, token


@transaction.atomic
def device_changes(device, operations):
    if not isinstance(operations, list) or len(operations) > 100:
        raise ValueError('Send at most 100 operations per sync.')
    book = Book.objects.select_for_update().get(pk=device.book_id)
    replies = []
    for raw in operations:
        oid = uuid.UUID(raw['id'])
        digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        old = Operation.objects.filter(pk=oid).first()
        if old:
            if old.book_id != book.pk or old.source_digest != digest:
                raise ValueError('Operation ID was already used for another change.')
            replies.append({'id': str(oid), 'status': old.status, 'message': old.message,
                            'item_id': str(old.item_id) if old.item_id else None, 'version': old.item.version if old.item_id else None})
            continue
        try:
            if not device.validated:
                raise ValueError('Device acceptance is pending. This change requires review.')
            action = raw.get('action', 'capture')
            if action == 'capture':
                if raw.get('uncertain'):
                    raise ValueError('This capture appears crossed out or ambiguous. Review it before publishing.')
                parsed = parse_entry(raw['text'], raw['day'], book.timezone)
                cal = Calendar.objects.filter(connection__owner=book.owner, selected=True, writable=True, kind=parsed['kind'])
                alias = parsed.pop('alias')
                cal = cal.filter(alias=alias).first() if alias else cal.filter(is_default=True).first()
                if not cal:
                    raise ValueError('Select a default writable calendar/task list first.')
                item = Item(book=book, calendar=cal, **parsed)
                if not book.start <= date.fromisoformat(parsed['day']) < date.fromisoformat(book.manifest['end']):
                    raise ValueError('Entry falls outside this planner.')
            elif action in ('cancel', 'complete'):
                if raw.get('item_id'):
                    item = Item.objects.get(pk=raw['item_id'], book=book)
                else:
                    source = Operation.objects.filter(pk=raw.get('item_operation'), book=book, source='device').first()
                    if source is None or source.item_id is None:
                        raise ValueError('The original handwritten entry has not been accepted.')
                    item = Item.objects.get(pk=source.item_id, book=book)
                if item.calendar_id and (not item.calendar.selected or not item.calendar.writable):
                    raise ValueError('This calendar is read-only or no longer selected.')
                if item.needs_review:
                    raise ValueError('Shared or recurring changes require review.')
                if int(raw['version']) != item.version:
                    raise ValueError('Event changed elsewhere. Review the latest version.')
                item.deleted = action == 'cancel'
                if action == 'complete':
                    if item.kind != 'task':
                        raise ValueError('Only tasks can be completed.')
                    item.completed = True
            else:
                raise ValueError('Unknown pen action.')
            # Parse ISO values into native fields before serializing the saved entry.
            for field in ['day', 'end_day']:
                if isinstance(getattr(item, field), str):
                    setattr(item, field, date.fromisoformat(getattr(item, field)))
            for field in ['start_time', 'end_time']:
                if isinstance(getattr(item, field), str):
                    setattr(item, field, datetime.strptime(getattr(item, field), '%H:%M:%S').time())
            save_item(item, raw.get('version'), 'device', oid)
            Operation.objects.filter(pk=oid).update(source_digest=digest)
            region = raw.get('source_region')
            if region:
                pages = {p['id']: p for p in book.manifest['pages']}
                rect = region.get('rect', [])
                if region.get('page_id') in pages and len(rect) == 4 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in rect):
                    op = Operation.objects.get(pk=oid)
                    op.payload['source_region'] = region
                    op.save(update_fields=['payload'])
            replies.append({'id': str(oid), 'status': 'pending', 'message': '', 'item_id': str(item.pk), 'version': item.version})
        except (ValueError, KeyError, Item.DoesNotExist) as exc:
            Operation.objects.create(id=oid, book=book, source='device', payload=raw, source_digest=digest, status='review', message=str(exc)[:250])
            replies.append({'id': str(oid), 'status': 'review', 'message': str(exc)})
    return replies
