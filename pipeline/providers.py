"""Optional cloud engines + HyperFrames post. Local Piper/SadTalker/FFmpeg stay default."""

from __future__ import annotations

import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import config


TTS_ENGINES = ("kokoro", "piper", "fish_local", "fish")
AVATAR_ENGINES = ("sadtalker", "heygen")
POST_ENGINES = ("ffmpeg", "hyperframes")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def fish_api_key() -> str:
    return _env("FISH_API_KEY")


def fish_reference_id() -> str:
    return _env("FISH_REFERENCE_ID")


def fish_model() -> str:
    return _env("FISH_MODEL", "s2-pro")


def fish_local_url() -> str:
    return _env("FISH_LOCAL_URL", "http://127.0.0.1:8080/v1/tts")


def fish_local_api_key() -> str:
    return _env("FISH_LOCAL_API_KEY")


def fish_local_ready(timeout: float = 0.45) -> bool:
    """Return whether a configured Fish Speech API server is responding."""
    try:
        parsed = urlsplit(fish_local_url())
        health_path = parsed.path.rsplit("/", 1)[0] + "/health"
        health_url = urlunsplit((parsed.scheme, parsed.netloc, health_path, "", ""))
        request = urllib.request.Request(health_url, method="GET")
        key = fish_local_api_key()
        if key:
            request.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (ValueError, OSError, urllib.error.URLError):
        return False


def heygen_api_key() -> str:
    return _env("HEYGEN_API_KEY")


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader so API keys can live next to the project."""
    env_path = path or (config.ROOT / ".env")
    if not env_path.is_file():
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_tts_engine(requested: object = None) -> str:
    value = str(requested or _env("DOUYIN_TTS_ENGINE", "piper")).strip().lower()
    if value == 'kokoro':
        from .kokoro_tts import ready
        if not ready():
            raise RuntimeError('Kokoro 模型未安装完整，请检查 models/kokoro')
        return 'kokoro'
    if value == "fish_local" and fish_local_ready():
        return "fish_local"
    if value == "fish" and fish_api_key():
        return "fish"
    return "piper"


def resolve_avatar_engine(requested: object = None) -> str:
    value = str(requested or _env("DOUYIN_AVATAR_ENGINE", "sadtalker")).strip().lower()
    if value == "heygen" and heygen_api_key():
        return "heygen"
    return "sadtalker"


def hyperframes_bin() -> list[str] | None:
    """Return argv prefix that CreateProcess can launch on Windows/Unix."""
    # On Windows, bare `npx` often resolves to an extensionless shim; use npx.cmd.
    for name in ("npx.cmd", "npx.exe", "npx"):
        found = shutil.which(name)
        if found:
            return [found]
    node = shutil.which("node.exe") or shutil.which("node")
    if not node:
        return None
    # Last resort: invoke npm's npx-cli.js next to the node install.
    node_dir = Path(node).resolve().parent
    candidates = [
        node_dir / "node_modules" / "npm" / "bin" / "npx-cli.js",
        node_dir / "npx-cli.js",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return [node, str(candidate)]
    return None


def resolve_post_engine(requested: object = None) -> str:
    value = str(requested or _env("DOUYIN_POST_ENGINE", "ffmpeg")).strip().lower()
    if value == "hyperframes" and hyperframes_bin():
        return "hyperframes"
    return "ffmpeg"


def provider_status() -> dict[str, object]:
    """Surface optional engines for /api/health and the UI."""
    load_dotenv()
    node = shutil.which("node.exe") or shutil.which("node")
    npx = hyperframes_bin()
    local_fish = fish_local_ready()
    from .kokoro_tts import ready as kokoro_ready
    return {
        "tts": {
            "current": resolve_tts_engine(),
            "options": [
                {"id": "kokoro", "label": "Kokoro 中文自然配音（本地）", "ready": kokoro_ready()},
                {"id": "piper", "label": "Piper（本地）", "ready": True},
                {
                    "id": "fish_local",
                    "label": "Fish Speech（本地）",
                    "ready": local_fish,
                    "hint": "本地服务已连接" if local_fish else f"本地服务未启动：{fish_local_url()}",
                },
                {
                    "id": "fish",
                    "label": "Fish Audio（云端）",
                    "ready": bool(fish_api_key()),
                    "hint": "设置 FISH_API_KEY 后可用",
                },
            ],
        },
        "avatar": {
            "current": resolve_avatar_engine(),
            "options": [
                {"id": "sadtalker", "label": "SadTalker（本地）", "ready": True},
                {
                    "id": "heygen",
                    "label": "HeyGen（云端）",
                    "ready": bool(heygen_api_key()),
                    "hint": "设置 HEYGEN_API_KEY 后可用；人像会上传",
                },
            ],
        },
        "post": {
            "current": resolve_post_engine(),
            "options": [
                {"id": "ffmpeg", "label": "FFmpeg（本地）", "ready": True},
                {
                    "id": "hyperframes",
                    "label": "HyperFrames（后期）",
                    "ready": bool(npx),
                    "hint": "需要 Node.js 22+；首次渲染会自动拉取 CLI",
                },
            ],
            "node": bool(node),
            "npx": bool(npx),
        },
        "fish_voice": fish_reference_id() or "",
    }
