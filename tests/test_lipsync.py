import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.lipsync import (
    gfpgan_ready,
    normalize_mouth_style,
    prepare_lipsync_audio,
    sadtalker_profile,
)
from pipeline.proc import WorkflowError
from pipeline.runner import _animate


class LipSyncTests(unittest.TestCase):
    def test_profiles_expose_useful_motion_without_unsafe_sizes(self):
        with patch.dict("os.environ", {}, clear=True):
            natural = sadtalker_profile("close", "natural")
            clear = sadtalker_profile("close", "clear")
            full = sadtalker_profile("full", "expressive")
        self.assertEqual(normalize_mouth_style("unknown"), "natural")
        self.assertFalse(natural.still)
        self.assertGreater(clear.expression_scale, natural.expression_scale)
        self.assertTrue(clear.still)
        self.assertEqual(full.preprocess, "full")
        self.assertTrue(full.still)
        self.assertEqual(natural.size, 256)

    def test_gfpgan_requires_all_local_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weights = root / "gfpgan" / "weights"
            weights.mkdir(parents=True)
            with patch("pipeline.lipsync.config.SADTALKER_DIR", root):
                self.assertFalse(gfpgan_ready())
                for name in (
                    "GFPGANv1.4.pth",
                    "alignment_WFLW_4HG.pth",
                    "detection_Resnet50_Final.pth",
                    "parsing_parsenet.pth",
                ):
                    (weights / name).write_bytes(b"model")
                self.assertTrue(gfpgan_ready())

    def test_audio_preparation_uses_sadtalker_native_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "speech.wav"
            destination = root / "lip.wav"
            source.write_bytes(b"RIFF" + b"x" * 80)

            def fake_run(command, *_args):
                self.assertIn("16000", command)
                self.assertIn("pcm_s16le", command)
                self.assertIn("acompressor=threshold=-24dB", " ".join(command))
                destination.write_bytes(b"RIFF" + b"y" * 80)

            with (
                patch("pipeline.lipsync.config.FFMPEG", "ffmpeg"),
                patch("pipeline.lipsync.run_command", side_effect=fake_run),
            ):
                result = prepare_lipsync_audio(source, destination)
            self.assertEqual(result, destination.resolve())

    def test_audio_preparation_falls_back_without_breaking_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "speech.wav"
            source.write_bytes(b"RIFF" + b"x" * 80)
            with (
                patch("pipeline.lipsync.config.FFMPEG", "ffmpeg"),
                patch("pipeline.lipsync.run_command", side_effect=WorkflowError("bad filter")),
            ):
                result = prepare_lipsync_audio(source, Path(directory) / "lip.wav")
            self.assertEqual(result, source.resolve())

    def test_animate_passes_style_to_sadtalker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "portrait.png"
            audio = root / "speech.wav"
            raw = root / "raw"
            image.write_bytes(b"image")
            audio.write_bytes(b"audio")
            commands = []

            def fake_run(command, *_args):
                commands.append(command)
                (raw / "result.mp4").write_bytes(b"video")

            with (
                patch("pipeline.runner.resolve_avatar_engine", return_value="sadtalker"),
                patch("pipeline.runner.config.runtime_report", return_value={"ready": True, "missing": []}),
                patch("pipeline.runner.prepare_lipsync_audio", return_value=audio),
                patch("pipeline.runner.gfpgan_ready", return_value=True),
                patch("pipeline.runner.gpu_acceleration.model_gpu_enabled", return_value=False),
                patch("pipeline.runner.run_command", side_effect=fake_run),
            ):
                result = _animate(
                    image,
                    audio,
                    raw,
                    "close",
                    mouth_style="expressive",
                    face_enhance=True,
                )
            command = commands[0]
            self.assertEqual(result, raw / "result.mp4")
            self.assertIn("--expression_scale", command)
            self.assertIn("1.28", command)
            self.assertIn("--pose_style", command)
            self.assertIn("--enhancer", command)
            self.assertIn("--cpu", command)


if __name__ == "__main__":
    unittest.main()
