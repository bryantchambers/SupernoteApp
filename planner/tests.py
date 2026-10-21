import io
import json
import tempfile
import uuid
from datetime import date, time, timedelta
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from .artifacts import generate_pdf
from .forms import ItemForm
from .google import GoogleError, import_record, pull, push, store_credentials
from .layout import holidays, manifest
from .models import Book, Calendar, Connection, Device, Item, Operation
from .recognition import parse_entry
from .services import device_changes, pair, save_item


class PlannerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('planner', password='test-password')
        self.other = get_user_model().objects.create_user('other')
        self.book = Book.objects.create(owner=self.user, title='2027', start=date(2027, 1, 1), manifest=manifest(date(2027, 1, 1)))
        self.connection = Connection.objects.create(owner=self.user)
        self.cal = Calendar.objects.create(connection=self.connection, remote_id='primary', title='Personal', kind='event', selected=True, writable=True, is_default=True)
        self.device, self.token = pair(self.book)
        self.device.validated = True
        self.device.save()

    def test_dashboard_renders_reviews_with_missing_payload_keys_and_escapes_handwriting(self):
        self.client.force_login(self.user)
        for payload, expected in [
            ({'action': 'capture', 'text': '17:00 Dinner'}, '17:00 Dinner'),
            ({'title': 'Meeting title'}, 'Meeting title'),
            ({'action': 'cancel'}, 'cancel'),
            ({}, 'Planner change'),
            ({'text': '<script>alert(1)</script>'}, '&lt;script&gt;alert(1)&lt;/script&gt;'),
        ]:
            with self.subTest(payload=payload):
                op = Operation.objects.create(book=self.book, source='device', status='review', payload=payload)
                response = self.client.get('/planner/', {'book': self.book.pk})
                self.assertContains(response, expected)
                self.assertNotContains(response, '<script>alert(1)</script>')
                op.delete()

    def test_handwriting_review_prefills_entry_and_accepts_once_without_publishing(self):
        self.device.validated = False
        self.device.save()
        raw = {'id': str(uuid.uuid4()), 'action': 'capture', 'day': '2027-01-05', 'text': '17:00 - 20:00 Dinner w/ - I'}
        device_changes(self.device, [raw])
        op = Operation.objects.get(pk=raw['id'])
        self.client.force_login(self.user)
        self.assertContains(self.client.get('/planner/'), 'Review &amp; accept')
        page = self.client.get(f'/planner/review/{op.pk}/')
        self.assertContains(page, 'Accept entry')
        self.assertEqual(page.context['form'].initial['title'], 'Dinner w/ - I')
        self.assertEqual(page.context['form'].initial['start_time'], '17:00:00')
        self.assertEqual(page.context['form'].initial['calendar'], self.cal.pk)
        data = {'action': 'accept_capture', 'kind': 'event', 'title': 'Dinner with friends',
                'day': '2027-01-05', 'start_time': '17:00', 'end_time': '20:00', 'calendar': self.cal.pk}
        with patch('planner.google.Client.request') as network:
            self.assertEqual(self.client.post(f'/planner/review/{op.pk}/', data).status_code, 302)
            self.assertEqual(self.client.post(f'/planner/review/{op.pk}/', data).status_code, 302)
            network.assert_not_called()
        op.refresh_from_db()
        self.assertEqual(op.status, 'accepted')
        self.assertEqual(op.payload, raw)
        self.assertEqual(Item.objects.count(), 1)
        self.assertEqual(op.item.title, 'Dinner with friends')
        self.assertEqual(Operation.objects.filter(status='pending', item=op.item).count(), 1)
        receipt = device_changes(self.device, [raw])[0]
        self.assertEqual(receipt['item_id'], str(op.item_id))
        self.assertEqual(Item.objects.count(), 1)

    def test_capture_review_invalid_input_keeps_editable_form_and_proposal(self):
        op = Operation.objects.create(book=self.book, source='device', status='review',
            payload={'action': 'capture', 'text': 'Unclear note', 'day': '2027-01-05'})
        foreign = Calendar.objects.create(connection=Connection.objects.create(owner=self.other), remote_id='foreign',
            title='Other owner', kind='event', selected=True, writable=True)
        self.client.force_login(self.user)
        for change in ({'title': ''}, {'calendar': foreign.pk}, {'day': '2030-01-01'}, {'start_time': '18:00', 'end_time': '17:00'}):
            with self.subTest(change=change):
                data = {'action': 'accept_capture', 'kind': 'event', 'title': 'Corrected note', 'day': '2027-01-05', 'calendar': self.cal.pk}
                data.update(change)
                response = self.client.post(f'/planner/review/{op.pk}/', data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assertFalse(Item.objects.exists())
                op.refresh_from_db()
                self.assertEqual(op.status, 'review')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/planner/review/{op.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/planner/review/{op.pk}/', data).status_code, 404)

    def test_capture_review_supports_tasks_aliases_and_local_entries(self):
        tasks = Calendar.objects.create(connection=self.connection, remote_id='tasks', title='Tasks', kind='task',
            selected=True, writable=True, is_default=True, alias='chores')
        op = Operation.objects.create(book=self.book, source='device', status='review',
            payload={'action': 'capture', 'text': '[ ] Buy milk @chores', 'day': '2027-01-05'})
        self.client.force_login(self.user)
        form = self.client.get(f'/planner/review/{op.pk}/').context['form']
        self.assertEqual(form.initial['kind'], 'task')
        self.assertEqual(form.initial['calendar'], tasks.pk)
        response = self.client.post(f'/planner/review/{op.pk}/', {'action': 'accept_capture', 'kind': 'task',
            'title': 'Buy milk', 'day': '2027-01-05', 'calendar': '', 'completed': 'on'})
        self.assertEqual(response.status_code, 302)
        op.refresh_from_db()
        self.assertTrue(op.item.completed)
        self.assertIsNone(op.item.calendar_id)
        self.assertEqual(Operation.objects.get(item=op.item, source='web').status, 'local')

    def test_calendar_gates_keep_accepted_edits_pending_without_attempting_google_writes(self):
        from .google import sync_connection
        for enabled, validated in ((False, True), (True, False)):
            with self.subTest(enabled=enabled), override_settings(PLANNER_GOOGLE_WRITES_ENABLED=enabled):
                self.device.validated = validated
                self.device.save()
                item = save_item(Item(book=self.book, calendar=self.cal, kind='event', title='Reviewed entry', day=date(2027, 1, 5)))
                op = Operation.objects.get(item=item)
                with patch('planner.google.pull'), patch('planner.google.push') as network:
                    sync_connection(self.connection)
                    network.assert_not_called()
                op.refresh_from_db()
                self.assertEqual(op.status, 'pending')
                self.assertEqual(op.attempts, 0)

    def test_device_acceptance_requires_explicit_confirmed_sync_and_does_not_guess_firmware(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        self.device.validated = False
        self.device.save()
        with self.assertRaises(CommandError):
            call_command('planner_accept_device', str(self.device.pk), confirm_roundtrip=True, stdout=io.StringIO())
        self.device.last_seen = timezone.now()
        self.device.acknowledged_revision = 1
        self.device.save()
        with self.assertRaises(CommandError):
            call_command('planner_accept_device', str(self.device.pk), stdout=io.StringIO())
        for firmware in ('2.26.39', '3.29.43', 'unknown'):
            with self.subTest(firmware=firmware), self.assertRaises(CommandError):
                call_command('planner_accept_device', str(self.device.pk), firmware=firmware, confirm_roundtrip=True, stdout=io.StringIO())
        call_command('planner_accept_device', str(self.device.pk), confirm_roundtrip=True, stdout=io.StringIO())
        self.device.refresh_from_db()
        self.assertTrue(self.device.validated)
        call_command('planner_accept_device', str(self.device.pk), firmware='2.28.42', confirm_roundtrip=True, stdout=io.StringIO())

    def test_pairing_file_is_owner_scoped_private_and_contains_only_device_connection(self):
        self.client.force_login(self.user)
        with tempfile.TemporaryDirectory() as temp, override_settings(DATA_DIR=Path(temp), PLANNER_PUBLIC_ORIGIN='https://planner.example:8443'):
            folder = Path(temp)/'planner'
            folder.mkdir()
            (folder/'ca.crt').write_text('-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----')
            response = self.client.post(f'/planner/book/{self.book.pk}/pair/', {'format':'json'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response['Cache-Control'],'no-store')
            self.assertIn('attachment;',response['Content-Disposition'])
            value=response.json()
            self.assertEqual(set(value),{'schema','origin','bookId','token','caPem'})
            self.assertEqual(value['bookId'],str(self.book.pk))
            self.assertEqual(value['origin'],'https://planner.example:8443')
            import hashlib
            device=Device.objects.get(token_hash=hashlib.sha256(value['token'].encode()).hexdigest())
            self.assertEqual(device.book_id,self.book.pk)
            self.client.force_login(self.other)
            before=Device.objects.count()
            self.assertEqual(self.client.post(f'/planner/book/{self.book.pk}/pair/',{'format':'json'}).status_code,404)
            self.assertEqual(Device.objects.count(),before)
            self.client.force_login(self.user)
            (folder/'ca.crt').unlink()
            self.assertEqual(self.client.post(f'/planner/book/{self.book.pk}/pair/',{'format':'json'}).status_code,503)
            self.assertEqual(Device.objects.count(),before)

    def test_manifest_has_each_day_once_and_unique_links(self):
        for start, months in ((date(2027, 1, 1), 12), (date(2028, 1, 1), 12), (date(2027, 10, 1), 12), (date(2026, 10, 1), 15)):
            value = manifest(start, months)
            self.assertEqual(sum(p['kind'] == 'month' for p in value['pages']), months)
            if months == 15:
                self.assertEqual(value['end'], '2028-01-01')
            ids = [p['id'] for p in value['pages']]
            self.assertEqual(len(ids), len(set(ids)))
            days = [r['date'] for p in value['pages'] if p['kind'] == 'week' for r in p['regions']]
            self.assertEqual(len(days), len(set(days)))
            last = date.fromisoformat(value['end'])
            self.assertEqual(sum(start <= date.fromisoformat(d) < last for d in days), (last-start).days)
        self.assertIn('2028-02-29', days if start.year == 2028 else [r['date'] for p in manifest(date(2028, 1, 1))['pages'] if p['kind'] == 'week' for r in p['regions']])

    def test_holidays_match_known_dates_and_observed_year_boundary(self):
        values = holidays(2027)
        self.assertIn('DK · Maundy Thursday', values['2027-03-25'])
        self.assertIn('US observed · Juneteenth', values['2027-06-18'])
        self.assertIn("US observed · New Year's Day", values['2027-12-31'])
        self.assertFalse(any('Prayer' in label for key, labels in values.items() if key.startswith('2027') for label in labels))

    def test_explicit_grammar_and_dst_safety(self):
        value = parse_entry('14:00–15:00 Dentist @personal', '2027-01-05')
        self.assertEqual(value['start_time'], '14:00:00')
        self.assertEqual(value['alias'], 'personal')
        self.assertEqual(parse_entry('□ Buy milk', '2027-01-05')['kind'], 'task')
        self.assertTrue(parse_entry('[x] Buy milk', '2027-01-05')['completed'])
        self.assertEqual(parse_entry('All day: Birthday', '2027-01-05')['title'], 'Birthday')
        for text, day in [('Just an ordinary note', '2027-01-05'), ('02:30 Appointment', '2027-03-28'), ('02:30 Appointment', '2027-10-31'), ('15:00-14:00 Dentist', '2027-01-05')]:
            with self.assertRaises(ValueError):
                parse_entry(text, day)

    def test_device_replay_is_idempotent_and_changed_replay_rejected(self):
        raw = {'id': str(uuid.uuid4()), 'text': '14:00 Dentist', 'day': '2027-01-05'}
        first = device_changes(self.device, [raw])
        self.assertEqual(device_changes(self.device, [raw]), first)
        self.assertEqual(Item.objects.count(), 1)
        with self.assertRaises(ValueError):
            device_changes(self.device, [{**raw, 'text': '15:00 Different'}])

    def test_unvalidated_device_and_notes_are_reviewed_without_items(self):
        self.device.validated = False
        self.device.save()
        device_changes(self.device, [{'id': str(uuid.uuid4()), 'text': '14:00 Dentist', 'day': '2027-01-05'}])
        self.assertFalse(Item.objects.exists())
        self.assertEqual(Operation.objects.get().status, 'review')

    def test_stale_cancel_does_not_delete_and_current_cancel_does(self):
        item = save_item(Item(book=self.book, calendar=self.cal, kind='event', day=date(2027, 1, 5), title='Dentist'))
        raw = {'id': str(uuid.uuid4()), 'action': 'cancel', 'item_id': str(item.id), 'version': 0}
        self.assertEqual(device_changes(self.device, [raw])[0]['status'], 'review')
        item.refresh_from_db()
        self.assertFalse(item.deleted)
        raw.update(id=str(uuid.uuid4()), version=item.version)
        device_changes(self.device, [raw])
        item.refresh_from_db()
        self.assertTrue(item.deleted)

    def test_browser_auth_owner_isolation_and_complete_dashboard_render(self):
        self.assertEqual(self.client.get('/planner/').status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/planner/book/{self.book.id}/download/manifest/').status_code, 404)
        self.client.force_login(self.user)
        response = self.client.get('/planner/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Daily Planner')
        self.assertContains(response, 'Monday 28 December')

    def test_device_auth_schema_validation_and_bad_ack_no_side_effects(self):
        raw = {'id': str(uuid.uuid4()), 'text': '14:00 Dentist', 'day': '2027-01-05'}
        payload = {'schema': 1, 'book_id': str(self.book.id), 'operations': [raw], 'acknowledged_revision': 100}
        self.assertEqual(self.client.post('/planner/api/v1/sync/', json.dumps(payload), content_type='application/json').status_code, 401)
        response = self.client.post('/planner/api/v1/sync/', json.dumps(payload), content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Item.objects.exists())
        payload['acknowledged_revision'] = 0
        response = self.client.post('/planner/api/v1/sync/', json.dumps(payload), content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['items']), 1)
        Operation.objects.create(book=self.book, source='device', payload={'text':'Needs review'}, status='review')
        payload['operations'] = []
        response = self.client.post('/planner/api/v1/sync/', json.dumps(payload), content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(response.json()['review_count'], 1)
        self.device.revoked = True
        self.device.save()
        self.assertEqual(self.client.post('/planner/api/v1/sync/', '{}', content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + self.token).status_code, 401)

    def test_form_rejects_other_owners_calendar_and_task_times(self):
        other = Connection.objects.create(owner=self.other)
        calendar = Calendar.objects.create(connection=other, remote_id='private', title='Private', kind='event', selected=True, writable=True)
        form = ItemForm({'kind': 'event', 'title': 'No', 'day': '2027-01-05', 'calendar': calendar.pk}, book=self.book)
        self.assertFalse(form.is_valid())
        form = ItemForm({'kind': 'task', 'title': 'No', 'day': '2027-01-05', 'start_time': '14:00', 'end_time': '15:00'}, book=self.book)
        self.assertFalse(form.is_valid())

    def test_google_writes_gated_and_ids_stable(self):
        item = save_item(Item(book=self.book, calendar=self.cal, kind='event', day=date(2027, 1, 5), title='Dentist'))
        op = Operation.objects.get(item=item)
        with patch('planner.google.Client.request') as call:
            with self.assertRaises(ValueError):
                push(op)
            call.assert_not_called()
        with override_settings(PLANNER_GOOGLE_WRITES_ENABLED=True), patch('planner.google.Client.request', return_value={'id': item.id.hex, 'etag': 'etag1'}) as call:
            push(op)
            self.assertEqual(call.call_args.kwargs['json']['id'], item.id.hex)
            self.assertEqual(call.call_args.kwargs['json']['end']['date'], '2027-01-06')

    def test_google_pending_remote_change_creates_conflict(self):
        item = save_item(Item(book=self.book, calendar=self.cal, kind='event', day=date(2027, 1, 5), title='Dentist', remote_id='remote', etag='old', remote_snapshot={'id': 'remote', 'etag': 'old'}))
        import_record(self.cal, {'id': 'remote', 'etag': 'new', 'summary': 'Changed', 'start': {'date': '2027-01-05'}, 'end': {'date': '2027-01-06'}})
        self.assertEqual(Operation.objects.get(item=item).status, 'conflict')
        item.refresh_from_db()
        self.assertEqual(item.title, 'Dentist')

    def test_google_sync_token_only_commits_complete_pagination(self):
        pages = [{'items': [], 'nextSyncToken': 'new'}]
        with patch('planner.google.Client.pages', return_value=iter(pages)):
            pull(self.cal)
        self.cal.refresh_from_db()
        self.assertEqual(self.cal.sync_token, 'new')
        with patch('planner.google.Client.pages', side_effect=GoogleError(503)):
            with self.assertRaises(GoogleError):
                pull(self.cal)
        self.cal.refresh_from_db()
        self.assertEqual(self.cal.sync_token, 'new')

    def test_google_410_rebuild_retains_pending_local_change(self):
        self.cal.sync_token = 'expired'
        self.cal.save()
        item = save_item(Item(book=self.book, calendar=self.cal, kind='event', day=date(2027, 1, 5), title='Keep', remote_id='remote'))
        with patch('planner.google.Client.pages', side_effect=[GoogleError(410), iter([{'items': [], 'nextSyncToken': 'fresh'}])]):
            pull(self.cal)
        item.refresh_from_db()
        self.assertFalse(item.deleted)
        self.assertEqual(Operation.objects.get(item=item).status, 'pending')

    def test_credentials_are_encrypted_and_not_in_dashboard(self):
        store_credentials(self.connection, {'refresh_token': 'secret-test-token'})
        self.connection.refresh_from_db()
        self.assertNotIn('secret-test-token', self.connection.credentials)
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get('/planner/'), 'secret-test-token')

    def test_tasks_completion_and_alias_target(self):
        tasks = Calendar.objects.create(connection=self.connection, remote_id='tasks', title='Tasks', kind='task', selected=True, writable=True, is_default=True, alias='todo')
        device_changes(self.device, [{'id': str(uuid.uuid4()), 'text': '□ Buy milk @todo', 'day': '2027-01-05'}])
        item = Item.objects.get()
        self.assertEqual(item.calendar, tasks)
        device_changes(self.device, [{'id': str(uuid.uuid4()), 'action': 'complete', 'item_id': str(item.pk), 'version': item.version}])
        item.refresh_from_db()
        self.assertTrue(item.completed)

    def test_planner_push_exclusion_and_native_history(self):
        from files.services import run_rclone_sync, archive_file_node
        from files.models import FileNode
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as temp, override_settings(SUPERNOTE_SOURCE=Path(temp)/'live', ARCHIVE_DIR=Path(temp)/'archive'):
            path = Path(temp)/'live/Note/Planner/2027.note'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'native strokes')
            with patch('files.services.subprocess.run', return_value=MagicMock(returncode=0,stdout='',stderr='')) as call:
                run_rclone_sync('push')
                self.assertEqual(call.call_args.args[0][-2:], ['--exclude','/Note/Planner/**'])
                run_rclone_sync('pull')
            snapshots = list((Path(temp)/'archive/planner-versions').glob('*.note'))
            self.assertEqual(snapshots[0].read_bytes(), b'native strokes')
            node = FileNode(path='Note', is_directory=True)
            with self.assertRaises(RuntimeError):
                archive_file_node(node)

    def test_previews_private_versioned_and_visible_on_paper(self):
        stream = io.BytesIO()
        Image.new('RGB',(140,187),'white').save(stream,format='PNG')
        with tempfile.TemporaryDirectory() as temp, override_settings(DATA_DIR=Path(temp)):
            response = self.client.post('/planner/api/v1/preview/',stream.getvalue(),content_type='image/png',HTTP_AUTHORIZATION='Bearer '+self.token,HTTP_X_PLANNER_PAGE='week-2026-12-28-a',HTTP_X_PLANNER_REVISION='0')
            self.assertEqual(response.status_code,200)
            self.client.force_login(self.user)
            self.assertContains(self.client.get('/planner/'),'Handwritten page')
            from .models import PageSnapshot
            snap = PageSnapshot.objects.get()
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(f'/planner/preview/{snap.pk}/').status_code,404)

    def test_calendar_deselection_removes_device_render_without_remote_delete(self):
        item = Item.objects.create(book=self.book,calendar=self.cal,kind='event',title='Hidden',day=date(2027,1,5))
        self.cal.selected = False
        self.cal.save()
        item.refresh_from_db()
        from .services import encode_item
        self.assertTrue(encode_item(item)['deleted'])
        self.assertFalse(item.deleted)

    def test_stale_web_form_does_not_overwrite_newer_item(self):
        item = save_item(Item(book=self.book,kind='event',title='Original',day=date(2027,1,5)))
        original_version = item.version
        item.title = 'Newer'
        save_item(item,original_version)
        self.client.force_login(self.user)
        self.client.post(f'/planner/book/{self.book.pk}/items/{item.pk}/',{'title':'Stale','kind':'event','day':'2027-01-05','version':original_version})
        item.refresh_from_db()
        self.assertEqual(item.title,'Newer')

    def test_upload_cannot_traverse_into_device_owned_planner(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        with tempfile.TemporaryDirectory() as temp, override_settings(SUPERNOTE_SOURCE=Path(temp)):
            response = self.client.post('/upload/', {'path': 'Document/../Note/Planner', 'file': SimpleUploadedFile('2027.note',b'overwrite')})
            self.assertEqual(response.status_code,400)
            self.assertFalse((Path(temp)/'Note/Planner/2027.note').exists())

    def test_plugin_handoff_is_authenticated_and_excludes_state(self):
        import tarfile
        self.assertEqual(self.client.get('/planner/device/source/').status_code,302)
        self.client.force_login(self.user)
        response = self.client.get('/planner/device/source/')
        self.assertEqual(response.status_code,200)
        with tarfile.open(fileobj=io.BytesIO(b''.join(response.streaming_content)),mode='r:gz') as archive:
            names = archive.getnames()
        self.assertIn('device-planner/Dockerfile',names)
        self.assertFalse(any('state.enc' in n or '.pyc' in n for n in names))

    def test_original_handwriting_reference_can_cancel_before_first_google_sync(self):
        source_id = str(uuid.uuid4())
        result = device_changes(self.device,[{'id':source_id,'action':'capture','day':'2027-01-05','text':'14:00 Dentist',
            'source_region':{'page_id':'week-2027-01-04-a','rect':[.1,.25,.4,.02]}},
            {'id':str(uuid.uuid4()),'action':'cancel','item_operation':source_id,'version':1}])
        self.assertEqual(len(result),2)
        self.assertTrue(Item.objects.get().deleted)
        self.assertEqual(result[0]['item_id'],result[1]['item_id'])
        self.assertIn('source_region',Operation.objects.get(pk=source_id).payload)

    def test_crossed_out_new_capture_requires_review_not_creation(self):
        result = device_changes(self.device,[{'id':str(uuid.uuid4()),'text':'14:00 Dentist','day':'2027-01-05','uncertain':True}])
        self.assertEqual(result[0]['status'],'review')
        self.assertFalse(Item.objects.exists())

    def test_preview_regions_cannot_reference_another_users_item(self):
        other_book = Book.objects.create(owner=self.other,start=self.book.start,manifest=self.book.manifest)
        other_item = Item.objects.create(book=other_book,day=date(2027,1,5),title='Private',kind='event')
        stream = io.BytesIO();Image.new('RGB',(140,187),'white').save(stream,format='PNG')
        with tempfile.TemporaryDirectory() as temp,override_settings(DATA_DIR=Path(temp)):
            response = self.client.post('/planner/api/v1/preview/',stream.getvalue(),content_type='image/png',HTTP_AUTHORIZATION='Bearer '+self.token,
                HTTP_X_PLANNER_PAGE='week-2027-01-04-a',HTTP_X_PLANNER_REVISION='0',HTTP_X_PLANNER_REGIONS=json.dumps({'regions':[{'item_id':str(other_item.pk),'rect':[.1,.2,.3,.04]}]}))
            self.assertEqual(response.status_code,400)

    def test_dismissed_conflict_reloads_even_when_google_etag_is_unchanged(self):
        item = save_item(Item(book=self.book,calendar=self.cal,kind='event',title='Local edit',day=date(2027,1,5),remote_id='remote',etag='same'))
        op = Operation.objects.get(item=item);op.status='conflict';op.save()
        self.cal.sync_token='already-consumed';self.cal.save()
        self.client.force_login(self.user)
        self.client.post(f'/planner/review/{op.pk}/',{'action':'dismiss'})
        item.refresh_from_db();self.cal.refresh_from_db()
        self.assertEqual(item.etag,'')
        self.assertEqual(self.cal.sync_token,'')
        import_record(self.cal,{'id':'remote','etag':'same','summary':'Google original','start':{'date':'2027-01-05'},'end':{'date':'2027-01-06'}})
        item.refresh_from_db()
        self.assertEqual(item.title,'Google original')

    @override_settings(SECRET_KEY='test-only-planner-signing-key-'*3, PLANNER_GOOGLE_CLIENT_ID='test-client', PLANNER_GOOGLE_CLIENT_SECRET='test-only-client-secret', PLANNER_GOOGLE_REDIRECT_URI='http://localhost:8123/planner/google/callback/')
    def test_oauth_pkce_state_and_callback_host_are_bound_to_session(self):
        import base64,hashlib
        from urllib.parse import urlsplit,parse_qs
        self.client.force_login(self.user)
        response = self.client.post('/planner/google/connect/',HTTP_HOST='localhost')
        self.assertEqual(response.status_code,302)
        query = parse_qs(urlsplit(response['Location']).query)
        flow = self.client.session['planner_oauth']
        self.assertEqual(query['state'],[flow['state']])
        self.assertEqual(query['code_challenge'],[base64.urlsafe_b64encode(hashlib.sha256(flow['verifier'].encode()).digest()).decode().rstrip('=')])
        with patch('planner.google.requests.post') as network:
            self.client.get('/planner/google/callback/?state=wrong&code=unused',HTTP_HOST='localhost')
            network.assert_not_called()
        response = self.client.post('/planner/google/connect/',HTTP_HOST='127.0.0.1')
        self.assertEqual(response.status_code,302)
        self.assertEqual(response['Location'],'/planner/')

    def test_pdf_generation_includes_resolved_links_and_cached_artifact(self):
        from reportlab.pdfgen.canvas import Canvas
        links, labels = [], []
        original_link, original_text = Canvas.linkRect, Canvas.drawString
        def capture_link(pdf, contents, destination, rect, **kwargs):
            links.append((pdf.getPageNumber(), destination, rect))
            return original_link(pdf, contents, destination, rect, **kwargs)
        def capture_text(pdf, x, y, text, *args, **kwargs):
            labels.append((pdf.getPageNumber(), text))
            return original_text(pdf, x, y, text, *args, **kwargs)
        with tempfile.TemporaryDirectory() as temp, override_settings(DATA_DIR=Path(temp)), patch('planner.artifacts.moon_phases', return_value={'2027-01-07': 'New Moon'}), patch.object(Canvas, 'linkRect', capture_link), patch.object(Canvas, 'drawString', capture_text):
            path = generate_pdf(self.book)
            self.assertTrue(path.read_bytes().startswith(b'%PDF-'))
            self.assertEqual(generate_pdf(self.book), path)
            self.assertGreater(path.stat().st_size, 10000)
        pages = {p['id']: p['index']+1 for p in self.book.manifest['pages']}
        destinations = {(page, dest) for page, dest, rect in links}
        self.assertTrue(all(dest in pages for page, dest, rect in links))
        # ISO week 53 crosses into January; Friday must open the second weekly page.
        self.assertIn((pages['week-2026-12-28-b'], 'Week 53'), labels)
        self.assertIn((pages['month-2027-01'], 'week-2026-12-28-b'), destinations)
        self.assertIn((pages['week-2026-12-28-b'], 'month-2027-01'), destinations)
        self.assertIn((pages['week-2026-12-28-a'], 'month-2027-01'), destinations)
        self.assertIn((pages['week-2027-01-04-a'], 'week-2027-01-04-b'), destinations)
        self.assertIn((pages['week-2027-01-04-b'], 'week-2027-01-04-a'), destinations)
        self.assertIn((pages['month-2027-01'], 'month-2027-02'), destinations)
        self.assertIn((pages['month-2027-02'], 'month-2027-01'), destinations)
