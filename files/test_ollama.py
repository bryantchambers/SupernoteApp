import json
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from PIL import Image
import requests

from .ai_service import AIService
from .models import FileNode, ProcessedNote
from .ollama_service import build_markdown, local_inference_lock, validate_base_url
from .settings_service import test_service, update_env_from_post
from .views import process_with_ai, process_with_ai_custom


@override_settings(AI_PROVIDER='ollama', OLLAMA_BASE_URL='http://rockpigeon:11434',
                   OLLAMA_MODEL='qwen3-vl:4b-instruct-q4_K_M', OLLAMA_TIMEOUT=180,
                   GOOGLE_GENAI_API_KEY='', AI_PROCESSING_ENABLED=True)
class OllamaTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        runtime = override_settings(ARCHIVE_DIR=self.folder)
        runtime.enable()
        self.addCleanup(runtime.disable)
        self.pages = [str(self.folder / 'page-1.png'), str(self.folder / 'page-2.png')]
        for page in self.pages:
            Image.new('RGB', (10, 10), 'white').save(page)
        self.inventory = {'models': [{'name': 'qwen3-vl:4b-instruct-q4_K_M', 'digest': 'abc123'}]}
        self.details = {'capabilities': ['completion', 'vision']}

    def responses(self, *values):
        return [Mock(status_code=200, json=Mock(return_value=value)) for value in values]

    def completion(self, text='Converted', reason='stop'):
        return {'done': True, 'done_reason': reason, 'message': {'content': text}}

    def node(self):
        return FileNode.objects.create(path='Note/test.note', name='test.note', extension='note',
                                       size=1, last_modified=timezone.now(), hash='abc')

    def test_default_and_custom_actions_need_no_gemini_key(self):
        node = self.node()
        processed = ProcessedNote.objects.create(file_node=node, markdown_content='# Local')
        processed.ai_provider, processed.ai_model = 'ollama', 'local-model'
        factory = RequestFactory()
        with patch('files.views.AIService.process_note_with_ai', return_value=processed) as process:
            for view, data in [(process_with_ai, {}), (process_with_ai_custom, {'prompt': 'Summarize'})]:
                result = view(factory.post('/process-ai/', data), node.pk)
                self.assertEqual(result.status_code, 200)
                payload = json.loads(result.content)
                self.assertEqual(payload['provider'], 'ollama')
                self.assertEqual(payload['model'], 'local-model')
            self.assertEqual(process.call_count, 2)

    def test_local_provider_persists_result_and_never_calls_gemini(self):
        node = self.node()
        with patch.object(AIService, '_render_note_images', return_value=(node, '', self.pages[:1])):
            with patch.object(AIService, '_build_markdown_from_images') as gemini:
                with patch('files.ollama_service.requests.request', side_effect=self.responses(
                        self.inventory, self.details, self.completion('# Local'))) as request:
                    result = AIService.process_note_with_ai(node.pk)
        self.assertEqual(result.markdown_content, '# Local')
        self.assertEqual(result.ai_provider, 'ollama')
        self.assertEqual(ProcessedNote.objects.get(file_node=node).markdown_content, '# Local')
        gemini.assert_not_called()
        payload = request.call_args.kwargs['json']
        self.assertEqual(len(payload['messages'][0]['images']), 1)
        self.assertEqual(payload['options']['num_ctx'], 8192)
        self.assertFalse(payload['stream'])

    def test_custom_multipage_transcribes_before_text_only_transformation(self):
        with patch('files.ollama_service.requests.request', side_effect=self.responses(
                self.inventory, self.details, self.completion('First'), self.completion('Second'),
                self.completion('Summary'))) as request:
            markdown, _, _ = build_markdown(self.pages, 'Summarize', 'Transcribe', custom=True)
        self.assertEqual(markdown, 'Summary')
        chats = [call.kwargs['json'] for call in request.call_args_list[2:]]
        self.assertTrue(chats[0]['messages'][0]['content'].startswith('Transcribe'))
        self.assertIn('images', chats[1]['messages'][0])
        self.assertNotIn('images', chats[2]['messages'][0])
        self.assertIn('First\n\n---\n\nSecond', chats[2]['messages'][0]['content'])

    def test_nonvision_and_remote_models_rejected_before_image_upload(self):
        for details, message in [({'capabilities': ['completion']}, 'cannot read images'),
                                 ({'capabilities': ['vision'], 'remote_host': 'cloud'}, 'cloud')]:
            with self.subTest(details=details):
                with patch('files.ollama_service.requests.request', side_effect=self.responses(self.inventory, details)) as request:
                    with self.assertRaisesRegex(ValueError, message):
                        build_markdown(self.pages[:1], 'Read', 'Read')
                    self.assertEqual(request.call_count, 2)

    def test_missing_model_does_not_download_or_upload(self):
        with patch('files.ollama_service.requests.request', side_effect=self.responses({'models': []})) as request:
            with self.assertRaisesRegex(ValueError, 'not installed'):
                build_markdown(self.pages[:1], 'Read', 'Read')
            self.assertEqual(request.call_count, 1)

    def test_timeout_does_not_replace_existing_result_or_fallback(self):
        node = self.node()
        ProcessedNote.objects.create(file_node=node, markdown_content='Keep existing')
        with patch.object(AIService, '_render_note_images', return_value=(node, '', self.pages[:1])):
            with patch.object(AIService, '_build_markdown_from_images') as gemini:
                with patch('files.ollama_service.requests.request', side_effect=requests.Timeout):
                    with self.assertRaisesRegex(RuntimeError, 'no cloud fallback'):
                        AIService.process_note_with_ai(node.pk)
                gemini.assert_not_called()
        self.assertEqual(ProcessedNote.objects.get(file_node=node).markdown_content, 'Keep existing')

    def test_truncated_empty_or_incomplete_outputs_fail(self):
        for completion in [self.completion('Truncated', 'length'), self.completion(''), {'done': False}]:
            with self.subTest(completion=completion):
                with patch('files.ollama_service.requests.request', side_effect=self.responses(
                        self.inventory, self.details, completion)):
                    with self.assertRaisesRegex(RuntimeError, 'Previous output is preserved'):
                        build_markdown(self.pages[:1], 'Read', 'Read')

    def test_concurrent_local_requests_return_busy_and_release_lock(self):
        with local_inference_lock():
            with self.assertRaisesRegex(RuntimeError, 'already processing'):
                with local_inference_lock():
                    self.fail('Second lock should fail')
        with local_inference_lock():
            pass

    def test_page_limit_prevents_network_call(self):
        with patch('files.ollama_service.requests.request') as request:
            with self.assertRaisesRegex(ValueError, '1–12 pages'):
                build_markdown(self.pages * 7, 'Read', 'Read')
            request.assert_not_called()

    def test_oversize_prompt_does_not_upload_image(self):
        with patch('files.ollama_service.requests.request', side_effect=self.responses(self.inventory, self.details)) as request:
            with self.assertRaisesRegex(ValueError, 'prompt is too long'):
                build_markdown(self.pages[:1], 'x' * 4000, 'Read', custom=True)
            self.assertEqual(request.call_count, 2)

    def test_oversize_combined_transcription_is_not_silently_truncated(self):
        with patch('files.ollama_service.requests.request', side_effect=self.responses(
                self.inventory, self.details, self.completion('a' * 2500), self.completion('b' * 2500))) as request:
            with self.assertRaisesRegex(ValueError, 'too long for the local context'):
                build_markdown(self.pages, 'Summarize', 'Read', custom=True)
            self.assertEqual(request.call_count, 4)

    def test_invalid_and_credentialed_addresses_rejected(self):
        for address in ['file:///tmp', 'http://user:password@host', 'http://host?key=x', '']:
            with self.subTest(address=address), self.assertRaises(ValueError):
                validate_base_url(address)

    def test_connection_check_validates_local_vision_capability(self):
        with patch('files.ollama_service.requests.request', side_effect=self.responses(self.inventory, self.details)):
            result = test_service('ai')
        self.assertTrue(result.ok)
        self.assertIn('local vision model', result.message)

    def test_settings_persist_provider_and_preserve_unrelated_credentials(self):
        env = self.folder / 'app.env'
        env.write_text('GOOGLE_GENAI_API_KEY=keep-secret\nPLANNER_GOOGLE_WRITES_ENABLED=true\n')
        import os
        # Runtime updates also touch os.environ; isolate both for later tests.
        with patch.dict(os.environ, {'SUPERNOTE_ENV_FILE': str(env)}):
            update_env_from_post({'AI_PROVIDER': 'ollama', 'OLLAMA_BASE_URL': 'http://rockpigeon:11434/',
                                  'OLLAMA_MODEL': 'qwen3-vl:4b-instruct-q4_K_M', 'GOOGLE_GENAI_API_KEY': ''})
        text = env.read_text()
        self.assertIn('AI_PROVIDER=ollama', text)
        self.assertIn('GOOGLE_GENAI_API_KEY=keep-secret', text)
        self.assertIn('PLANNER_GOOGLE_WRITES_ENABLED=true', text)
