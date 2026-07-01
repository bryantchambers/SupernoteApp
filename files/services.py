import os
import hashlib
import fcntl
import subprocess
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from django.conf import settings
from django.utils import timezone
from .models import FileNode, SyncState, ArchiveRecord

SYNC_STATE_KEY = "supernote"


class SyncInProgressError(RuntimeError):
    pass


def get_sync_state():
    state, _ = SyncState.objects.get_or_create(
        key=SYNC_STATE_KEY,
        defaults={
            "status": "idle",
            "direction": "pull",
            "last_message": "",
        },
    )
    return state


def ensure_supernote_source_exists():
    source_dir = Path(settings.SUPERNOTE_SOURCE)
    source_dir.mkdir(parents=True, exist_ok=True)
    return source_dir


@contextmanager
def _sync_lock():
    lock_path = Path(settings.SUPERNOTE_SYNC_LOCK_FILE)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SyncInProgressError("A sync job is already running.") from exc

        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def get_file_hash(file_path):
    """Calculate SHA-256 hash of a file."""
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except (OSError, IOError):
        return ""


def _archive_full_path(node):
    return Path(settings.ARCHIVE_DIR) / node.path


def _live_full_path(node):
    return Path(settings.SUPERNOTE_SOURCE) / node.path


def _readable_archive_path(node):
    archive_full_path = _archive_full_path(node)
    if node.extension == 'note':
        return archive_full_path.with_suffix('.pdf')
    if node.extension == 'spd':
        return archive_full_path.with_suffix('.png')
    return archive_full_path


def _ensure_parent(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _create_readable_archive_copy(node, archive_full_path):
    readable_path = _readable_archive_path(node)
    if readable_path == archive_full_path:
        return str(readable_path)

    _ensure_parent(readable_path)
    from .utils import SuperNoteUtility
    from .atelier_utils import AtelierUtility

    if node.extension == 'note':
        success = SuperNoteUtility.convert_note(str(archive_full_path), str(readable_path), 'pdf')
    elif node.extension == 'spd':
        success = AtelierUtility.extract_thumbnail(str(archive_full_path), str(readable_path))
    else:
        success = True

    if not success:
        return ''
    return str(readable_path)


def archive_file_node(node):
    source_path = _live_full_path(node)
    archive_path = _archive_full_path(node)
    _ensure_parent(archive_path)
    if archive_path.exists():
        if archive_path.is_file():
            archive_path.unlink()
        else:
            raise RuntimeError(f'Archive path exists and is not a file: {archive_path}')

    if not source_path.exists():
        raise FileNotFoundError(f'Source file not found: {source_path}')

    shutil.move(str(source_path), str(archive_path))

    readable_path = _create_readable_archive_copy(node, archive_path)
    if node.extension in {'note', 'spd'} and not readable_path:
        shutil.move(str(archive_path), str(source_path))
        raise RuntimeError(f'Failed to generate readable archive copy for {node.path}')

    ArchiveRecord.objects.create(
        file_node=node,
        archive_path=node.path,
        readable_path=(Path(readable_path).relative_to(settings.ARCHIVE_DIR).as_posix() if readable_path else ''),
        version_hash=node.hash,
    )

    node.is_archived = True
    node.save(update_fields=['is_archived', 'updated_at'])
    return {
        'archive_path': str(archive_path),
        'readable_path': readable_path,
    }


def restore_file_node(node):
    source_path = _live_full_path(node)
    archive_path = _archive_full_path(node)
    _ensure_parent(source_path)
    if source_path.exists():
        if source_path.is_file():
            source_path.unlink()
        else:
            raise RuntimeError(f'Source path exists and is not a file: {source_path}')

    if not archive_path.exists():
        raise FileNotFoundError(f'Archive file not found: {archive_path}')

    shutil.move(str(archive_path), str(source_path))
    node.is_archived = False
    node.save(update_fields=['is_archived', 'updated_at'])
    return {'source_path': str(source_path)}


def crawl_supernote_directory():
    """Recursively scan the SuperNote source directory and sync with the database."""
    source_dir = ensure_supernote_source_exists()
    if not os.path.exists(source_dir):
        print(f"Source directory {source_dir} does not exist.")
        return

    # Track paths seen in this crawl to identify deleted files
    seen_paths = set()

    for root, dirs, files in os.walk(source_dir):
        # Process Directories
        for d in dirs:
            dir_path = os.path.join(root, d)
            rel_path = os.path.relpath(dir_path, source_dir)
            seen_paths.add(rel_path)

            parent_rel = os.path.relpath(root, source_dir)
            parent = None
            if parent_rel != ".":
                parent = FileNode.objects.filter(path=parent_rel).first()

            FileNode.objects.update_or_create(
                path=rel_path,
                defaults={
                    'name': d,
                    'is_directory': True,
                    'last_modified': timezone.make_aware(datetime.fromtimestamp(os.path.getmtime(dir_path))),
                    'parent': parent
                }
            )

        # Process Files
        for f in files:
            file_path = os.path.join(root, f)
            rel_path = os.path.relpath(file_path, source_dir)
            seen_paths.add(rel_path)

            parent_rel = os.path.relpath(root, source_dir)
            parent = None
            if parent_rel != ".":
                parent = FileNode.objects.filter(path=parent_rel).first()

            extension = os.path.splitext(f)[1].lower().replace('.', '')
            size = os.path.getsize(file_path)
            mtime = timezone.make_aware(datetime.fromtimestamp(os.path.getmtime(file_path)))

            # Optimization: Only re-hash if size or mtime changed
            existing = FileNode.objects.filter(path=rel_path).first()
            file_hash = existing.hash if existing else ""
            if not existing or existing.size != size or existing.last_modified != mtime:
                file_hash = get_file_hash(file_path)

            FileNode.objects.update_or_create(
                path=rel_path,
                defaults={
                    'name': f,
                    'extension': extension,
                    'size': size,
                    'last_modified': mtime,
                    'hash': file_hash,
                    'is_directory': False,
                    'parent': parent
                }
            )

    # Cleanup: Remove records for live source files that no longer exist
    FileNode.objects.filter(is_archived=False).exclude(path__in=seen_paths).delete()


def run_rclone_sync(direction="pull"):
    ensure_supernote_source_exists()
    source_dir = str(settings.SUPERNOTE_SOURCE)
    remote = settings.SUPERNOTE_REMOTE

    if direction == "push":
        command = ["rclone", "sync", source_dir, remote]
    else:
        command = ["rclone", "sync", remote, source_dir]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "rclone sync failed"
        raise RuntimeError(message)

    return {
        "command": command,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def perform_supernote_sync(direction="pull", rescan=True):
    state = get_sync_state()
    state.status = "running"
    state.direction = direction
    state.last_started_at = timezone.now()
    state.last_message = ""
    state.save()

    try:
        with _sync_lock():
            result = run_rclone_sync(direction=direction)
            if rescan:
                crawl_supernote_directory()
    except Exception as exc:
        state.status = "error"
        state.last_finished_at = timezone.now()
        state.last_message = str(exc)
        state.save()
        raise

    state.status = "success"
    state.last_finished_at = timezone.now()
    state.last_message = result["stdout"] or "Sync completed successfully."
    state.save()
    return {"success": True, "state": state, "result": result}
