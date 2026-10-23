import json
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from exchangelib.errors import ErrorInvalidSyncStateData, ErrorIrresolvableConflict, UnauthorizedError

from . import exchange, google
from .forms import ItemForm
from .layout import manifest
from .models import Book, Calendar, Connection, Device, Item, Operation
from .providers import sync_connection
from .services import device_changes, save_item


def remote(**overrides):
    values = dict(id='ews-event', changekey='change-1', subject='Work appointment',
                  start=datetime(2027, 1, 5, 13, tzinfo=dt_timezone.utc),
                  end=datetime(2027, 1, 5, 14, tzinfo=dt_timezone.utc), is_all_day=False,
                  is_meeting=False, is_recurring=False, type='Single', uid='uid-1',
                  original_start=None, is_cancelled=False, planner_id='',
                  effective_rights=SimpleNamespace(modify=True, delete=True),
                  refresh=Mock(), save=Mock(), move_to_trash=Mock())
    values.update(overrides)
    return SimpleNamespace(**values)


@override_settings(SECRET_KEY='exchange-tests-private-key', PLANNER_GOOGLE_WRITES_ENABLED=True,
                   PLANNER_EXCHANGE_WRITES_ENABLED=True)
class ExchangeTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        runtime = override_settings(DATA_DIR=Path(self.temp.name))
        runtime.enable()
        self.addCleanup(runtime.disable)
        self.user = get_user_model().objects.create_user('work-owner')
        self.other = get_user_model().objects.create_user('another-owner')
        self.book = Book.objects.create(owner=self.user, start=date(2027, 1, 1), manifest=manifest(date(2027, 1, 1)))
        self.google = Connection.objects.create(owner=self.user, provider='google')
        self.connection = Connection.objects.create(owner=self.user, provider='exchange', write_verified_at=timezone.now())
        self.personal = Calendar.objects.create(connection=self.google, remote_id='primary', title='Personal',
                                                kind='event', selected=True, writable=True)
        self.work = Calendar.objects.create(connection=self.connection, remote_id='primary', title='Work',
                                            kind='event', selected=True, writable=True, alias='work')
        Device.objects.create(book=self.book, token_hash='test-token', validated=True)
        self.folder = Mock(effective_rights=SimpleNamespace(create_contents=True, modify=True, delete=True))
        self.client_adapter = Mock()
        self.client_adapter.folder.return_value = self.folder
        self.client_adapter.get.return_value = remote()
        self.client_adapter.markers.return_value = []
        self.client.force_login(self.user)

    def item(self, **kwargs):
        values = dict(book=self.book, calendar=self.work, kind='event', title='Work appointment', day=date(2027, 1, 5))
        values.update(kwargs)
        return save_item(Item(**values))

    def test_one_connection_per_provider_and_existing_google_cipher_unchanged(self):
        google.store_credentials(self.google, {'refresh_token': 'test-google-token'})
        self.assertEqual(json.loads(google.cipher().decrypt(self.google.credentials.encode()))['refresh_token'], 'test-google-token')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Connection.objects.create(owner=self.user, provider='exchange')
        self.assertEqual(Connection.objects.filter(owner=self.user).count(), 2)

    def test_encrypted_credential_stays_out_of_pages_and_device_payloads(self):
        secret = 'unique-work-secret'
        exchange.store_credentials(self.connection, {'mailbox': 'person@sund.ku.dk', 'username': 'KU\\person', 'password': secret})
        self.assertNotIn(secret, self.connection.credentials)
        self.assertEqual(exchange.credentials(self.connection)['password'], secret)
        response = self.client.get('/planner/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, secret)
        response = self.client.get('/planner/exchange/connect/', secure=True)
        self.assertNotContains(response, secret)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertTrue(self.connection.suspended)
        self.assertIsNone(self.connection.write_verified_at)

    def test_setup_requires_https_and_does_not_write_a_credential_on_http(self):
        data = {'mailbox': 'person@sund.ku.dk', 'username': 'person', 'password': 'secret'}
        with patch('planner.exchange.discover') as discover:
            result = self.client.post('/planner/exchange/connect/', data)
        self.assertContains(result, 'HTTPS address')
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.credentials, '')
        discover.assert_not_called()

    def test_setup_failure_masks_password_and_suspends_automatic_login(self):
        secret = 'unique-work-secret'
        with patch('planner.exchange.discover', side_effect=UnauthorizedError(secret)):
            response = self.client.post('/planner/exchange/connect/', {
                'mailbox': 'person@sund.ku.dk', 'username': 'person', 'password': secret}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, secret)
        self.assertContains(response, 'Automatic attempts are paused')
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.suspended)

    def test_google_worker_ignores_exchange_operations_with_identical_remote_calendar_id(self):
        item = self.item()
        with patch('planner.google.pull'), patch('planner.google.push') as push:
            google.sync_connection(self.google)
        push.assert_not_called()
        self.assertEqual(Operation.objects.get(item=item).attempts, 0)
        with self.assertRaisesRegex(ValueError, 'cannot be sent to Google'):
            google.push(Operation.objects.get(item=item))

    def test_provider_dispatch_routes_correctly(self):
        with patch('planner.google.sync_connection') as g, patch('planner.exchange.sync_connection') as e:
            sync_connection(self.google)
            sync_connection(self.connection)
        g.assert_called_once_with(self.google)
        e.assert_called_once_with(self.connection)

    def test_handwritten_work_alias_routes_to_exchange_without_changing_defaults(self):
        device = Device.objects.get(book=self.book)
        result = device_changes(device, [{'id': str(uuid.uuid4()), 'text': '14:00–15:00 Project work @work', 'day': '2027-01-05'}])
        self.assertEqual(result[0]['status'], 'pending')
        self.assertEqual(Item.objects.get(pk=result[0]['item_id']).calendar_id, self.work.pk)
        self.assertFalse(Calendar.objects.filter(is_default=True).exists())

    def test_work_read_imports_local_times_all_day_and_occurrences_without_duplicates(self):
        exchange.import_record(self.work, exchange.normalized(remote()))
        item = Item.objects.get(calendar=self.work)
        self.assertEqual(item.start_time.hour, 14)
        exchange.import_record(self.work, exchange.normalized(remote()))
        self.assertEqual(Item.objects.filter(calendar=self.work).count(), 1)
        exchange.import_record(self.work, exchange.normalized(remote(changekey='change-2', is_meeting=True)))
        item.refresh_from_db()
        self.assertTrue(item.provider_read_only)
        exchange.import_record(self.work, exchange.normalized(remote(id='all-day', uid='all-day', is_all_day=True,
            start=datetime(2027, 1, 6, 23, tzinfo=dt_timezone.utc), end=datetime(2027, 1, 8, 23, tzinfo=dt_timezone.utc))))
        item = Item.objects.get(remote_id='all-day')
        self.assertEqual(item.day, date(2027, 1, 7))
        self.assertEqual(item.end_day, date(2027, 1, 8))
        self.assertIsNone(item.start_time)

    def test_occurrence_ids_can_change_without_duplicate_items(self):
        first = remote(type='Occurrence', is_recurring=True, original_start=datetime(2027, 1, 5, 13, tzinfo=dt_timezone.utc))
        exchange.import_record(self.work, exchange.normalized(first))
        first.id, first.changekey = 'replacement-id', 'change-2'
        exchange.import_record(self.work, exchange.normalized(first))
        self.assertEqual(Item.objects.filter(calendar=self.work).count(), 1)
        self.assertEqual(Item.objects.get(calendar=self.work).remote_id, 'replacement-id')

    def test_remote_change_conflicts_with_pending_local_edit(self):
        exchange.import_record(self.work, exchange.normalized(remote()))
        item = Item.objects.get(calendar=self.work)
        item.title = 'Local edit'
        save_item(item, expected=item.version)
        exchange.import_record(self.work, exchange.normalized(remote(changekey='change-2', subject='Remote edit')))
        self.assertEqual(Operation.objects.get(item=item).status, 'conflict')
        item.refresh_from_db()
        self.assertEqual(item.title, 'Local edit')

    def test_partial_fetch_keeps_existing_items_and_previous_cursor(self):
        exchange.import_record(self.work, exchange.normalized(remote()))
        self.work.sync_token = json.dumps({'state': 'previous'})
        self.work.save()
        self.client_adapter.changes.return_value = (True, 'new-state')
        self.client_adapter.window.side_effect = RuntimeError('page two failed')
        with self.assertRaises(RuntimeError):
            exchange.pull(self.work, self.client_adapter)
        self.work.refresh_from_db()
        self.assertEqual(json.loads(self.work.sync_token)['state'], 'previous')
        self.assertFalse(Item.objects.get(calendar=self.work).deleted)

    def test_complete_empty_refresh_marks_remote_deleted_but_retains_pending_local_edits(self):
        exchange.import_record(self.work, exchange.normalized(remote()))
        self.client_adapter.changes.return_value = (True, 'next')
        self.client_adapter.window.return_value = []
        exchange.pull(self.work, self.client_adapter)
        self.assertTrue(Item.objects.get(calendar=self.work).deleted)

    def test_readonly_meetings_block_web_pen_and_direct_writes_even_with_review_approval(self):
        exchange.import_record(self.work, exchange.normalized(remote(is_meeting=True)))
        item = Item.objects.get(calendar=self.work)
        with self.assertRaisesRegex(ValueError, 'read only'):
            save_item(item, expected=item.version)
        response = self.client.post(f'/planner/items/{item.pk}/remove/', {'version': item.version})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertFalse(item.deleted)
        result = device_changes(Device.objects.get(book=self.book), [{'id': str(uuid.uuid4()), 'action': 'cancel', 'item_id': str(item.pk), 'version': item.version}])
        self.assertEqual(result[0]['status'], 'review')
        op = Operation.objects.create(book=self.book, item=item, status='review', payload={'version': item.version, 'review_approved': True})
        self.client.post(f'/planner/review/{op.pk}/', {'action': 'approve'})
        op.refresh_from_db()
        self.assertEqual(op.status, 'review')
        with self.assertRaisesRegex(exchange.ExchangeError, 'Outlook'):
            exchange.push(op, self.client_adapter)
        self.client_adapter.get.assert_not_called()

    def test_synced_entry_cannot_be_moved_to_another_provider(self):
        exchange.import_record(self.work, exchange.normalized(remote()))
        item = Item.objects.get(calendar=self.work)
        item.calendar = self.personal
        with self.assertRaisesRegex(ValueError, 'Moving a synced entry'):
            save_item(item, expected=item.version)

    def test_changed_remote_key_and_new_meeting_are_not_overwritten(self):
        item = self.item(remote_id='ews-event', etag='change-1')
        op = Operation.objects.get(item=item)
        self.client_adapter.get.return_value = remote(changekey='newer')
        with self.assertRaises(ErrorIrresolvableConflict):
            exchange.push(op, self.client_adapter)
        self.client_adapter.get.return_value = remote(is_meeting=True)
        with self.assertRaisesRegex(exchange.ExchangeError, 'meeting'):
            exchange.push(op, self.client_adapter)

    def test_marker_recovers_ambiguous_create_and_uses_never_overwrite(self):
        item = self.item()
        marker_match = remote(is_all_day=True, start=exchange.appointment_fields(item)['start'], end=exchange.appointment_fields(item)['end'])
        self.client_adapter.markers.return_value = [marker_match]
        with patch('planner.exchange.CalendarItem') as constructor:
            exchange.push(Operation.objects.get(item=item), self.client_adapter)
        constructor.assert_not_called()
        marker_match.save.assert_called_once()
        self.assertEqual(marker_match.save.call_args.kwargs['conflict_resolution'], 'NeverOverwrite')
        self.assertEqual(marker_match.save.call_args.kwargs['send_meeting_invitations'], 'SendToNone')
        item.refresh_from_db()
        self.assertEqual(item.remote_id, marker_match.id)

    def test_duplicate_marker_blocks_creation(self):
        item = self.item()
        self.client_adapter.markers.return_value = [remote(), remote()]
        with self.assertRaisesRegex(exchange.ExchangeError, 'Duplicate'):
            exchange.push(Operation.objects.get(item=item), self.client_adapter)

    def test_new_appointment_has_stable_marker_and_sends_no_invitation(self):
        item = self.item()
        created = remote(planner_id=str(item.pk))
        with patch('planner.exchange.CalendarItem', return_value=created) as constructor:
            exchange.push(Operation.objects.get(item=item), self.client_adapter)
        self.assertEqual(constructor.call_args.kwargs['planner_id'], str(item.pk))
        self.assertNotIn('required_attendees', constructor.call_args.kwargs)
        created.save.assert_called_once_with(send_meeting_invitations='SendToNone')
        self.assertEqual(Operation.objects.get(item=item).status, 'synced')

    def test_dense_calendar_view_splits_and_fetches_both_halves(self):
        client = object.__new__(exchange.Client)
        client.account = Mock()
        first, second = remote(id='first'), remote(id='second')
        client.account.fetch.side_effect = [[first], [second]]
        service = Mock()
        service.call.side_effect = [exchange.IncompleteView('partial'), [('first', 'key')], [('second', 'key')]]
        start = datetime(2027, 1, 1, tzinfo=dt_timezone.utc)
        with patch('planner.exchange.CompleteFindItem', return_value=service):
            result = client.window(self.folder, start, start + timedelta(days=2))
        self.assertEqual(result, [first, second])
        self.assertEqual(service.call.call_count, 3)

    def test_client_uses_fixed_verified_endpoint_without_autodiscovery(self):
        exchange.store_credentials(self.connection, {'mailbox': 'person@sund.ku.dk', 'username': 'person', 'password': 'test-secret'})
        with patch('planner.exchange.Account') as account:
            exchange.Client(self.connection)
        arguments = account.call_args.kwargs
        self.assertFalse(arguments['autodiscover'])
        self.assertEqual(arguments['config'].service_endpoint, 'https://mail.ku.dk/EWS/Exchange.asmx')
        self.assertEqual(arguments['config'].auth_type, 'NTLM')
        self.assertEqual(arguments['config'].credentials.username, 'person')

    def test_existing_folder_uses_library_root_to_resolve_its_account(self):
        # Construct the real library model: mocking the whole client misses
        # read-only model properties such as Folder.account.
        client = object.__new__(exchange.Client)
        account = SimpleNamespace()
        account.root = SimpleNamespace(account=account)
        client.account = account
        with patch.object(exchange.EWSCalendar, 'refresh') as refresh:
            folder = client.folder(self.work)
        self.assertEqual(folder.id, self.work.remote_id)
        self.assertIs(folder.account, account)
        refresh.assert_called_once()

    def test_write_test_requires_explicit_creation_confirmation(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with patch('planner.exchange.Client') as client:
            with self.assertRaisesRegex(CommandError, 'confirm-create'):
                call_command('planner_test_exchange', self.work.pk)
        client.assert_not_called()

    def test_exchange_failure_does_not_stop_google_worker_cycle(self):
        from django.core.management import call_command
        import io
        Connection.objects.filter(owner=self.user).update(credentials='test-only-cipher')
        processed = []
        def sync(connection):
            processed.append(connection.provider)
            if connection.provider == 'exchange':
                raise RuntimeError('test failure')
        with patch('planner.management.commands.planner_worker.sync_connection', side_effect=sync):
            call_command('planner_worker', once=True, stdout=io.StringIO(), stderr=io.StringIO())
        self.assertCountEqual(processed, ['google', 'exchange'])

    def test_delete_uses_trash_without_cancellations(self):
        item = self.item(remote_id='ews-event', etag='change-1', deleted=True)
        r = self.client_adapter.get.return_value
        exchange.push(Operation.objects.get(item=item), self.client_adapter)
        r.move_to_trash.assert_called_once_with(send_meeting_cancellations='SendToNone')
        self.assertEqual(Operation.objects.get(item=item).status, 'synced')

    def test_unverified_connection_keeps_accepted_writes_queued(self):
        self.item()
        self.connection.write_verified_at = None
        self.connection.save()
        with patch('planner.exchange.Client', return_value=self.client_adapter), patch('planner.exchange.pull'), patch('planner.exchange.push') as push:
            exchange.sync_connection(self.connection)
        push.assert_not_called()
        self.assertEqual(Operation.objects.get(book=self.book).attempts, 0)

    def test_bad_auth_suspends_retries_and_other_failures_back_off_without_leaking(self):
        exchange.record_failure(self.connection, UnauthorizedError('secret password'))
        self.assertTrue(self.connection.suspended)
        self.assertNotIn('secret password', self.connection.message)
        exchange.record_failure(self.connection, RuntimeError('secret XML'))
        self.assertGreater(self.connection.retry_at, timezone.now())
        self.assertNotIn('secret XML', self.connection.message)

    def test_disconnect_destroys_credential_but_preserves_google_and_pending_work(self):
        self.item()
        google.store_credentials(self.google, {'refresh_token': 'retain-google'})
        previous = self.google.credentials
        self.client.post('/planner/exchange/disconnect/')
        self.connection.refresh_from_db()
        self.google.refresh_from_db()
        self.assertEqual(self.connection.credentials, '')
        self.assertEqual(self.google.credentials, previous)
        self.assertEqual(Operation.objects.get(book=self.book).status, 'pending')
        self.assertTrue(Item.objects.filter(book=self.book).exists())

    def test_calendar_selection_aliases_unique_across_providers(self):
        response = self.client.post('/planner/calendars/select/', {
            'selected': [self.personal.pk, self.work.pk], f'alias_{self.personal.pk}': 'work', f'alias_{self.work.pk}': 'work'})
        self.assertEqual(response.status_code, 302)
        self.personal.refresh_from_db()
        self.assertEqual(self.personal.alias, '')

    def test_foreign_work_calendar_cannot_be_used(self):
        foreign = Calendar.objects.create(connection=Connection.objects.create(owner=self.other, provider='exchange'),
                                          remote_id='foreign', kind='event', writable=True, selected=True, title='Other work')
        with self.assertRaisesRegex(ValueError, 'another account'):
            self.item(calendar=foreign)

    def test_complete_calendar_view_rejects_partial_metadata(self):
        from lxml import etree
        with self.assertRaises(exchange.IncompleteView):
            exchange.CompleteFindItem._get_paging_values(etree.fromstring(b'<RootFolder TotalItemsInView="10" IncludesLastItemInRange="false"/>'))
        self.assertEqual(exchange.CompleteFindItem._get_paging_values(etree.fromstring(
            b'<RootFolder TotalItemsInView="10" IncludesLastItemInRange="true"/>')), (10, None))

    def test_expired_sync_state_resets_without_advancing_until_complete(self):
        folder = Mock(item_sync_state=None)
        def reset(**kwargs):
            if kwargs.get('sync_state'):
                raise ErrorInvalidSyncStateData('expired')
            folder.item_sync_state = 'fresh'
            return iter([])
        folder.sync_items.side_effect = reset
        changed, cursor = exchange.Client.changes(Mock(), folder, 'expired')
        self.assertTrue(changed)
        self.assertEqual(cursor, 'fresh')


class ConnectionMigrationTests(TransactionTestCase):
    def test_google_identity_and_credentials_survive_migration(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        executor.migrate([('planner', '0003_pagesnapshot_regions')])
        try:
            old = executor.loader.project_state([('planner', '0003_pagesnapshot_regions')]).apps
            user = old.get_model('auth', 'User').objects.create(username='migration-owner')
            row = old.get_model('planner', 'Connection').objects.create(owner_id=user.pk, credentials='preserve-encrypted-token')
            cal = old.get_model('planner', 'Calendar').objects.create(connection_id=row.pk, remote_id='google-primary', title='Personal', kind='event', selected=True, alias='personal')
            executor = MigrationExecutor(connection)
            executor.migrate([('planner', '0004_exchange_connections')])
            current = Connection.objects.get(pk=row.pk)
            self.assertEqual(current.provider, 'google')
            self.assertEqual(current.credentials, 'preserve-encrypted-token')
            self.assertEqual(Calendar.objects.get(pk=cal.pk).connection_id, row.pk)
            self.assertTrue(Calendar.objects.get(pk=cal.pk).selected)
            Connection.objects.create(owner_id=user.pk, provider='exchange')
        finally:
            MigrationExecutor(connection).migrate([('planner', '0004_exchange_connections')])
