from django.db import models
import os
from .models_ai import ProcessedNote, NoteAsset

class FileNode(models.Model):
    path = models.CharField(max_length=1024, unique=True)
    name = models.CharField(max_length=255)
    extension = models.CharField(max_length=20, blank=True)
    size = models.BigIntegerField(default=0)
    last_modified = models.DateTimeField()
    hash = models.CharField(max_length=64, blank=True) # SHA-256
    is_directory = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    is_recycled = models.BooleanField(default=False)
    recycled_at = models.DateTimeField(null=True, blank=True)
    recycle_source_is_archived = models.BooleanField(default=False)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.path

    class Meta:
        ordering = ['is_directory', 'name']

class ArchiveRecord(models.Model):
    file_node = models.ForeignKey(FileNode, on_delete=models.CASCADE, related_name='archives')
    archive_path = models.CharField(max_length=1024)
    readable_path = models.CharField(max_length=1024, blank=True, default='')
    version_hash = models.CharField(max_length=64)
    archived_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.file_node.name} - {self.archived_at}"


class SyncState(models.Model):
    key = models.CharField(max_length=64, unique=True, default='supernote')
    status = models.CharField(max_length=32, default='idle')
    direction = models.CharField(max_length=16, default='pull')
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    last_message = models.TextField(blank=True)

    def __str__(self):
        return f"{self.key}: {self.status}"


class ZoteroSyncState(models.Model):
    key = models.CharField(max_length=64, unique=True, default='zotero')
    status = models.CharField(max_length=32, default='idle')
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_message = models.TextField(blank=True)

    def __str__(self):
        return f"{self.key}: {self.status}"


class ZoteroItem(models.Model):
    zotero_key = models.CharField(max_length=64, unique=True)
    library_type = models.CharField(max_length=16, default='user')
    item_type = models.CharField(max_length=64, blank=True, default='')
    title = models.CharField(max_length=512, blank=True, default='')
    creators = models.JSONField(default=list, blank=True)
    abstract_note = models.TextField(blank=True, default='')
    date = models.CharField(max_length=64, blank=True, default='')
    url = models.URLField(blank=True, default='')
    date_added = models.DateTimeField(null=True, blank=True)
    date_modified = models.DateTimeField(null=True, blank=True)
    attachment_key = models.CharField(max_length=64, blank=True, default='')
    attachment_title = models.CharField(max_length=512, blank=True, default='')
    attachment_filename = models.CharField(max_length=512, blank=True, default='')
    attachment_mime_type = models.CharField(max_length=128, blank=True, default='')
    attachment_link_mode = models.CharField(max_length=64, blank=True, default='')
    attachment_path = models.CharField(max_length=2048, blank=True, default='')
    attachment_url = models.URLField(blank=True, default='')
    device_path = models.CharField(max_length=1024, blank=True, default='')
    note_text = models.TextField(blank=True, default='')
    raw_data = models.JSONField(default=dict, blank=True)
    is_on_device = models.BooleanField(default=False)
    is_recycled = models.BooleanField(default=False)
    recycled_at = models.DateTimeField(null=True, blank=True)
    last_transfer_status = models.CharField(max_length=32, blank=True, default='idle')
    last_transfer_message = models.TextField(blank=True, default='')
    synced_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title or self.zotero_key

    @property
    def attachment_source_kind(self):
        link_mode = (self.attachment_link_mode or '').lower()
        if link_mode in {'linked_file', 'linked_url'}:
            for candidate in (self.attachment_url, self.attachment_path):
                if not candidate:
                    continue
                lowered = candidate.lower()
                if 'koofr.net' in lowered or lowered.startswith('/dav/') or lowered.startswith('dav/'):
                    return 'koofr_linked'
                return 'remote_linked'
            return 'linked_unresolved'

        if link_mode in {'imported_file', 'imported_url'} or self.attachment_key:
            return 'zotero_storage'

        for candidate in (self.attachment_url, self.attachment_path):
            if not candidate:
                continue
            lowered = candidate.lower()
            if lowered.startswith('http://') or lowered.startswith('https://'):
                if 'koofr.net' in lowered:
                    return 'koofr_linked'
                return 'remote_linked'
            if lowered.startswith('/dav/') or lowered.startswith('dav/'):
                return 'koofr_linked'
            if lowered.startswith('koofr/') or lowered.startswith('zotero/'):
                return 'koofr_linked'

        return 'missing'

    @property
    def attachment_source_label(self):
        labels = {
            'zotero_storage': 'Zotero storage (Koofr)',
            'koofr_linked': 'Koofr linked',
            'remote_linked': 'Direct link',
            'linked_unresolved': 'Linked path only',
            'missing': 'No attachment',
        }
        return labels.get(self.attachment_source_kind, 'Unknown')

    @property
    def can_add_to_device(self):
        return self.attachment_source_kind in {'zotero_storage', 'koofr_linked', 'remote_linked'}

    @property
    def transfer_status_label(self):
        labels = {
            'idle': 'Ready',
            'success': 'On device',
            'removed': 'Library only',
            'recycled': 'In recycle bin',
            'missing_remote': 'Missing remote file',
            'unresolved_linked': 'Linked path only',
            'sync_failed': 'Sync failed',
            'error': 'Needs attention',
        }
        if self.is_recycled:
            return labels['recycled']
        if self.is_on_device and self.last_transfer_status == 'idle':
            return 'On device'
        return labels.get(self.last_transfer_status or 'idle', 'Ready')

    @property
    def transfer_status_tone(self):
        if self.is_recycled:
            return 'error'
        if self.last_transfer_status == 'success' or (self.is_on_device and self.last_transfer_status == 'idle'):
            return 'success'
        if self.last_transfer_status in {'missing_remote', 'unresolved_linked', 'sync_failed', 'error'}:
            return 'error'
        return 'neutral'



class PeriodicalRecipe(models.Model):
    slug = models.SlugField(max_length=128, unique=True)
    title = models.CharField(max_length=255)
    recipe_url = models.URLField(max_length=1024, blank=True, default='')
    recipe_path = models.CharField(max_length=1024, blank=True, default='')
    enabled = models.BooleanField(default=False)
    renew_interval_days = models.PositiveIntegerField(default=7)
    retention_days = models.PositiveIntegerField(default=14)
    username_env = models.CharField(max_length=128, blank=True, default='')
    password_env = models.CharField(max_length=128, blank=True, default='')
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=32, blank=True, default='idle')
    last_message = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return self.title

    @property
    def cadence_label(self):
        if self.renew_interval_days == 1:
            return 'Daily'
        if self.renew_interval_days == 7:
            return 'Weekly'
        if self.renew_interval_days in {28, 30, 31}:
            return 'Monthly'
        return f'Every {self.renew_interval_days} days'

    @property
    def status_tone(self):
        if self.last_status in {'success', 'ready'}:
            return 'success'
        if self.last_status in {'error', 'fetch_failed', 'missing_calibre'}:
            return 'error'
        if self.last_status == 'running':
            return 'running'
        return 'neutral'


class PeriodicalIssue(models.Model):
    recipe = models.ForeignKey(PeriodicalRecipe, on_delete=models.CASCADE, related_name='issues')
    title = models.CharField(max_length=512)
    issue_date = models.DateField(null=True, blank=True)
    output_format = models.CharField(max_length=16, default='epub')
    archive_path = models.CharField(max_length=1024)
    debug_path = models.CharField(max_length=1024, blank=True, default='')
    device_path = models.CharField(max_length=1024, blank=True, default='')
    status = models.CharField(max_length=32, default='ready')
    status_message = models.TextField(blank=True, default='')
    is_on_device = models.BooleanField(default=False)
    is_pinned = models.BooleanField(default=False)
    fetched_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fetched_at']

    def __str__(self):
        return self.title

    @property
    def status_tone(self):
        if self.status in {'ready', 'on_device'}:
            return 'success'
        if self.status in {'error', 'missing'}:
            return 'error'
        return 'neutral'
