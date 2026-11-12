import unittest
from unittest.mock import patch
import app
from pipeline.providers import resolve_tts_engine


class KokoroIntegrationTests(unittest.TestCase):
    def test_kokoro_voice_is_not_resolved_as_a_piper_voice(self):
        self.assertEqual(app._resolve_job_voice({'ttsEngine': 'kokoro', 'voice': 'kokoro:9'}), 'kokoro:9')
        self.assertEqual(app._resolve_job_voice({'ttsEngine': 'kokoro', 'voice': 'zh_CN-huayan-medium'}), 'kokoro:3')

    def test_missing_kokoro_is_reported_instead_of_silently_changing_voice(self):
        with patch('pipeline.kokoro_tts.ready', return_value=False):
            with self.assertRaises(RuntimeError):
                resolve_tts_engine('kokoro')
