"""Local Fish Speech reference voice library."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from . import config


REFERENCE_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}


def fish_speech_dir() -> Path:
    value = (os.environ.get("FISH_SPEECH_DIR") or "").strip()
    return Path(value) if value else config.ROOT / "vendor" / "fish-speech"


def reference_dir() -> Path:
    return fish_speech_dir() / "references"


def _safe_voice_id(value: object) -> str:
    voice_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(value or "").strip())
    return voice_id[:64]


def _voice_folder(voice_id: object) -> Path | None:
    safe = _safe_voice_id(voice_id)
    if not safe:
        return None
    root = reference_dir().resolve()
    candidate = (root / safe).resolve()
    return candidate if root in candidate.parents else None


def list_fish_voices() -> list[dict[str, object]]:
    """Return the base model plus every usable audio/.lab reference pair."""
    voices: list[dict[str, object]] = [
        {
            "id": "",
            "label": "系统默认音色",
            "description": "不使用参考录音，由 Fish Speech 直接生成",
            "initial": "默",
            "default": True,
            "audioUrl": "",
        }
    ]
    root = reference_dir()
    if not root.is_dir():
        return voices

    for folder in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda p: p.name):
        audio = next(
            (
                item
                for item in sorted(folder.iterdir())
                if item.is_file()
                and item.suffix.lower() in REFERENCE_AUDIO_SUFFIXES
                and item.with_suffix(".lab").is_file()
            ),
            None,
        )
        if audio is None:
            continue
        metadata: dict[str, object] = {}
        meta_file = folder / "meta.json"
        if meta_file.is_file():
            try:
                loaded = json.loads(meta_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    metadata = loaded
            except (OSError, json.JSONDecodeError):
                pass
        label = str(metadata.get("label") or folder.name).strip() or folder.name
        description = str(metadata.get("description") or "本地参考音色").strip()
        voices.append(
            {
                "id": folder.name,
                "label": label,
                "description": description,
                "initial": label[:1],
                "default": False,
                "audioUrl": f"/fish-voices/{folder.name}/{audio.name}",
                "transcript": audio.with_suffix(".lab").read_text(encoding="utf-8", errors="replace").strip(),
            }
        )
    return voices


def audio_extension(header: str, payload: bytes) -> str:
    mime = header.split(";", 1)[0].lower()
    by_mime = {
        "data:audio/wav": ".wav",
        "data:audio/x-wav": ".wav",
        "data:audio/mpeg": ".mp3",
        "data:audio/mp3": ".mp3",
        "data:audio/flac": ".flac",
        "data:audio/ogg": ".ogg",
        "data:audio/mp4": ".m4a",
        "data:audio/x-m4a": ".m4a",
        "data:audio/aac": ".aac",
    }
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WAVE":
        return ".wav"
    if payload.startswith(b"fLaC"):
        return ".flac"
    if payload.startswith(b"OggS"):
        return ".ogg"
    if payload.startswith(b"ID3") or (len(payload) > 1 and payload[0] == 0xFF and payload[1] & 0xE0 == 0xE0):
        return ".mp3"
    if len(payload) >= 12 and payload[4:8] == b"ftyp":
        return ".m4a"
    return by_mime.get(mime, "")


def save_fish_voice(label: str, transcript: str, audio: bytes, suffix: str) -> dict[str, object]:
    label = label.strip()
    transcript = transcript.strip()
    if not label:
        raise ValueError("请填写音色名称。")
    if not transcript:
        raise ValueError("请填写参考录音对应的完整文字。")
    if suffix not in REFERENCE_AUDIO_SUFFIXES:
        raise ValueError("参考录音只支持 WAV、MP3、FLAC、OGG、M4A 或 AAC。")
    if not audio or len(audio) > 20 * 1024 * 1024:
        raise ValueError("参考录音为空或超过 20MB。")

    voice_id = f"voice-{uuid.uuid4().hex[:10]}"
    folder = reference_dir() / voice_id
    folder.mkdir(parents=True, exist_ok=False)
    reference = folder / f"reference{suffix}"
    reference.write_bytes(audio)
    reference.with_suffix(".lab").write_text(transcript, encoding="utf-8")
    (folder / "meta.json").write_text(
        json.dumps(
            {"label": label, "description": "我的克隆音色"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return next(item for item in list_fish_voices() if item["id"] == voice_id)


def resolve_voice_asset(voice_id: object, filename: object) -> Path | None:
    folder = _voice_folder(voice_id)
    safe_name = Path(str(filename or "")).name
    if folder is None or not safe_name:
        return None
    candidate = (folder / safe_name).resolve()
    return candidate if folder in candidate.parents and candidate.is_file() else None


def remove_fish_voice(voice_id: object) -> bool:
    folder = _voice_folder(voice_id)
    if folder is None or not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True
