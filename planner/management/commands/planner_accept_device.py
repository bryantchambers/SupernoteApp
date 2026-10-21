from django.core.management.base import BaseCommand, CommandError
import re
from planner.models import Device


class Command(BaseCommand):
    help = 'Record successful manual device acceptance; Google writes also require PLANNER_GOOGLE_WRITES_ENABLED.'

    def add_arguments(self, parser):
        parser.add_argument('device_id')
        parser.add_argument('--firmware', help='Optional observed A6X firmware version; never inferred from a successful sync.')
        parser.add_argument('--confirm-roundtrip', action='store_true')

    def handle(self, *args, **options):
        if not options['confirm_roundtrip']:
            raise CommandError('Explicitly confirm the observed plugin round trip with --confirm-roundtrip.')
        firmware = options['firmware']
        if firmware and (not re.fullmatch(r'2\.\d+\.\d+', firmware) or tuple(map(int, firmware.split('.'))) < (2, 27, 42)):
            raise CommandError('When supplied, the observed A6X firmware must be 2.27.42 or later.')
        try:
            device = Device.objects.get(pk=options['device_id'], revoked=False)
        except (Device.DoesNotExist, ValueError):
            raise CommandError('Active device not found.')
        if not device.last_seen or device.acknowledged_revision < 1:
            raise CommandError('The plugin must connect and acknowledge a planner revision before acceptance.')
        device.validated = True
        device.save(update_fields=['validated'])
        self.stdout.write('Acceptance recorded. Previously reviewed operations still require explicit resolution.')
