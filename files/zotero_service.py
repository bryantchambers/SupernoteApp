import base64
import io
import json
from pathlib import Path
from urllib import error, parse, request as urlrequest
import zipfile

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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
        with urlrequest.urlopen(req, timeout=20) as resp:
            payload = resp.read()
            if raw:
                return payload
            if not payload:
                return {}
            return json.loads(payload.decode('utf-8'))
    except error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='ignore')
        raise ZoteroSyncError(detail or str(exc)) from exc


def _parse_dt(value):
    if not value:
        return None
    return parse_datetime(value)


def _select_attachment(item_key, prefix=None):
    prefix = prefix or _library_prefix()
    children = _request(f'{prefix}/items/{item_key}/children', params={'format': 'json'})
    first_attachment = None
    for child in children or []:
        data = child.get('data', {})
        if data.get('itemType') != 'attachment':
            continue
        if first_attachment is None:
            first_attachment = child
        if data.get('contentType') == 'application/pdf':
            return child
    return first_attachment


def sync_zotero_library(limit=100):
    state = get_zotero_state()
    state.status = 'running'
    state.last_message = ''
    state.save()

    prefix = _library_prefix()
    params = {'format': 'json', 'sort': 'dateAdded', 'direction': 'desc'}
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
        if data.get('itemType') == 'attachment':
            attachment_data = data
        else:
            try:
                attachment = _select_attachment(key, prefix=prefix)
                attachment_data = (attachment or {}).get('data', {})
            except ZoteroSyncError:
                attachment_data = {}
        existing = ZoteroItem.objects.filter(zotero_key=key).only('is_on_device', 'device_path', 'last_transfer_status', 'last_transfer_message').first()
        preserve_transfer_state = bool(existing and existing.is_on_device)
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
                'date_added': _parse_dt(data.get('dateAdded')),
                'date_modified': _parse_dt(data.get('dateModified')),
                'attachment_key': attachment_data.get('key', ''),
                'attachment_title': attachment_data.get('title', ''),
                'attachment_filename': attachment_data.get('filename', ''),
                'attachment_mime_type': attachment_data.get('contentType', ''),
                'attachment_link_mode': attachment_data.get('linkMode', ''),
                'attachment_path': attachment_data.get('path', ''),
                'attachment_url': attachment_data.get('url', ''),
                'raw_data': data,
                'synced_at': timezone.now(),
                'is_recycled': bool(existing.is_recycled) if existing else False,
                'recycled_at': existing.recycled_at if existing else None,
                'is_on_device': preserve_transfer_state,
                'device_path': existing.device_path if preserve_transfer_state and existing else '',
                'last_transfer_status': existing.last_transfer_status if preserve_transfer_state and existing else 'idle',
                'last_transfer_message': existing.last_transfer_message if preserve_transfer_state and existing else '',
            },
        )

    ZoteroItem.objects.filter(is_recycled=False).exclude(zotero_key__in=seen).delete()

    state.status = 'success'
    state.last_synced_at = timezone.now()
    state.last_message = f'Synced {len(seen)} items.'
    state.save()
    return {'success': True, 'state': state, 'count': len(seen)}


def _device_path_for(item, filename=None):
    title = filename or item.attachment_filename or item.attachment_title or item.title or item.zotero_key
    parsed_title = parse.urlparse(title)
    stem_source = Path(parsed_title.path or title).stem
    suffix = Path(parsed_title.path or title).suffix or '.pdf'
    safe = slugify(stem_source) or item.zotero_key
    return Path(settings.ZOTERO_DEVICE_DIR) / f'{safe}{suffix}'


def set_transfer_status(item, status, message):
    item.last_transfer_status = status
    item.last_transfer_message = message
    item.save(update_fields=['last_transfer_status', 'last_transfer_message', 'updated_at'])


def _relative_device_path(device_path):
    try:
        return str(device_path.relative_to(settings.SUPERNOTE_SOURCE))
    except ValueError:
        return str(device_path)


def _koofr_auth_header():
    if not getattr(settings, 'KOOFR_ENABLED', True):
        raise ZoteroSyncError('Koofr/WebDAV is disabled in Settings.')
    username = (getattr(settings, 'KOOFR_USER_NAME', '') or '').strip()
    token = (getattr(settings, 'KOOFR_TOKEN', '') or '').strip()
    if not username or not token:
        raise ZoteroSyncError('Koofr credentials are not configured on the server.')
    raw = f'{username}:{token}'.encode('utf-8')
    return 'Basic ' + base64.b64encode(raw).decode('ascii')


def _quote_url(url):
    parsed = parse.urlsplit(url)
    path = parse.quote(parsed.path, safe='/%')
    query = parse.quote_plus(parsed.query, safe='=&')
    return parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, parsed.fragment))


def _download_remote_bytes(url):
    headers = {'Accept': 'application/octet-stream'}
    if 'koofr.net' in parse.urlsplit(url).netloc.lower():
        headers['Authorization'] = _koofr_auth_header()
    req = urlrequest.Request(_quote_url(url), headers=headers, method='GET')
    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            return resp.read()
    except error.HTTPError as exc:
        if exc.code == 404:
            raise ZoteroSyncError('Remote file was not found in Koofr/WebDAV.') from exc
        detail = exc.read().decode('utf-8', errors='ignore')
        raise ZoteroSyncError(detail or str(exc)) from exc
    except error.URLError as exc:
        raise ZoteroSyncError(str(exc.reason)) from exc


def _koofr_base_url():
    return (getattr(settings, 'KOOFR_BASE_URL', '') or '').rstrip('/')


def _koofr_storage_urls(attachment_key):
    base = _koofr_base_url()
    if not base:
        raise ZoteroSyncError('KOOFR_BASE_URL is not configured on the server.')
    safe_key = parse.quote(attachment_key.strip())
    return {
        'prop': f'{base}/{safe_key}.prop',
        'zip': f'{base}/{safe_key}.zip',
    }


def _extract_pdf_from_zip(payload, fallback_name='attachment.pdf'):
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ZoteroSyncError('Koofr storage archive is not a valid zip file.') from exc

    names = [name for name in archive.namelist() if not name.endswith('/')]
    if not names:
        raise ZoteroSyncError('Koofr storage archive does not contain a file.')

    pdf_name = next((name for name in names if name.lower().endswith('.pdf')), names[0])
    return archive.read(pdf_name), Path(pdf_name).name or fallback_name


def _candidate_remote_url(candidate):
    candidate = (candidate or '').strip()
    if not candidate:
        return ''

    lowered = candidate.lower()
    if lowered.startswith('http://') or lowered.startswith('https://'):
        return candidate

    base = _koofr_base_url()
    if not base:
        return ''

    if candidate.startswith('/dav/') or candidate.startswith('dav/'):
        base_parts = parse.urlsplit(base)
        suffix = candidate.lstrip('/')
        return f'{base_parts.scheme}://{base_parts.netloc}/{suffix}'

    for marker in ('Koofr/zotero/', 'koofr/zotero/', 'zotero/'):
        idx = candidate.find(marker)
        if idx >= 0:
            relative = candidate[idx + len(marker):].lstrip('/')
            return f'{base}/{relative}'

    if not candidate.startswith('/') and ':' not in candidate:
        return f'{base}/{candidate.lstrip("./")}'

    return ''


def _resolve_attachment_source(item):
    link_mode = (item.attachment_link_mode or '').lower()
    if item.attachment_key and link_mode in {'imported_file', 'imported_url', ''}:
        urls = _koofr_storage_urls(item.attachment_key)
        try:
            _download_remote_bytes(urls['prop'])
        except ZoteroSyncError:
            pass
        zip_payload = _download_remote_bytes(urls['zip'])
        pdf_payload, filename = _extract_pdf_from_zip(zip_payload, fallback_name=item.attachment_filename or item.attachment_title or item.title or item.zotero_key)
        return {
            'kind': 'zotero_storage',
            'label': 'Zotero storage (Koofr)',
            'payload': pdf_payload,
            'filename': filename,
        }

    for candidate in (item.attachment_url, item.attachment_path):
        remote_url = _candidate_remote_url(candidate)
        if not remote_url:
            continue
        kind = 'koofr_linked' if 'koofr.net' in parse.urlsplit(remote_url).netloc.lower() else 'remote_linked'
        label = 'Koofr linked attachment' if kind == 'koofr_linked' else 'Direct linked attachment'
        return {
            'kind': kind,
            'label': label,
            'payload': _download_remote_bytes(remote_url),
        }

    if item.attachment_path or item.attachment_url:
        raise ZoteroSyncError('Linked attachment exists, but the server cannot resolve a remote file URL for it.')

    raise ZoteroSyncError('Item has no attachment to copy to the device mirror.')


def add_item_to_device(item):
    set_transfer_status(item, 'idle', 'Resolving attachment source...')
    source = _resolve_attachment_source(item)
    device_path = _device_path_for(item, filename=source.get('filename'))
    device_path.parent.mkdir(parents=True, exist_ok=True)
    device_path.write_bytes(source['payload'])
    item.device_path = _relative_device_path(device_path)
    item.is_on_device = True
    item.last_transfer_status = 'success'
    item.last_transfer_message = f'Added from {source["label"]}.'
    item.save(update_fields=['device_path', 'is_on_device', 'last_transfer_status', 'last_transfer_message', 'updated_at'])
    return {'device_path': str(device_path), 'source_kind': source['kind'], 'source_label': source['label']}


def remove_item_from_device(item):
    if item.device_path:
        device_path = Path(settings.SUPERNOTE_SOURCE) / item.device_path if not Path(item.device_path).is_absolute() else Path(item.device_path)
    else:
        device_path = _device_path_for(item)

    if device_path.exists():
        device_path.unlink()

    item.is_on_device = False
    item.device_path = ''
    item.last_transfer_status = 'removed'
    item.last_transfer_message = 'Removed from the device mirror.'
    item.save(update_fields=['device_path', 'is_on_device', 'last_transfer_status', 'last_transfer_message', 'updated_at'])
    return {'removed': True}


def move_item_to_recycle_bin(item):
    if item.is_on_device:
        remove_item_from_device(item)
    item.is_recycled = True
    item.recycled_at = timezone.now()
    item.last_transfer_status = 'recycled'
    item.last_transfer_message = 'Moved to recycle bin.'
    item.save(update_fields=['is_recycled', 'recycled_at', 'last_transfer_status', 'last_transfer_message', 'updated_at'])
    return {'recycled': True}


def restore_item_from_recycle_bin(item):
    item.is_recycled = False
    item.recycled_at = None
    item.last_transfer_status = 'idle'
    item.last_transfer_message = ''
    item.save(update_fields=['is_recycled', 'recycled_at', 'last_transfer_status', 'last_transfer_message', 'updated_at'])
    return {'restored': True}


def empty_recycle_bin_item(item):
    if item.device_path:
        device_path = Path(settings.SUPERNOTE_SOURCE) / item.device_path if not Path(item.device_path).is_absolute() else Path(item.device_path)
        if device_path.exists():
            device_path.unlink()
    item.delete()
    return {'deleted': True}


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
