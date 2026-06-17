import os
import hashlib
from datetime import datetime
from django.conf import settings
from django.utils import timezone
from .models import FileNode

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

def crawl_supernote_directory():
    """Recursively scan the SuperNote source directory and sync with the database."""
    source_dir = settings.SUPERNOTE_SOURCE
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

    # Cleanup: Remove records for files that no longer exist
    FileNode.objects.exclude(path__in=seen_paths).delete()
