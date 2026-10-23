from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('planner', '0003_pagesnapshot_regions'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AlterField(model_name='connection', name='owner', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='connection', name='provider', field=models.CharField(choices=[('google', 'Google'), ('exchange', 'KU Exchange')], default='google', max_length=16)),
        migrations.AddField(model_name='connection', name='suspended', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='connection', name='retry_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='connection', name='failures', field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name='connection', name='write_verified_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddConstraint(model_name='connection', constraint=models.UniqueConstraint(fields=('owner', 'provider'), name='planner_connection_provider')),
    ]
