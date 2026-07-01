from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0004_syncstate'),
    ]

    operations = [
        migrations.AddField(
            model_name='archiverecord',
            name='readable_path',
            field=models.CharField(blank=True, default='', max_length=1024),
        ),
    ]
