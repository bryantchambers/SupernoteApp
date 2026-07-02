import io
import json
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase, override_settings
from supernote_project import settings as project_settings
from django.utils import timezone

from .models import ArchiveRecord, FileNode, SyncState, ZoteroItem, ZoteroSyncState
from .services import crawl_supernote_directory, perform_supernote_sync
from .ai_service import AIService
from .views import toggle_archive_status, trigger_sync, process_with_ai, recycle_file_node, restore_file_from_recycle, empty_recycle_bin, zotero_add_to_device, zotero_remove_from_device, zotero_return_note_submit, zotero_recycle_item, zotero_restore_item, zotero_empty_bin, zotero_recycle_bin
from .zotero_service import sync_zotero_library, add_item_to_device, remove_item_from_device, move_item_to_recycle_bin, restore_item_from_recycle_bin, empty_recycle_bin_item, return_note_to_zotero, ZoteroSyncError


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

    def test_recycle_file_moves_source_into_recycle_bin(self):
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
            request = self.factory.post(f'/recycle/{node.pk}/', HTTP_HX_REQUEST='true')
            response = recycle_file_node(request, node.pk)

        self.assertEqual(response.status_code, 200)
        node.refresh_from_db()
        self.assertTrue(node.is_recycled)
        self.assertTrue(node.recycled_at is not None)
        self.assertFalse((self.source / 'Note' / 'sample.note').exists())
        self.assertTrue((self.archive_dir / 'recycle' / 'Note' / 'sample.note').exists())

    def test_restore_file_from_recycle_moves_file_back_to_source(self):
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
            is_recycled=True,
            recycled_at=timezone.now(),
            parent=parent,
        )
        (self.archive_dir / 'recycle' / 'Note').mkdir(parents=True, exist_ok=True)
        (self.archive_dir / 'recycle' / 'Note' / 'sample.note').write_text('recycled copy')

        with override_settings(SUPERNOTE_SOURCE=self.source, ARCHIVE_DIR=self.archive_dir):
            request = self.factory.post(f'/recycle/restore/{node.pk}/', {'q': ''}, HTTP_HX_REQUEST='true')
            response = restore_file_from_recycle(request, node.pk)

        self.assertEqual(response.status_code, 200)
        node.refresh_from_db()
        self.assertFalse(node.is_recycled)
        self.assertTrue((self.source / 'Note' / 'sample.note').exists())
        self.assertFalse((self.archive_dir / 'recycle' / 'Note' / 'sample.note').exists())

    def test_empty_recycle_bin_deletes_recycled_items(self):
        node = FileNode.objects.create(
            path='Note/sample.note',
            name='sample.note',
            extension='note',
            size=11,
            last_modified=timezone.now(),
            hash='abc123',
            is_directory=False,
            is_archived=False,
            is_recycled=True,
            recycled_at=timezone.now(),
        )
        (self.archive_dir / 'recycle' / 'Note').mkdir(parents=True, exist_ok=True)
        (self.archive_dir / 'recycle' / 'Note' / 'sample.note').write_text('recycled copy')

        with override_settings(SUPERNOTE_SOURCE=self.source, ARCHIVE_DIR=self.archive_dir):
            request = self.factory.post('/recycle/empty/', {'q': ''}, HTTP_HX_REQUEST='true')
            response = empty_recycle_bin(request)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(FileNode.objects.filter(pk=node.pk).exists())
        self.assertFalse((self.archive_dir / 'recycle' / 'Note' / 'sample.note').exists())

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


class EnvLoadingAndAIServiceTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_load_env_file_populates_api_key_aliases(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / '.env'
            env_path.write_text('GEMINI_API_KEY=from-file\nGOOGLE_GENAI_MODEL=gemini-test\n', encoding='utf-8')

            original = {key: os.environ.get(key) for key in ['GEMINI_API_KEY', 'GOOGLE_GENAI_API_KEY', 'GOOGLE_API_KEY', 'GOOGLE_GENAI_MODEL']}
            try:
                for key in original:
                    os.environ.pop(key, None)
                project_settings._load_env_file(env_path)
                self.assertEqual(os.environ.get('GEMINI_API_KEY'), 'from-file')
                self.assertEqual(os.environ.get('GOOGLE_GENAI_MODEL'), 'gemini-test')
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_process_with_ai_surfaces_real_exception(self):
        node = FileNode.objects.create(
            path='Note/sample.note',
            name='sample.note',
            extension='note',
            size=5,
            last_modified=timezone.now(),
            hash='abc',
            is_directory=False,
        )

        with override_settings(GOOGLE_GENAI_API_KEY='secret-key'):
            with patch('files.views.AIService.process_note_with_ai', side_effect=ValueError('bad key')):
                request = self.factory.post(f'/process-ai/{node.pk}/')
                response = process_with_ai(request, node.pk)

        self.assertEqual(response.status_code, 500)
        self.assertIn(b'ValueError: bad key', response.content)

    def test_ai_service_uses_configured_key_and_model(self):
        source_tmp = tempfile.TemporaryDirectory()
        archive_tmp = tempfile.TemporaryDirectory()
        source = Path(source_tmp.name)
        archive_dir = Path(archive_tmp.name)
        try:
            (source / 'Note').mkdir(parents=True, exist_ok=True)
            (source / 'Note' / 'sample.note').write_text('hello', encoding='utf-8')
            node = FileNode.objects.create(
                path='Note/sample.note',
                name='sample.note',
                extension='note',
                size=5,
                last_modified=timezone.now(),
                hash='abc',
                is_directory=False,
            )

            fake_client = MagicMock()
            fake_client.models.generate_content.return_value = MagicMock(text='# converted markdown')

            def fake_convert_note_to_images(input_path, temp_img_dir):
                Path(temp_img_dir).mkdir(parents=True, exist_ok=True)
                Path(temp_img_dir, 'page-1.png').write_bytes(b'\x89PNG\r\n\x1a\n')
                return True

            with override_settings(
                SUPERNOTE_SOURCE=source,
                ARCHIVE_DIR=archive_dir,
                GOOGLE_GENAI_API_KEY='secret-key',
                GOOGLE_GENAI_MODEL='gemini-test-model',
            ):
                with patch('files.ai_service.genai.Client', return_value=fake_client):
                    with patch('files.ai_service.SuperNoteUtility.convert_note_to_images', side_effect=fake_convert_note_to_images):
                        result = AIService.process_note_with_ai(node.id)

            self.assertIsNotNone(result)
            self.assertEqual(result.markdown_content, '# converted markdown')
            fake_client.models.generate_content.assert_called_once()
            self.assertEqual(fake_client.models.generate_content.call_args.kwargs['model'], 'gemini-test-model')
        finally:
            source_tmp.cleanup()
            archive_tmp.cleanup()


class ZoteroIntegrationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.source_tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.source_tmp.name)
        (self.source / 'Document' / 'ZoteroSync').mkdir(parents=True, exist_ok=True)
        self.last_top_params = None
        self.last_zotero_payload = None

    def tearDown(self):
        self.source_tmp.cleanup()

    def _request_stub(self, path, method='GET', params=None, data=None, headers=None, raw=False):
        if path.endswith('/items/top'):
            self.last_top_params = params
            return [{
                'data': {
                    'key': 'AAA111',
                    'itemType': 'journalArticle',
                    'title': 'Quantum Notes',
                    'creators': [{'firstName': 'Ada', 'lastName': 'Lovelace'}],
                    'abstractNote': 'A useful abstract.',
                    'date': '2026',
                    'dateAdded': '2026-04-10T14:13:27Z',
                    'dateModified': '2026-04-11T14:13:27Z',
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
                    'linkMode': 'imported_file',
                }
            }]
        if path.endswith('/file') and raw:
            return b'%PDF-1.4 mock zotero attachment'
        if path.endswith('/items') and method == 'POST':
            self.last_zotero_payload = data
            return {'success': True}
        raise AssertionError(f'Unexpected Zotero request: {path}')

    def test_sync_zotero_library_caches_recent_items_using_date_added_order(self):
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
        self.assertEqual(self.last_top_params['sort'], 'dateAdded')
        self.assertEqual(self.last_top_params['direction'], 'desc')
        self.assertEqual(self.last_top_params['limit'], 100)
        self.assertEqual(item.title, 'Quantum Notes')
        self.assertEqual(item.attachment_key, 'ATT222')
        self.assertEqual(item.attachment_link_mode, 'imported_file')
        self.assertIsNotNone(item.date_added)
        self.assertEqual(ZoteroSyncState.objects.get(key='zotero').status, 'success')

    def test_add_item_to_device_downloads_zotero_storage_bundle_from_koofr(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('Recurrent patterns of microdiversity in a temperate coastal marine environment.pdf', b'%PDF-1.4 koofr storage bundle')
        zip_bytes = buffer.getvalue()

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return self._payload

        def urlopen_stub(req, timeout=20):
            url = req.full_url
            if url.endswith('.prop'):
                return FakeResponse(b'<properties version="1"><mtime>1773402863791</mtime><hash>99d93ac487ec5f8857d90bf0457e2459</hash></properties>')
            if url.endswith('.zip'):
                return FakeResponse(zip_bytes)
            raise AssertionError(f'Unexpected Koofr request: {url}')

        item = ZoteroItem.objects.create(
            zotero_key='AAA111',
            item_type='attachment',
            title='Recurrent patterns of microdiversity in a temperate coastal marine environment',
            attachment_key='2DVZDR57',
            attachment_filename='Recurrent patterns of microdiversity in a temperate coastal marine environment.pdf',
            attachment_link_mode='imported_file',
        )

        with override_settings(
            SUPERNOTE_SOURCE=self.source,
            ZOTERO_DEVICE_DIR=self.source / 'Document' / 'ZoteroSync',
            KOOFR_BASE_URL='https://app.koofr.net/dav/Koofr/zotero',
            KOOFR_USER_NAME='user@example.com',
            KOOFR_TOKEN='token',
        ):
            with patch('files.zotero_service.urlrequest.urlopen', side_effect=urlopen_stub) as urlopen_mock:
                result = add_item_to_device(item)

        item.refresh_from_db()
        self.assertTrue(item.is_on_device)
        self.assertEqual(item.last_transfer_status, 'success')
        self.assertEqual(result['source_kind'], 'zotero_storage')
        self.assertTrue((self.source / 'Document' / 'ZoteroSync' / 'recurrent-patterns-of-microdiversity-in-a-temperate-coastal-marine-environment.pdf').exists())
        self.assertIn('Authorization', urlopen_mock.call_args_list[0].args[0].headers)

    def test_add_item_to_device_downloads_koofr_linked_attachment(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'%PDF-1.4 mock koofr attachment'

        item = ZoteroItem.objects.create(
            zotero_key='AAA111',
            item_type='journalArticle',
            title='Quantum Notes',
            attachment_title='Quantum Notes.pdf',
            attachment_url='https://app.koofr.net/dav/Koofr/zotero/recent/Quantum Notes.pdf',
        )

        with override_settings(
            SUPERNOTE_SOURCE=self.source,
            ZOTERO_DEVICE_DIR=self.source / 'Document' / 'ZoteroSync',
            KOOFR_BASE_URL='https://app.koofr.net/dav/Koofr/zotero',
            KOOFR_USER_NAME='user@example.com',
            KOOFR_TOKEN='token',
        ):
            with patch('files.zotero_service.urlrequest.urlopen', return_value=FakeResponse()) as urlopen_mock:
                result = add_item_to_device(item)

        item.refresh_from_db()
        self.assertTrue(item.is_on_device)
        self.assertEqual(result['source_kind'], 'koofr_linked')
        self.assertTrue((self.source / 'Document' / 'ZoteroSync' / 'quantum-notes.pdf').exists())
        self.assertIn('Authorization', urlopen_mock.call_args.args[0].headers)

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
        self.assertEqual(item.last_transfer_status, 'removed')
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

    def test_zotero_add_to_device_pushes_mirror(self):
        item = ZoteroItem.objects.create(
            zotero_key='AAA111',
            item_type='journalArticle',
            title='Quantum Notes',
            attachment_key='ATT222',
            attachment_filename='Quantum Notes.pdf',
            attachment_link_mode='imported_file',
        )

        with override_settings(
            SUPERNOTE_SOURCE=self.source,
            ZOTERO_DEVICE_DIR=self.source / 'Document' / 'ZoteroSync',
            ZOTERO_API_BASE='https://api.zotero.org',
            ZOTERO_API_KEY='secret',
            ZOTERO_USER_ID='12345',
            ZOTERO_LIBRARY_TYPE='user',
        ):
            with patch('files.views.add_item_to_device', return_value={'device_path': 'Document/ZoteroSync/quantum-notes.pdf', 'source_label': 'Zotero stored attachment'}) as add_mock:
                with patch('files.views.perform_supernote_sync', return_value={'success': True}) as sync_mock:
                    request = self.factory.post(f'/zotero/add/{item.pk}/', HTTP_HX_REQUEST='true')
                    response = zotero_add_to_device(request, item.pk)

        self.assertEqual(response.status_code, 200)
        add_mock.assert_called_once()
        sync_mock.assert_called_once_with(direction='push', rescan=True)
        self.assertIn(b'copied to Supernote and sync pushed', response.content)

    def test_zotero_add_to_device_returns_row_on_error(self):
        item = ZoteroItem.objects.create(
            zotero_key='AAA111',
            item_type='journalArticle',
            title='Quantum Notes',
            attachment_path='/home/user/Zotero/storage/AAA111/Quantum Notes.pdf',
        )

        with patch('files.views.add_item_to_device', side_effect=ZoteroSyncError('Linked attachment exists, but the server cannot resolve a remote file URL for it.')):
            request = self.factory.post(f'/zotero/add/{item.pk}/', HTTP_HX_REQUEST='true')
            response = zotero_add_to_device(request, item.pk)

        item.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(item.last_transfer_status, 'unresolved_linked')
        self.assertIn(b'cannot resolve a remote file URL', response.content)

    def test_zotero_remove_from_device_pushes_mirror(self):
        item = ZoteroItem.objects.create(
            zotero_key='AAA111',
            item_type='journalArticle',
            title='Quantum Notes',
            attachment_key='ATT222',
            attachment_filename='Quantum Notes.pdf',
            device_path='Document/ZoteroSync/quantum-notes.pdf',
            is_on_device=True,
        )

        with override_settings(SUPERNOTE_SOURCE=self.source):
            with patch('files.views.remove_item_from_device', return_value={'removed': True}) as remove_mock:
                with patch('files.views.perform_supernote_sync', return_value={'success': True}) as sync_mock:
                    request = self.factory.post(f'/zotero/remove/{item.pk}/', HTTP_HX_REQUEST='true')
                    response = zotero_remove_from_device(request, item.pk)

        self.assertEqual(response.status_code, 200)
        remove_mock.assert_called_once()
        sync_mock.assert_called_once_with(direction='push', rescan=True)
        self.assertIn(b'Removed from the device mirror and sync pushed', response.content)

    def test_zotero_return_note_submit_uses_post_body_pk(self):
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
                request = self.factory.post('/zotero/return/', {'pk': str(item.pk), 'note_text': 'Return this note'}, HTTP_HX_REQUEST='true')
                response = zotero_return_note_submit(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Quantum Notes', response.content)

    def test_recycle_item_moves_item_out_of_active_list(self):
        item = ZoteroItem.objects.create(
            zotero_key='REC111',
            item_type='journalArticle',
            title='Recyclable Note',
            attachment_key='ATTREC',
            attachment_filename='Recyclable Note.pdf',
            attachment_link_mode='imported_file',
        )

        request = self.factory.post(f'/zotero/recycle/{item.pk}/', HTTP_HX_REQUEST='true')
        response = zotero_recycle_item(request, item.pk)

        item.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(item.is_recycled)
        self.assertEqual(item.last_transfer_status, 'recycled')
        self.assertEqual(ZoteroItem.objects.filter(is_recycled=False).count(), 0)
        self.assertEqual(ZoteroItem.objects.filter(is_recycled=True).count(), 1)

    def test_restore_item_brings_item_back_to_active_list(self):
        item = ZoteroItem.objects.create(
            zotero_key='REC111',
            item_type='journalArticle',
            title='Recyclable Note',
            attachment_key='ATTREC',
            attachment_filename='Recyclable Note.pdf',
            attachment_link_mode='imported_file',
            is_recycled=True,
            recycled_at=timezone.now(),
            last_transfer_status='recycled',
        )

        request = self.factory.post(f'/zotero/restore/{item.pk}/', HTTP_HX_REQUEST='true')
        response = zotero_restore_item(request, item.pk)

        item.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(item.is_recycled)
        self.assertEqual(item.last_transfer_status, 'idle')

    def test_empty_bin_deletes_recycled_items(self):
        ZoteroItem.objects.create(
            zotero_key='REC111',
            item_type='journalArticle',
            title='Recyclable Note',
            attachment_key='ATTREC',
            attachment_filename='Recyclable Note.pdf',
            attachment_link_mode='imported_file',
            is_recycled=True,
            recycled_at=timezone.now(),
        )

        request = self.factory.post('/zotero/empty-bin/', HTTP_HX_REQUEST='true')
        response = zotero_empty_bin(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ZoteroItem.objects.filter(is_recycled=True).count(), 0)

    def test_sync_preserves_recycled_items(self):
        ZoteroItem.objects.create(
            zotero_key='REC111',
            item_type='journalArticle',
            title='Recyclable Note',
            attachment_key='ATTREC',
            attachment_filename='Recyclable Note.pdf',
            attachment_link_mode='imported_file',
            is_recycled=True,
            recycled_at=timezone.now(),
        )

        with override_settings(
            ZOTERO_API_BASE='https://api.zotero.org',
            ZOTERO_API_KEY='secret',
            ZOTERO_USER_ID='12345',
            ZOTERO_LIBRARY_TYPE='user',
        ):
            with patch('files.zotero_service._request', side_effect=self._request_stub):
                sync_zotero_library()

        self.assertTrue(ZoteroItem.objects.filter(zotero_key='REC111', is_recycled=True).exists())
