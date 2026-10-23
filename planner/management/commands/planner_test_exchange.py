"""Explicit operator acceptance: creates, edits, reads and trashes one test appointment."""
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from exchangelib import CalendarItem, EWSDateTime
from planner import exchange
from planner.models import Calendar


class Command(BaseCommand):
    help = 'Validate Exchange writes with one disposable appointment. Requires --confirm-create.'

    def add_arguments(self, parser):
        parser.add_argument('calendar_id', type=int)
        parser.add_argument('--confirm-create', action='store_true')

    def handle(self, *args, **options):
        if not options['confirm_create']:
            raise CommandError('Pass --confirm-create to create, update and move one test appointment to Deleted Items.')
        calendar = Calendar.objects.select_related('connection').filter(pk=options['calendar_id'], connection__provider='exchange').first()
        if not calendar or not calendar.selected or not calendar.writable or calendar.connection.suspended:
            raise CommandError('Select a writable calendar on a verified Exchange connection first.')
        try:
            with exchange.connection_lock(calendar.connection):
                client = exchange.Client(calendar.connection)
                folder = client.folder(calendar)
                if not exchange.writable(folder):
                    raise CommandError('Exchange no longer permits appointment writes in this calendar.')
                marker = str(uuid.uuid4())
                start = datetime.combine(timezone.localdate() + timedelta(days=1), time(10), ZoneInfo('Europe/Copenhagen'))
                appointment = CalendarItem(account=client.account, folder=folder, planner_id=marker,
                    subject='Supernote Planner connection test', start=EWSDateTime.from_datetime(start),
                    end=EWSDateTime.from_datetime(start + timedelta(hours=1)), is_all_day=False)
                try:
                    appointment.save(send_meeting_invitations='SendToNone')
                    loaded = client.get(folder, appointment.id)
                    if loaded.subject != appointment.subject or loaded.start != appointment.start or exchange.read_only(loaded):
                        raise CommandError('The test appointment could not be verified. Exchange writes remain unverified.')
                    loaded.subject = 'Supernote Planner connection test updated'
                    loaded.save(update_fields=['subject'], conflict_resolution='NeverOverwrite', send_meeting_invitations='SendToNone')
                    loaded.refresh()
                    if loaded.subject != 'Supernote Planner connection test updated':
                        raise CommandError('The test update could not be verified.')
                finally:
                    try:
                        for found in client.markers(folder, marker):
                            if exchange.read_only(found):
                                raise CommandError('The test appointment became a meeting; remove it in Outlook.')
                            found.move_to_trash(send_meeting_cancellations='SendToNone')
                        if client.markers(folder, marker):
                            raise CommandError('The test appointment could not be removed; check Outlook.')
                    except Exception:
                        raise CommandError('Test cleanup could not be verified. Check Outlook for “Supernote Planner connection test”; writes remain unverified.') from None
                calendar.connection.write_verified_at = timezone.now()
                calendar.connection.save(update_fields=['write_verified_at'])
        except CommandError:
            raise
        except Exception:
            raise CommandError('Exchange round-trip failed. Check the account and test appointment in Outlook; writes remain unverified.') from None
        self.stdout.write('Exchange create/read/update/trash round-trip passed. Enable PLANNER_EXCHANGE_WRITES_ENABLED only after tablet acceptance; no flag was changed.')
