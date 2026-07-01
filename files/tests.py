import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from .models import ArchiveRecord, FileNode, SyncState, ZoteroItem, ZoteroSyncState
from .services import crawl_supernote_directory, perform_supernote_sync
from .views import toggle_archive_status, trigger_sync
from .zotero_service import sync_zotero_library, add_item_to_device, remove_item_from_device, return_note_to_zotero


class SupernoteSyncTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.source_tmp = tempfile.TemporaryDirectory()
        self.archive_tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.source_tmp.name)
        self.archive_dir = Path(self.archive_tmp.name)
        (self.source / 'Note').mkdir(parents=True, exist_ok=True)
        (self.archive_dir / 'Note').mkdir(parents=True, exist_ok=True)
        (self.source / 'Note' / 'sample.note').write_text('hello world')

    def tearDown(self):
        self.source_tmp.cleanup()
        self.archive_tmp.cleanup()

    def _write_fake_pdf(self, input_path, output_path, output_type='pdf'):
        Path(output_path).write_bytes(b'%PDF-1.4 fake archive copy')
        return True

    def test_crawl_supernote_directory_indexes_live_files(self):
        with override_settings(SUPERNOTE_SOURCE=self.source):
            crawl_supernote_directory()

        node = FileNode.objects.get(path='Note/sample.note')
        self.assertEqual(node.name, 'sample.note')
        self.assertFalse(node.is_directory)
        self.assertTrue(node.hash)

    def test_perform_supernote_sync_pulls_and_rescans(self):
        fake_result = MagicMock(returncode=0, stdout='ok', stderr='')
        with override_settings(SUPERNOTE_SOURCE=self.source):
            with patch('files.services.subprocess.run', return_value=fake_result) as run_mock:
                with patch('files.services.crawl_supernote_directory') as crawl_mock:
                    result = perform_supernote_sync(direction='pull', rescan=True)

        run_mock.assert_called_once()
        crawl_mock.assert_called_once()
        self.assertTrue(result['success'])
        self.assertEqual(SyncState.objects.get(key='supernote').status, 'success')
        self.assertEqual(
            run_mock.call_args.args[0],
            ['rclone', 'sync', 'SuperNote:Supernote', str(self.source)],
        )

    def test_trigger_sync_returns_status_partial(self):
        with patch('files.views.perform_supernote_sync', return_value={'success': True, 'state': SyncState(key='supernote', status='success')}):
            request = self.factory.post('/sync/', {'direction': 'pull'})
            response = trigger_sync(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'sync-status', response.content)

    def test_crawl_preserves_archived_nodes(self):
        archived = FileNode.objects.create(
            path='Note/archived.note',
            name='archived.note',
            extension='note',
            size=1,
            last_modified=timezone.now(),
            hash='abc',
            is_directory=False,
            is_archived=True,
        )

        with override_settings(SUPERNOTE_SOURCE=self.source):
            crawl_supernote_directory()

        self.assertTrue(FileNode.objects.filter(pk=archived.pk).exists())

    def test_toggle_archive_creates_readable_copy_and_moves_source(self):
        parent = FileNode.objects.create(
            path='Note',
            name='Note',
            extension='',
            size=0,
            last_modified=timezone.now(),
            hash='parent',
            is_directory=True,
            is_archived=False,
        )
        node = FileNode.objects.create(
            path='Note/sample.note',
            name='sample.note',
            extension='note',
            size=11,
            last_modified=timezone.now(),
            hash='abc123',
            is_directory=False,
            is_archived=False,
            parent=parent,
        )

        with override_settings(SUPERNOTE_SOURCE=self.source, ARCHIVE_DIR=self.archive_dir):
            with patch('files.utils.SuperNoteUtility.convert_note', side_effect=self._write_fake_pdf):
                request = self.factory.post(f'/toggle-archive/{node.pk}/', {'is_archived': 'true'})
                response = toggle_archive_status(request, node.pk)

        payload = json.loads(response.content.decode('utf-8'))
        node.refresh_from_db()
        self.assertTrue(node.is_archived)
        self.assertFalse((self.source / 'Note' / 'sample.note').exists())
        self.assertTrue((self.archive_dir / 'Note' / 'sample.note').exists())
        self.assertTrue((self.archive_dir / 'Note' / 'sample.pdf').exists())
        self.assertEqual(payload['success'], True)
        self.assertTrue(ArchiveRecord.objects.filter(file_node=node, archive_path='Note/sample.note').exists())


    def test_toggle_archive_htmx_returns_updated_row(self):
        parent = FileNode.objects.create(
            path='Note',
            name='Note',
            extension='',
            size=0,
            last_modified=timezone.now(),
            hash='parent',
            is_directory=True,
            is_archived=False,
        )
        node = FileNode.objects.create(
            path='Note/sample.note',
            name='sample.note',
            extension='note',
            size=11,
            last_modified=timezone.now(),
            hash='abc123',
            is_directory=False,
            is_archived=False,
            parent=parent,
        )

        with override_settings(SUPERNOTE_SOURCE=self.source, ARCHIVE_DIR=self.archive_dir):
            with patch('files.utils.SuperNoteUtility.convert_note', side_effect=self._write_fake_pdf):
                request = self.factory.post(f'/toggle-archive/{node.pk}/', {'is_archived': 'true'}, HTTP_HX_REQUEST='true')
                response = toggle_archive_status(request, node.pk)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'checked', response.content)
        self.assertIn(b'Archived', response.content)

    def test_toggle_restore_moves_file_back_to_source(self):
        parent = FileNode.objects.create(
            path='Note',
            name='Note',
            extension='',
            size=0,
            last_modified=timezone.now(),
            hash='parent',
            is_directory=True,
            is_archived=False,
        )
        node = FileNode.objects.create(
            path='Note/sample.note',
            name='sample.note',
            extension='note',
            size=11,
            last_modified=timezone.now(),
            hash='abc123',
            is_directory=False,
            is_archived=True,
            parent=parent,
        )
        (self.archive_dir / 'Note').mkdir(parents=True, exist_ok=True)
        (self.archive_dir / 'Note' / 'sample.note').write_text('archived copy')
        (self.archive_dir / 'Note' / 'sample.pdf').write_bytes(b'%PDF-1.4 fake archive copy')

        with override_settings(SUPERNOTE_SOURCE=self.source, ARCHIVE_DIR=self.archive_dir):
            request = self.factory.post(f'/toggle-archive/{node.pk}/', {'is_archived': 'false'})
            response = toggle_archive_status(request, node.pk)

        payload = json.loads(response.content.decode('utf-8'))
        node.refresh_from_db()
        self.assertFalse(node.is_archived)
        self.assertTrue((self.source / 'Note' / 'sample.note').exists())
        self.assertFalse((self.archive_dir / 'Note' / 'sample.note').exists())
        self.assertTrue((self.archive_dir / 'Note' / 'sample.pdf').exists())
        self.assertEqual(payload['success'], True)


class ZoteroIntegrationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.source_tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.source_tmp.name)
        (self.source / 'Document' / 'ZoteroSync').mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.source_tmp.cleanup()

    def _request_stub(self, path, method='GET', params=None, data=None, headers=None, raw=False):
        if path.endswith('/items/top'):
            return [{
                'data': {
                    'key': 'AAA111',
                    'itemType': 'journalArticle',
                    'title': 'Quantum Notes',
                    'creators': [{'firstName': 'Ada', 'lastName': 'Lovelace'}],
                    'abstractNote': 'A useful abstract.',
                    'date': '2026',
                    'url': 'https://example.com/article',
                }
            }]
        if path.endswith('/children'):
            return [{
                'data': {
                    'itemType': 'attachment',
                    'key': 'ATT222',
                    'title': 'Quantum Notes.pdf',
                    'filename': 'Quantum Notes.pdf',
                    'contentType': 'application/pdf',
                }
            }]
        if path.endswith('/file') and raw:
            return b'%PDF-1.4 mock zotero attachment'
        if path.endswith('/items') and method == 'POST':
            self.last_zotero_payload = data
            return {'success': True}
        raise AssertionError(f'Unexpected Zotero request: {path}')

    def test_sync_zotero_library_caches_items_and_attachment(self):
        with override_settings(
            ZOTERO_API_BASE='https://api.zotero.org',
            ZOTERO_API_KEY='secret',
            ZOTERO_USER_ID='12345',
            ZOTERO_LIBRARY_TYPE='user',
        ):
            with patch('files.zotero_service._request', side_effect=self._request_stub):
                result = sync_zotero_library()

        item = ZoteroItem.objects.get(zotero_key='AAA111')
        self.assertEqual(result['count'], 1)
        self.assertEqual(item.title, 'Quantum Notes')
        self.assertEqual(item.attachment_key, 'ATT222')
        self.assertEqual(ZoteroSyncState.objects.get(key='zotero').status, 'success')

    def test_add_item_to_device_downloads_attachment(self):
        item = ZoteroItem.objects.create(
            zotero_key='AAA111',
            item_type='journalArticle',
            title='Quantum Notes',
            attachment_key='ATT222',
            attachment_filename='Quantum Notes.pdf',
        )

        with override_settings(
            SUPERNOTE_SOURCE=self.source,
            ZOTERO_DEVICE_DIR=self.source / 'Document' / 'ZoteroSync',
            ZOTERO_API_BASE='https://api.zotero.org',
            ZOTERO_API_KEY='secret',
            ZOTERO_USER_ID='12345',
            ZOTERO_LIBRARY_TYPE='user',
        ):
            with patch('files.zotero_service._request', side_effect=self._request_stub):
                result = add_item_to_device(item)

        item.refresh_from_db()
        self.assertTrue(item.is_on_device)
        self.assertTrue((self.source / 'Document' / 'ZoteroSync' / 'quantum-notes.pdf').exists())
        self.assertIn('quantum-notes.pdf', result['device_path'])

    def test_remove_item_from_device_clears_local_copy(self):
        item = ZoteroItem.objects.create(
            zotero_key='AAA111',
            item_type='journalArticle',
            title='Quantum Notes',
            attachment_key='ATT222',
            attachment_filename='Quantum Notes.pdf',
            device_path='Document/ZoteroSync/quantum-notes.pdf',
            is_on_device=True,
        )
        (self.source / 'Document' / 'ZoteroSync' / 'quantum-notes.pdf').write_bytes(b'data')

        with override_settings(SUPERNOTE_SOURCE=self.source):
            remove_item_from_device(item)

        item.refresh_from_db()
        self.assertFalse(item.is_on_device)
        self.assertFalse((self.source / 'Document' / 'ZoteroSync' / 'quantum-notes.pdf').exists())

    def test_return_note_to_zotero_posts_parent_note(self):
        item = ZoteroItem.objects.create(
            zotero_key='AAA111',
            item_type='journalArticle',
            title='Quantum Notes',
            attachment_key='ATT222',
        )

        with override_settings(
            ZOTERO_API_BASE='https://api.zotero.org',
            ZOTERO_API_KEY='secret',
            ZOTERO_USER_ID='12345',
            ZOTERO_LIBRARY_TYPE='user',
        ):
            with patch('files.zotero_service._request', side_effect=self._request_stub):
                return_note_to_zotero(item, 'Return this note')

        item.refresh_from_db()
        self.assertEqual(item.note_text, 'Return this note')
        self.assertEqual(self.last_zotero_payload[0]['parentItem'], 'AAA111')
