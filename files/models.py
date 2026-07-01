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
    attachment_key = models.CharField(max_length=64, blank=True, default='')
    attachment_title = models.CharField(max_length=512, blank=True, default='')
    attachment_filename = models.CharField(max_length=512, blank=True, default='')
    attachment_mime_type = models.CharField(max_length=128, blank=True, default='')
    device_path = models.CharField(max_length=1024, blank=True, default='')
    note_text = models.TextField(blank=True, default='')
    raw_data = models.JSONField(default=dict, blank=True)
    is_on_device = models.BooleanField(default=False)
    synced_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title or self.zotero_key
