import tempfile
import unittest
from pathlib import Path

from pipeline.text import ass_timestamp, escape_ass, split_caption_chunks, subtitle_timeline, write_animated_ass


class TextTests(unittest.TestCase):
    def test_split_preserves_copy(self):
        source = "你好，欢迎使用完全本地运行的数字人口播工作流。"
        chunks = split_caption_chunks(source, max_chars=6)
        self.assertEqual("".join(chunks), source)
        self.assertTrue(all(len(chunk.strip("，。")) <= 6 for chunk in chunks))

    def test_timeline_covers_full_duration(self):
        timeline = subtitle_timeline("第一句。第二句更长一些。", 8.0)
        self.assertEqual(timeline[0][0], 0.0)
        self.assertEqual(timeline[-1][1], 8.0)

    def test_ass_helpers_and_animation(self):
        self.assertEqual(ass_timestamp(65.34), "0:01:05.34")
        self.assertEqual(escape_ass(r"a{b}\c"), r"a\{b\}\\c")
        with tempfile.TemporaryDirectory() as directory:
            path = write_animated_ass("测试字幕。", 2.0, Path(directory) / "captions.ass")
            content = path.read_text(encoding="utf-8-sig")
            self.assertIn(r"\t(0,140,\fscx114\fscy114)", content)
            self.assertIn("测试字幕。", content)


if __name__ == "__main__":
    unittest.main()

