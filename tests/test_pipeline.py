import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import _as_bool, _backfill_timeline, _decode_data_url, _image_extension
from pipeline import acceleration, editing, history
from pipeline.proc import WorkflowError
from pipeline.hyperframes_post import build_composition
from pipeline.cloud import normalize_local_fish_text, synthesize_fish_local
from pipeline.fish_voices import list_fish_voices, save_fish_voice
from pipeline.providers import resolve_post_engine, resolve_tts_engine
from pipeline.runner import _resolve_bgm, _split_for_shots, build_shot_timeline, list_bgm


class PipelineTests(unittest.TestCase):
    def test_gpu_vendor_and_mode_detection(self):
        self.assertEqual(acceleration._vendor("AMD Radeon RX 580"), "amd")
        self.assertEqual(acceleration._vendor("NVIDIA GeForce RTX 4060"), "nvidia")
        self.assertEqual(acceleration._vendor("Intel Arc A750"), "intel")
        self.assertEqual(acceleration.normalize_mode("unexpected"), "auto")
        self.assertEqual(acceleration.normalize_mode("CPU"), "cpu")

    def test_gpu_video_args_follow_verified_encoder(self):
        report = {"video": {"encoder": "h264_amf"}}
        with patch("pipeline.acceleration.acceleration_report", return_value=report):
            args, hardware = acceleration.video_encode_args("gpu")
        self.assertTrue(hardware)
        self.assertIn("h264_amf", args)

    def test_cpu_mode_never_uses_hardware_encoder(self):
        args, hardware = acceleration.video_encode_args("cpu")
        self.assertFalse(hardware)
        self.assertIn("libx264", args)

    def test_failed_hardware_encode_retries_with_cpu(self):
        output = Path("result.mp4")
        with (
            patch("pipeline.editing.gpu_acceleration.video_encode_args", return_value=(["-c:v", "h264_qsv"], True)),
            patch("pipeline.editing.run_command", side_effect=[WorkflowError("GPU failed"), ""]) as invoked,
        ):
            editing._run_video_encode(["ffmpeg", "-y"], [str(output)], output, "测试编码", "gpu")
        self.assertEqual(invoked.call_count, 2)
        self.assertIn("libx264", invoked.call_args_list[1].args[0])

    def test_split_for_shots(self):
        pieces = _split_for_shots("第一句。第二句。第三句。第四句。", 2)
        self.assertEqual(pieces, ["第一句。第二句。", "第三句。第四句。"])
        self.assertEqual(len(_split_for_shots("一。二。三。四。五。六。七。", 6)), 6)

    def test_timeline_keeps_crossfade_overlap(self):
        timeline = build_shot_timeline(["一", "二"], ["甲", "乙"], [3.0, 4.0], 0.5)
        self.assertEqual(timeline[1]["start"], 2.5)
        scaled = _backfill_timeline(timeline, 13.0)
        self.assertEqual(scaled[-1]["end"], 13.0)
        self.assertLess(scaled[1]["start"], scaled[0]["end"])

    def test_boolean_and_data_url_helpers(self):
        self.assertTrue(_as_bool("true"))
        self.assertFalse(_as_bool("false", True))
        header, payload = _decode_data_url("data:image/png;base64,aGk=")
        self.assertIn("image/png", header)
        self.assertEqual(payload, b"hi")
        self.assertEqual(_image_extension(b"\xff\xd8\xffpayload"), ".jpg")
        self.assertEqual(_image_extension(b"\x89PNG\r\n\x1a\npayload"), ".png")
        with self.assertRaises(ValueError):
            _image_extension(b"not-an-image")

    def test_failed_history_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            with patch("pipeline.config.HISTORY_FILE", path), patch("pipeline.config.OUTPUT_DIR", Path(directory)):
                history.record("failed-job", title="测试", status="failed", duration=0, elapsed=1, voice="v", shots=1)
                self.assertEqual(history.listing()[0]["id"], "failed-job")
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))[0]["status"], "failed")

    def test_resolve_library_bgm_and_reject_traversal(self):
        tracks = list_bgm()
        self.assertIn("soft-ambient.wav", tracks)
        with tempfile.TemporaryDirectory() as directory:
            resolved = _resolve_bgm("soft-ambient.wav", Path(directory))
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.name, "soft-ambient.wav")
            self.assertIsNone(_resolve_bgm("../secrets.wav", Path(directory)))
            self.assertIsNone(_resolve_bgm("", Path(directory)))

    def test_provider_defaults_stay_local_without_keys(self):
        with patch.dict("os.environ", {"FISH_API_KEY": "", "HEYGEN_API_KEY": "", "DOUYIN_TTS_ENGINE": "fish"}, clear=False):
            self.assertEqual(resolve_tts_engine("fish"), "piper")
            self.assertEqual(resolve_post_engine("ffmpeg"), "ffmpeg")

    def test_local_fish_requires_a_live_server(self):
        with patch("pipeline.providers.fish_local_ready", return_value=False):
            self.assertEqual(resolve_tts_engine("fish_local"), "piper")
        with patch("pipeline.providers.fish_local_ready", return_value=True):
            self.assertEqual(resolve_tts_engine("fish_local"), "fish_local")

    def test_local_fish_client_writes_returned_wav(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"RIFF-local-fish"

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "speech.wav"
            with (
                patch("pipeline.cloud.fish_local_url", return_value="http://127.0.0.1:8080/v1/tts"),
                patch("pipeline.cloud.fish_local_api_key", return_value=""),
                patch("pipeline.cloud.urllib.request.urlopen", return_value=Response()) as opened,
            ):
                synthesize_fish_local("你好", output, speed=1.0, reference_id="demo")
            self.assertEqual(output.read_bytes(), b"RIFF-local-fish")
            request = opened.call_args.args[0]
            self.assertIn("format=msgpack", request.full_url)
            self.assertEqual(request.get_header("Content-type"), "application/msgpack")

    def test_fish_tags_and_reference_voice_library(self):
        self.assertEqual(
            normalize_local_fish_text("你好[停顿]重点[语气加重][未知]"),
            "你好，……重点！",
        )
        with tempfile.TemporaryDirectory() as directory:
            fish_dir = Path(directory) / "fish-speech"
            with patch.dict("os.environ", {"FISH_SPEECH_DIR": str(fish_dir)}, clear=False):
                voice = save_fish_voice(
                    "我的声音",
                    "这是一段参考录音。",
                    b"RIFF\x00\x00\x00\x00WAVEpayload",
                    ".wav",
                )
                self.assertTrue(str(voice["id"]).startswith("voice-"))
                voices = list_fish_voices()
                self.assertEqual(voices[0]["label"], "系统默认音色")
                self.assertEqual(voices[1]["transcript"], "这是一段参考录音。")

    def test_hyperframes_composition_writes_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "a.mp4"
            audio = root / "a.wav"
            video.write_bytes(b"fake")
            audio.write_bytes(b"fake")
            with patch("pipeline.hyperframes_post.probe_duration", return_value=2.5):
                comp = build_composition(
                    root / "hf",
                    [{"video": video, "audio": audio, "text": "你好世界。继续测试。", "duration": 2.5}],
                    title="开场标题",
                    captions=True,
                    transition=0.5,
                    bgm=None,
                )
            html = comp.read_text(encoding="utf-8")
            self.assertIn('data-width="1080"', html)
            self.assertIn("开场标题", html)
            self.assertIn("media/shot1.mp4", html)
            self.assertIn("caption", html)
if __name__ == "__main__":
    unittest.main()
