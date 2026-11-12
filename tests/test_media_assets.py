import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import editing, media_assets


class MediaAssetTests(unittest.TestCase):
    def test_catalog_and_resolver_use_safe_relative_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock = root / "stock"
            stickers = root / "stickers"
            bgm = root / "bgm"
            (stock / "城市").mkdir(parents=True)
            stickers.mkdir()
            bgm.mkdir()
            (stock / "城市" / "夜景.jpg").write_bytes(b"image")
            with patch.dict(
                media_assets.ASSET_DIRS,
                {"stock": stock, "stickers": stickers, "bgm": bgm},
                clear=True,
            ):
                catalog = media_assets.list_media_assets()
                item = catalog["stock"][0]
                self.assertEqual(item["id"], "城市/夜景.jpg")
                self.assertEqual(item["type"], "image")
                self.assertIn("%E5%9F%8E%E5%B8%82", item["url"])
                self.assertEqual(
                    media_assets.resolve_media_asset("stock", "城市/夜景.jpg"),
                    (stock / "城市" / "夜景.jpg").resolve(),
                )
                self.assertIsNone(media_assets.resolve_media_asset("stock", "../secret.jpg"))
                self.assertIsNone(media_assets.resolve_media_asset("stock", root / "secret.jpg"))

    def test_sticker_specs_keep_safe_render_hints(self):
        with tempfile.TemporaryDirectory() as directory:
            stickers = Path(directory)
            sticker = stickers / "happy.png"
            sticker.write_bytes(b"image")
            with patch.dict(media_assets.ASSET_DIRS, {"stickers": stickers}, clear=False):
                specs = media_assets.resolve_sticker_specs(
                    [{"id": "happy.png", "start": 1.2, "duration": 2, "position": "top-right"}]
                )
            self.assertEqual(specs[0]["path"], sticker.resolve())
            self.assertEqual(specs[0]["start"], 1.2)
            self.assertEqual(specs[0]["position"], "top-right")

    def test_stock_segment_maps_narration_and_uses_vertical_canvas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock = root / "stock.jpg"
            audio = root / "speech.wav"
            output = root / "shot.mp4"
            stock.write_bytes(b"image")
            audio.write_bytes(b"audio")
            with (
                patch("pipeline.editing.config.FFMPEG", "ffmpeg"),
                patch("pipeline.editing.probe_duration", return_value=2.0),
                patch("pipeline.editing._run_video_encode") as encoded,
            ):
                editing.build_stock_segment(
                    stock, audio, output, script="测试文案", captions=False, acceleration="cpu"
                )
            command = encoded.call_args.args[0]
            self.assertIn("-loop", command)
            self.assertIn("1:a:0", command)
            self.assertIn("crop=1080:1920", " ".join(command))

    def test_sticker_overlay_keeps_base_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "base.mp4"
            sticker = root / "happy.png"
            output = root / "result.mp4"
            video.write_bytes(b"video")
            sticker.write_bytes(b"image")
            with (
                patch("pipeline.editing.config.FFMPEG", "ffmpeg"),
                patch("pipeline.editing.probe_duration", return_value=5.0),
                patch("pipeline.editing._run_video_encode") as encoded,
            ):
                editing.overlay_stickers(
                    video,
                    [{"path": sticker, "start": 1, "duration": 2, "position": "top-right"}],
                    output,
                    acceleration="cpu",
                )
            command = encoded.call_args.args[0]
            self.assertIn("0:a?", command)
            self.assertIn("between(t\\,1.000\\,3.000)", " ".join(command))


if __name__ == "__main__":
    unittest.main()
