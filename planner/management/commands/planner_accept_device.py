from django.core.management.base import BaseCommand, CommandError
from planner.models import Device


class Command(BaseCommand):
    help = 'Record successful manual device acceptance; Google writes also require PLANNER_GOOGLE_WRITES_ENABLED.'

    def add_arguments(self, parser):
        parser.add_argument('device_id')
        parser.add_argument('--firmware', required=True)
        parser.add_argument('--confirm-roundtrip', action='store_true')

    def handle(self, *args, **options):
        if options['firmware'] != '2.27.42' or not options['confirm_roundtrip']:
            raise CommandError('Validate A6X firmware 2.27.42 and all documented round-trip checks first.')
        try:
            device = Device.objects.get(pk=options['device_id'], revoked=False)
        except (Device.DoesNotExist, ValueError):
            raise CommandError('Active device not found.')
        if not device.last_seen:
            raise CommandError('The plugin must connect successfully before acceptance.')
        device.validated = True
        device.save(update_fields=['validated'])
        self.stdout.write('Acceptance recorded. Previously reviewed operations still require explicit resolution.')
