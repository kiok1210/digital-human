from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SADTALKER_DIR = Path(os.environ.get("SADTALKER_DIR", ROOT / "vendor" / "SadTalker"))
MODEL_DIR = Path(os.environ.get("MODEL_DIR", ROOT / "models"))
PIPER_DIR = MODEL_DIR / "piper"
PIPER_VOICE = os.environ.get("PIPER_VOICE", "zh_CN-huayan-medium")
OUTPUT_DIR = ROOT / "outputs"
BGM_DIR = Path(os.environ.get("BGM_DIR", ROOT / "assets" / "bgm"))
# 可参与自动混剪的本地素材库。目录可以通过环境变量迁移到更大的磁盘。
STOCK_DIR = Path(os.environ.get("STOCK_DIR", ROOT / "assets" / "stock"))
STICKER_DIR = Path(os.environ.get("STICKER_DIR", ROOT / "assets" / "stickers"))
# 虚拟人像库：页面可直接选用的预置形象。
AVATAR_DIR = Path(os.environ.get("AVATAR_DIR", ROOT / "assets" / "avatars"))
# 已生成成片的索引，用于历史记录。
HISTORY_DIR = OUTPUT_DIR
HISTORY_FILE = OUTPUT_DIR / "history.json"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
STICKER_SUFFIXES = {".png", ".webp", ".gif", ".mp4", ".webm"}


def _find_tool(name: str) -> str | None:
    candidates = [
        ROOT / ".runtime" / "Library" / "bin" / f"{name}.exe",
        ROOT / ".runtime" / "Scripts" / f"{name}.exe",
        ROOT / ".runtime" / "bin" / name,
        ROOT / "vendor" / "bin" / f"{name}.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def _bundled_ffmpeg() -> str | None:
    """imageio-ffmpeg ships ffmpeg without ffprobe; probe_duration can cope."""
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    return path if path and Path(path).exists() else None


def _runtime_python() -> str | None:
    """Resolve the interpreter that owns the SadTalker dependencies."""
    local = ROOT / ".runtime" / "python.exe"
    if local.exists():
        return str(local)
    venv = ROOT / ".runtime" / "venv" / "Scripts" / "python.exe"
    if venv.exists():
        return str(venv)
    marker = ROOT / ".runtime" / "system-python.txt"
    if marker.exists():
        recorded = marker.read_text(encoding="ascii", errors="ignore").strip()
        if recorded and Path(recorded).exists():
            return recorded
    return sys.executable


FFMPEG = _find_tool("ffmpeg") or _bundled_ffmpeg()
FFPROBE = _find_tool("ffprobe")
PYTHON = _runtime_python()

# 内置中文音色的展示信息。键为 Piper 声音名，缺失文件时回退到 huayan。
VOICE_CATALOG: dict[str, dict[str, str]] = {
    "zh_CN-huayan-medium": {"label": "华言 · 女声", "tone": "沉稳自然，适合知识口播"},
    "zh_CN-huayan-x_low": {"label": "华言 · 女声（轻量）", "tone": "语速偏快，体积小"},
}

DEFAULT_VOICE = PIPER_VOICE


def voice_candidates() -> list[str]:
    """Every voice name that could be offered, defaults included."""
    names = [PIPER_VOICE, DEFAULT_VOICE, *VOICE_CATALOG.keys()]
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def voice_files(name: str) -> tuple[Path, Path]:
    return PIPER_DIR / f"{name}.onnx", PIPER_DIR / f"{name}.onnx.json"


def voice_installed(name: str) -> bool:
    model, config_path = voice_files(name)
    return model.exists() and config_path.exists()


def list_voices() -> list[dict[str, object]]:
    """Installed voices enriched with display metadata, plus any stray models."""
    installed: list[str] = []
    if PIPER_DIR.exists():
        installed = sorted(
            path.stem for path in PIPER_DIR.glob("*.onnx") if voice_installed(path.stem)
        )
    if not installed:
        installed = [name for name in voice_candidates() if voice_installed(name)]
    if not installed:
        installed = [DEFAULT_VOICE]

    voices: list[dict[str, object]] = []
    for name in installed:
        meta = VOICE_CATALOG.get(name, {})
        model, _ = voice_files(name)
        voices.append(
            {
                "id": name,
                "label": meta.get("label", name),
                "tone": meta.get("tone", "本机已安装的中文音色"),
                "size_mb": round(model.stat().st_size / 1024 / 1024, 1) if model.exists() else 0,
                "default": name == PIPER_VOICE,
            }
        )
    return voices


def resolve_voice(name: object) -> str:
    """Only accept voices whose model files are actually present."""
    value = str(name or "").strip()
    if value and voice_installed(value):
        return value
    for candidate in voice_candidates():
        if voice_installed(candidate):
            return candidate
    return DEFAULT_VOICE


def list_avatars() -> list[dict[str, object]]:
    """Preset portraits under assets/avatars, usable without uploading a photo."""
    if not AVATAR_DIR.exists():
        return []
    avatars: list[dict[str, object]] = []
    for path in sorted(AVATAR_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        avatars.append(
            {
                "id": path.name,
                "label": path.stem,
                "url": f"/avatars/{path.name}",
                "size_kb": round(path.stat().st_size / 1024, 1),
            }
        )
    return avatars


def resolve_avatar(name: object) -> Path | None:
    """Map an avatar id to a file inside AVATAR_DIR, rejecting traversal."""
    value = Path(str(name or "").strip()).name
    if not value or Path(value).suffix.lower() not in IMAGE_SUFFIXES:
        return None
    candidate = (AVATAR_DIR / value).resolve()
    if AVATAR_DIR.resolve() in candidate.parents and candidate.is_file():
        return candidate
    return None


def runtime_report() -> dict[str, object]:
    required = {
        "SadTalker 程序": SADTALKER_DIR / "inference.py",
        "SadTalker 256 模型": SADTALKER_DIR / "checkpoints" / "SadTalker_V0.0.2_256.safetensors",
        "SadTalker 映射模型 1": SADTALKER_DIR / "checkpoints" / "mapping_00109-model.pth.tar",
        "SadTalker 映射模型 2": SADTALKER_DIR / "checkpoints" / "mapping_00229-model.pth.tar",
    }
    missing = [label for label, path in required.items() if not path.exists()]
    installed_voices = [voice for voice in voice_candidates() if voice_installed(voice)]
    if not installed_voices:
        missing.append("Piper 中文声音")
    if not FFMPEG:
        missing.append("FFmpeg")
    # ffprobe is optional: probe_duration falls back to parsing ffmpeg's banner.
    return {
        "ready": not missing,
        "missing": missing,
        "ffprobe": bool(FFPROBE),
        "python": PYTHON,
        "voices": list_voices(),
        "voice": resolve_voice(None),
        "device_hint": "当前安装脚本会根据显存自动选择 CPU 或 CUDA。",
    }
