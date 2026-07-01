from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0005_archiverecord_readable_path'),
    ]

    operations = [
        migrations.CreateModel(
            name='ZoteroSyncState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(default='zotero', max_length=64, unique=True)),
                ('status', models.CharField(default='idle', max_length=32)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('last_message', models.TextField(blank=True)),
            ],
        ),
        migrations.CreateModel(
            name='ZoteroItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('zotero_key', models.CharField(max_length=64, unique=True)),
                ('library_type', models.CharField(default='user', max_length=16)),
                ('item_type', models.CharField(blank=True, default='', max_length=64)),
                ('title', models.CharField(blank=True, default='', max_length=512)),
                ('creators', models.JSONField(blank=True, default=list)),
                ('abstract_note', models.TextField(blank=True, default='')),
                ('date', models.CharField(blank=True, default='', max_length=64)),
                ('url', models.URLField(blank=True, default='')),
                ('attachment_key', models.CharField(blank=True, default='', max_length=64)),
                ('attachment_title', models.CharField(blank=True, default='', max_length=512)),
                ('attachment_filename', models.CharField(blank=True, default='', max_length=512)),
                ('attachment_mime_type', models.CharField(blank=True, default='', max_length=128)),
                ('device_path', models.CharField(blank=True, default='', max_length=1024)),
                ('note_text', models.TextField(blank=True, default='')),
                ('raw_data', models.JSONField(blank=True, default=dict)),
                ('is_on_device', models.BooleanField(default=False)),
                ('synced_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
