import uuid
from django.conf import settings
from django.db import models


class Book(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=120, default='My planner')
    start = models.DateField()
    timezone = models.CharField(max_length=64, default='Europe/Copenhagen')
    revision = models.PositiveIntegerField(default=0)
    manifest = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class Connection(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    # Auth tokens are encrypted using a deployment key derived from SECRET_KEY.
    credentials = models.TextField(blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    message = models.CharField(max_length=250, blank=True)


class Calendar(models.Model):
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    remote_id = models.CharField(max_length=1024)
    title = models.CharField(max_length=250)
    kind = models.CharField(max_length=12, choices=[('event', 'Calendar'), ('task', 'Task list')])
    writable = models.BooleanField(default=False)
    selected = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    sync_token = models.TextField(blank=True)
    watermark = models.DateTimeField(null=True, blank=True)
    last_full_at = models.DateTimeField(null=True, blank=True)
    alias = models.SlugField(max_length=40, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['connection', 'remote_id', 'kind'], name='planner_calendar_identity')]


class Item(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    calendar = models.ForeignKey(Calendar, null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=12, choices=[('event', 'Event'), ('task', 'Task')])
    title = models.CharField(max_length=250)
    day = models.DateField()
    end_day = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    completed = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    remote_id = models.CharField(max_length=1024, blank=True)
    etag = models.CharField(max_length=250, blank=True)
    remote_snapshot = models.JSONField(default=dict)
    needs_review = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['day', 'start_time', 'title']
        constraints = [models.UniqueConstraint(fields=['book', 'calendar', 'remote_id'], condition=~models.Q(remote_id=''), name='planner_item_remote_identity')]


class Operation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    item = models.ForeignKey(Item, null=True, on_delete=models.SET_NULL)
    source = models.CharField(max_length=12, default='web')
    payload = models.JSONField(default=dict)
    source_digest = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, default='pending')
    message = models.CharField(max_length=250, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class Device(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    name = models.CharField(max_length=100, default='A6X')
    token_hash = models.CharField(max_length=64, unique=True)
    # Only an operator's explicit hardware acceptance command can set this.
    validated = models.BooleanField(default=False)
    acknowledged_revision = models.PositiveIntegerField(default=0)
    last_seen = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)


class PageSnapshot(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE)
    page_id = models.CharField(max_length=80)
    revision = models.PositiveIntegerField()
    digest = models.CharField(max_length=64)
    image_path = models.CharField(max_length=1024)
    regions = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
