import json
from pathlib import Path
from urllib import error, parse, request as urlrequest

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

from .models import ZoteroItem, ZoteroSyncState

ZOTERO_SYNC_KEY = 'zotero'


class ZoteroSyncError(RuntimeError):
    pass


def get_zotero_state():
    state, _ = ZoteroSyncState.objects.get_or_create(
        key=ZOTERO_SYNC_KEY,
        defaults={'status': 'idle', 'last_message': ''},
    )
    return state


def _library_prefix(user_id=None):
    user_id = (user_id if user_id is not None else settings.ZOTERO_USER_ID).strip()
    if settings.ZOTERO_LIBRARY_TYPE == 'group':
        if not user_id:
            raise ZoteroSyncError('ZOTERO_USER_ID is not configured')
        return f"groups/{user_id}"
    if not user_id or user_id == 'me':
        return 'users/me'
    return f"users/{user_id}"


def _api_url(path, params=None):
    base = settings.ZOTERO_API_BASE.rstrip('/')
    url = f"{base}/{path.lstrip('/')}"
    if params:
        url = f"{url}?{parse.urlencode(params, doseq=True)}"
    return url


def _request(path, method='GET', params=None, data=None, headers=None, raw=False):
    if not settings.ZOTERO_API_KEY:
        raise ZoteroSyncError('ZOTERO_API_KEY is not configured')

    url = _api_url(path, params=params)
    req_headers = {
        'Zotero-API-Key': settings.ZOTERO_API_KEY,
        'Accept': 'application/octet-stream' if raw else 'application/json',
    }
    if headers:
        req_headers.update(headers)

    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode('utf-8')
            req_headers.setdefault('Content-Type', 'application/json')
        elif isinstance(data, bytes):
            body = data
        else:
            body = str(data).encode('utf-8')

    req = urlrequest.Request(url, data=body, method=method, headers=req_headers)
    try:
        with urlrequest.urlopen(req) as resp:
            payload = resp.read()
            if raw:
                return payload
            if not payload:
                return {}
            return json.loads(payload.decode('utf-8'))
    except error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='ignore')
        raise ZoteroSyncError(detail or str(exc)) from exc


def _select_attachment(item_key, prefix=None):
    prefix = prefix or _library_prefix()
    children = _request(f'{prefix}/items/{item_key}/children', params={'format': 'json'})
    for child in children or []:
        data = child.get('data', {})
        if data.get('itemType') == 'attachment':
            return child
    return None


def sync_zotero_library(limit=1000):
    state = get_zotero_state()
    state.status = 'running'
    state.last_message = ''
    state.save()

    prefix = _library_prefix()
    params = {'format': 'json'}
    if limit is not None:
        params['limit'] = limit
    try:
        items = _request(f'{prefix}/items/top', params=params)
    except ZoteroSyncError as exc:
        if 'Invalid user ID' in str(exc) and prefix != 'users/me' and settings.ZOTERO_LIBRARY_TYPE != 'group':
            prefix = 'users/me'
            items = _request(f'{prefix}/items/top', params=params)
        else:
            raise
    seen = set()

    for item in items or []:
        data = item.get('data', {})
        key = data.get('key')
        if not key:
            continue

        seen.add(key)
        attachment = _select_attachment(key, prefix=prefix)
        attachment_data = (attachment or {}).get('data', {})
        existing = ZoteroItem.objects.filter(zotero_key=key).only('is_on_device').first()
        ZoteroItem.objects.update_or_create(
            zotero_key=key,
            defaults={
                'library_type': settings.ZOTERO_LIBRARY_TYPE if prefix != 'users/me' else 'user',
                'item_type': data.get('itemType', ''),
                'title': data.get('title', ''),
                'creators': data.get('creators', []) or [],
                'abstract_note': data.get('abstractNote', ''),
                'date': data.get('date', ''),
                'url': data.get('url', ''),
                'attachment_key': attachment_data.get('key', ''),
                'attachment_title': attachment_data.get('title', ''),
                'attachment_filename': attachment_data.get('filename', ''),
                'attachment_mime_type': attachment_data.get('contentType', ''),
                'raw_data': data,
                'synced_at': timezone.now(),
                'is_on_device': bool(existing and existing.is_on_device),
            },
        )

    ZoteroItem.objects.exclude(zotero_key__in=seen).delete()

    state.status = 'success'
    state.last_synced_at = timezone.now()
    state.last_message = f'Synced {len(seen)} items.'
    state.save()
    return {'success': True, 'state': state, 'count': len(seen)}


def _device_path_for(item):
    title = item.attachment_filename or item.attachment_title or item.title or item.zotero_key
    safe = slugify(Path(title).stem) or item.zotero_key
    suffix = Path(title).suffix or '.pdf'
    return Path(settings.ZOTERO_DEVICE_DIR) / f'{safe}{suffix}'


def add_item_to_device(item):
    if not item.attachment_key:
        raise ZoteroSyncError('Item has no attachment to copy to the device mirror.')

    device_path = _device_path_for(item)
    device_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _request(f'{_library_prefix()}/items/{item.attachment_key}/file', raw=True)
    device_path.write_bytes(payload)
    try:
        item.device_path = str(device_path.relative_to(settings.SUPERNOTE_SOURCE))
    except ValueError:
        item.device_path = str(device_path)
    item.is_on_device = True
    item.save(update_fields=['device_path', 'is_on_device', 'updated_at'])
    return {'device_path': str(device_path)}


def remove_item_from_device(item):
    if item.device_path:
        device_path = Path(settings.SUPERNOTE_SOURCE) / item.device_path if not Path(item.device_path).is_absolute() else Path(item.device_path)
    else:
        device_path = _device_path_for(item)

    if device_path.exists():
        device_path.unlink()

    item.is_on_device = False
    item.device_path = ''
    item.save(update_fields=['device_path', 'is_on_device', 'updated_at'])
    return {'removed': True}


def return_note_to_zotero(item, note_text):
    note_text = (note_text or '').strip()
    if not note_text:
        raise ZoteroSyncError('A note is required to send content back to Zotero.')

    prefix = _library_prefix()
    payload = [{
        'itemType': 'note',
        'note': note_text,
        'parentItem': item.zotero_key,
        'tags': [],
    }]
    result = _request(
        f'{prefix}/items',
        method='POST',
        data=payload,
        headers={'Zotero-Write-Token': 'supernote-app'},
    )
    item.note_text = note_text
    item.save(update_fields=['note_text', 'updated_at'])
    return result
