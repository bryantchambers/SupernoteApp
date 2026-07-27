import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from django.conf import settings


SERVICE_FLAGS = {
    'supernote_sync': 'SUPERNOTE_SYNC_ENABLED',
    'zotero': 'ZOTERO_ENABLED',
    'koofr': 'KOOFR_ENABLED',
    'ai': 'AI_PROCESSING_ENABLED',
    'periodicals': 'PERIODICALS_ENABLED',
}

SERVICE_LABELS = {
    'supernote_sync': 'Supernote Sync',
    'zotero': 'Zotero',
    'koofr': 'Koofr / WebDAV',
    'ai': 'AI Processing',
    'periodicals': 'Periodicals',
}

EDITABLE_ENV_KEYS = [
    'SUPERNOTE_REMOTE',
    'GOOGLE_GENAI_API_KEY',
    'GOOGLE_GENAI_MODEL',
    'ZOTERO_API_BASE',
    'ZOTERO_API_KEY',
    'ZOTERO_USER_ID',
    'ZOTERO_LIBRARY_TYPE',
    'KOOFR_BASE_URL',
    'KOOFR_USER_NAME',
    'KOOFR_TOKEN',
    'CALIBRE_EBOOK_CONVERT',
    'PERIODICAL_OUTPUT_FORMAT',
    *SERVICE_FLAGS.values(),
]

SECRET_KEYS = {'GOOGLE_GENAI_API_KEY', 'ZOTERO_API_KEY', 'KOOFR_TOKEN'}

RUNTIME_SETTINGS = {
    'SUPERNOTE_REMOTE': 'SUPERNOTE_REMOTE',
    'GOOGLE_GENAI_API_KEY': 'GOOGLE_GENAI_API_KEY',
    'GOOGLE_GENAI_MODEL': 'GOOGLE_GENAI_MODEL',
    'ZOTERO_API_BASE': 'ZOTERO_API_BASE',
    'ZOTERO_API_KEY': 'ZOTERO_API_KEY',
    'ZOTERO_USER_ID': 'ZOTERO_USER_ID',
    'ZOTERO_LIBRARY_TYPE': 'ZOTERO_LIBRARY_TYPE',
    'KOOFR_BASE_URL': 'KOOFR_BASE_URL',
    'KOOFR_USER_NAME': 'KOOFR_USER_NAME',
    'KOOFR_TOKEN': 'KOOFR_TOKEN',
    'CALIBRE_EBOOK_CONVERT': 'CALIBRE_EBOOK_CONVERT',
    'PERIODICAL_OUTPUT_FORMAT': 'PERIODICAL_OUTPUT_FORMAT',
    **{key: key for key in SERVICE_FLAGS.values()},
}

DEFAULTS = {
    'SUPERNOTE_REMOTE': 'SuperNote:Supernote',
    'GOOGLE_GENAI_MODEL': 'gemini-3.6-flash',
    'ZOTERO_API_BASE': 'https://api.zotero.org',
    'ZOTERO_LIBRARY_TYPE': 'user',
    'KOOFR_BASE_URL': 'https://app.koofr.net/dav/Koofr/zotero',
    'CALIBRE_EBOOK_CONVERT': 'ebook-convert',
    'PERIODICAL_OUTPUT_FORMAT': 'epub',
    **{key: 'true' for key in SERVICE_FLAGS.values()},
}

BOOLEAN_KEYS = set(SERVICE_FLAGS.values())


@dataclass
class ServiceCheck:
    service: str
    ok: bool
    message: str


def env_path() -> Path:
    configured_path = os.environ.get('SUPERNOTE_ENV_FILE')
    return Path(configured_path) if configured_path else Path(settings.BASE_DIR) / '.env'


def _parse_line(line: str):
    stripped = line.strip()
    if not stripped or stripped.startswith('#'):
        return None, None
    if stripped.startswith('export '):
        stripped = stripped[7:].lstrip()
    if '=' not in stripped:
        return None, None
    key, value = stripped.split('=', 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return key, value


def _format_value(value: str) -> str:
    value = '' if value is None else str(value).strip()
    if not value:
        return ''
    if any(char.isspace() for char in value) or '#' in value:
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _env_file_values() -> dict:
    path = env_path()
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding='utf-8').splitlines():
        key, value = _parse_line(line)
        if key:
            values[key] = value
    return values


def current_env_values(keys: Iterable[str] = EDITABLE_ENV_KEYS) -> dict:
    file_values = _env_file_values()
    values = {}
    for key in keys:
        if key in file_values:
            values[key] = file_values[key]
        elif key in os.environ:
            values[key] = os.environ[key]
        elif hasattr(settings, key):
            values[key] = str(getattr(settings, key))
        else:
            values[key] = DEFAULTS.get(key, '')
    return values


def mask_value(key: str, value: str) -> str:
    if key not in SECRET_KEYS:
        return value
    if not value:
        return ''
    if len(value) <= 8:
        return '********'
    return f'{value[:4]}...{value[-4:]}'


def boolean_value(value) -> bool:
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def apply_runtime_settings(updates: dict) -> None:
    for key, value in updates.items():
        value = str(value).strip()
        os.environ[key] = value
        setting_name = RUNTIME_SETTINGS.get(key)
        if not setting_name:
            continue
        if key in BOOLEAN_KEYS:
            setattr(settings, setting_name, boolean_value(value))
        else:
            setattr(settings, setting_name, value)


def write_env_values(updates: dict) -> dict:
    path = env_path()
    lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    remaining = {key: str(value).strip() for key, value in updates.items() if key in EDITABLE_ENV_KEYS}
    written = set()
    output = []

    for line in lines:
        key, _ = _parse_line(line)
        if key in remaining:
            output.append(f'{key}={_format_value(remaining[key])}')
            written.add(key)
        else:
            output.append(line)

    for key in EDITABLE_ENV_KEYS:
        if key in remaining and key not in written:
            output.append(f'{key}={_format_value(remaining[key])}')

    path.write_text('\n'.join(output).rstrip() + '\n', encoding='utf-8')
    apply_runtime_settings(remaining)
    return remaining


def settings_context() -> dict:
    values = current_env_values()
    masked = {key: mask_value(key, value) for key, value in values.items()}
    service_flags = {name: boolean_value(values.get(key, DEFAULTS.get(key, 'true'))) for name, key in SERVICE_FLAGS.items()}
    return {
        'title': 'Settings',
        'values': values,
        'masked_values': masked,
        'secret_keys': SECRET_KEYS,
        'service_flags': service_flags,
        'service_flag_keys': SERVICE_FLAGS,
        'service_labels': SERVICE_LABELS,
        'supernote_source': settings.SUPERNOTE_SOURCE,
        'archive_dir': settings.ARCHIVE_DIR,
        'database_path': settings.DATABASES['default']['NAME'],
    }


def update_env_from_post(post) -> dict:
    current = current_env_values()
    updates = {}
    for key in EDITABLE_ENV_KEYS:
        if key in BOOLEAN_KEYS:
            continue
        if key not in post:
            continue
        value = post.get(key, '').strip()
        if key in SECRET_KEYS and not value:
            value = current.get(key, '')
        updates[key] = value
    return write_env_values(updates)


def update_service_flags_from_post(post) -> dict:
    updates = {key: ('true' if post.get(key) == 'on' else 'false') for key in SERVICE_FLAGS.values()}
    return write_env_values(updates)


def is_service_enabled(name: str) -> bool:
    key = SERVICE_FLAGS[name]
    return boolean_value(getattr(settings, key, DEFAULTS.get(key, 'true')))


def require_service_enabled(name: str) -> None:
    if not is_service_enabled(name):
        raise RuntimeError(f'{name.replace("_", " ").title()} is disabled in Settings.')


def test_service(service: str) -> ServiceCheck:
    if service == 'sync':
        if not is_service_enabled('supernote_sync'):
            return ServiceCheck(service, False, 'Supernote sync is disabled.')
        source = Path(settings.SUPERNOTE_SOURCE)
        if not source.exists():
            return ServiceCheck(service, False, f'Supernote source does not exist: {source}')
        if shutil.which('rclone') is None:
            return ServiceCheck(service, False, 'rclone was not found on PATH.')
        return ServiceCheck(service, True, f'rclone is available; source exists at {source}.')

    if service == 'ai':
        if not is_service_enabled('ai'):
            return ServiceCheck(service, False, 'AI processing is disabled.')
        if not getattr(settings, 'GOOGLE_GENAI_API_KEY', ''):
            return ServiceCheck(service, False, 'Gemini API key is not configured.')
        return ServiceCheck(service, True, f'Gemini key is configured for {settings.GOOGLE_GENAI_MODEL}.')

    if service == 'zotero':
        if not is_service_enabled('zotero'):
            return ServiceCheck(service, False, 'Zotero is disabled.')
        if not settings.ZOTERO_API_KEY or not settings.ZOTERO_USER_ID:
            return ServiceCheck(service, False, 'Zotero API key or user ID is missing.')
        return ServiceCheck(service, True, 'Zotero credentials are configured.')

    if service == 'koofr':
        if not is_service_enabled('koofr'):
            return ServiceCheck(service, False, 'Koofr/WebDAV is disabled.')
        if not settings.KOOFR_USER_NAME or not settings.KOOFR_TOKEN:
            return ServiceCheck(service, False, 'Koofr username or token is missing.')
        return ServiceCheck(service, True, 'Koofr/WebDAV credentials are configured.')

    if service == 'periodicals':
        if not is_service_enabled('periodicals'):
            return ServiceCheck(service, False, 'Periodicals are disabled.')
        command = getattr(settings, 'CALIBRE_EBOOK_CONVERT', 'ebook-convert')
        if shutil.which(command) is None and not Path(command).exists():
            return ServiceCheck(service, False, f'Calibre command was not found: {command}')
        return ServiceCheck(service, True, f'Calibre command is available: {command}')

    return ServiceCheck(service, False, 'Unknown service.')


def database_export_name() -> str:
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    return f'supernote-db-{timestamp}.sqlite3'
