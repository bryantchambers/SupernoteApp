from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0003_filenode_is_archived'),
    ]

    operations = [
        migrations.CreateModel(
            name='SyncState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(default='supernote', max_length=64, unique=True)),
                ('status', models.CharField(default='idle', max_length=32)),
                ('direction', models.CharField(default='pull', max_length=16)),
                ('last_started_at', models.DateTimeField(blank=True, null=True)),
                ('last_finished_at', models.DateTimeField(blank=True, null=True)),
                ('last_message', models.TextField(blank=True)),
            ],
        ),
    ]
