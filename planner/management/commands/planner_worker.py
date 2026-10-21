import fcntl
import time
from django.core.management.base import BaseCommand
from django.utils import timezone
from planner.artifacts import root
from planner.google import sync_connection
from planner.models import Connection


class Command(BaseCommand):
    help = 'Run the single planner sync worker. --once performs one cycle.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=int, default=60)

    def handle(self, *args, **options):
        with (root() / 'worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stdout.write('Planner worker is already running.')
                return
            while True:
                for connection in Connection.objects.exclude(credentials=''):
                    try:
                        sync_connection(connection)
                    except Exception:
                        connection.message = 'Google sync failed. Check connectivity or reconnect the account.'
                        connection.checked_at = timezone.now()
                        connection.save(update_fields=['message', 'checked_at'])
                        self.stderr.write(f'Planner connection {connection.pk}: sync failed; pending changes retained.')
                if options['once']:
                    break
                time.sleep(max(10, options['interval']))
