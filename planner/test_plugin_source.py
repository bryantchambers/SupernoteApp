import io
import tarfile
from django.contrib.auth import get_user_model
from django.test import TestCase


class PluginSourceTests(TestCase):
    def test_source_download_contains_java_codec_and_its_portable_tests(self):
        self.client.force_login(get_user_model().objects.create_user('source-reviewer'))
        response = self.client.get('/planner/device/source/')
        self.assertEqual(response.status_code, 200)
        with tarfile.open(fileobj=io.BytesIO(b''.join(response.streaming_content)), mode='r:gz') as archive:
            names = archive.getnames()
        self.assertIn('device-planner/native/PlannerStateCodec.java', names)
        self.assertIn('device-planner/native/PlannerStorageModule.kt', names)
        self.assertIn('device-planner/native-tests/PlannerStateCodecTest.java', names)
