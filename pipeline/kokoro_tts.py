"""CPU Chinese Kokoro 1.1 inference in an isolated worker process."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from . import config

MODEL_DIR = config.MODEL_DIR / 'kokoro' / 'kokoro-int8-multi-lang-v1_1'
VOICES = [
    {'id': 'kokoro:3', 'label': '中文女声 · 001', 'tone': 'Kokoro 中文女声'},
    {'id': 'kokoro:6', 'label': '中文女声 · 004', 'tone': 'Kokoro 中文女声'},
    {'id': 'kokoro:9', 'label': '中文女声 · 007', 'tone': 'Kokoro 中文女声'},
    {'id': 'kokoro:58', 'label': '中文男声 · 009', 'tone': 'Kokoro 中文男声'},
]


def ready() -> bool:
    return bool(importlib.util.find_spec('sherpa_onnx')) and all(
        (MODEL_DIR / name).is_file() for name in
        ('model.int8.onnx', 'voices.bin', 'tokens.txt', 'lexicon-zh.txt', 'dict/jieba.dict.utf8'))


def synthesize(text: str, output: Path, speed: float = 1.0, voice: str | None = None):
    import sherpa_onnx
    import soundfile as sf
    import re
    sid = int(voice.split(':')[1]) if voice in {v['id'] for v in VOICES} else 3
    cfg = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(MODEL_DIR / 'model.int8.onnx'),
                voices=str(MODEL_DIR / 'voices.bin'), tokens=str(MODEL_DIR / 'tokens.txt'),
                lexicon=','.join(str(MODEL_DIR / f) for f in ('lexicon-zh.txt', 'lexicon-us-en.txt')),
                dict_dir=str(MODEL_DIR / 'dict'), lang='zh'),
            num_threads=4, provider='cpu'),
        rule_fsts=','.join(str(MODEL_DIR / f) for f in ('phone-zh.fst', 'date-zh.fst', 'number-zh.fst')),
        max_num_sentences=1)
    if not cfg.validate():
        raise RuntimeError('Kokoro 模型配置无效')
    tts = sherpa_onnx.OfflineTts(cfg)
    generation = sherpa_onnx.GenerationConfig()
    generation.sid = sid
    generation.speed = max(0.7, min(1.4, speed))
    generation.silence_scale = 0.65
    clean = re.sub(r'\[[^\]]*\]', '', text)
    audio = tts.generate(clean, generation)
    if len(audio.samples) == 0:
        raise RuntimeError('Kokoro 没有生成有效音频')
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), audio.samples, audio.sample_rate, subtype='PCM_16')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--text-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--speed', type=float, default=1.0)
    parser.add_argument('--voice', default='kokoro:3')
    args = parser.parse_args()
    synthesize(args.text_file.read_text(encoding='utf-8'), args.output, args.speed, args.voice)
