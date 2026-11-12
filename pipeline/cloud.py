"""Optional Fish Audio TTS and HeyGen talking-photo adapters (stdlib HTTP)."""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config
from .proc import WorkflowError, run_command
from .providers import (
    fish_api_key,
    fish_local_api_key,
    fish_local_url,
    fish_model,
    fish_reference_id,
    heygen_api_key,
)


_LEGACY_FISH_TAGS = {
    "[停顿]": "，……",
    "[长停顿]": "。……",
    "[强调]": "",
    "[语气加重]": "！",
    "[轻声]": "，",
    "[开心]": "！",
}


def normalize_local_fish_text(script: str) -> str:
    """Map the studio's simple S2-like controls to punctuation understood by 1.5."""
    text = script
    for tag, replacement in _LEGACY_FISH_TAGS.items():
        text = text.replace(tag, replacement)
    # Unknown square-bracket controls should not be spoken aloud by the legacy model.
    import re

    return re.sub(r"\[[^\[\]\r\n]{1,16}\]", "", text).strip()


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 120,
) -> dict[str, object]:
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise WorkflowError(f"云端请求失败（{exc.code}）：{detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise WorkflowError(f"无法连接云端服务：{exc.reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowError("云端返回了无法解析的 JSON。") from exc


def _download(url: str, destination: Path, timeout: int = 300) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            destination.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        raise WorkflowError(f"下载云端成片失败（{exc.code}）") from exc
    except urllib.error.URLError as exc:
        raise WorkflowError(f"下载云端成片失败：{exc.reason}") from exc
    return destination


def _to_wav(source: Path, destination: Path) -> Path:
    if source.suffix.lower() == ".wav":
        if source != destination:
            destination.write_bytes(source.read_bytes())
        return destination
    if not config.FFMPEG:
        raise WorkflowError("Fish Audio 输出需要 ffmpeg 转成 WAV。")
    run_command(
        [
            config.FFMPEG, "-y", "-i", str(source),
            "-ac", "1", "-ar", "22050", str(destination),
        ],
        destination.parent,
        "转换配音格式",
    )
    return destination


def synthesize_fish(
    script: str,
    output_wav: Path,
    *,
    speed: float = 1.0,
    reference_id: str | None = None,
) -> Path:
    """Call Fish Audio TTS and write a WAV file for the local lip-sync stage."""
    key = fish_api_key()
    if not key:
        raise WorkflowError("未配置 FISH_API_KEY，无法使用 Fish Audio。")

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "text": script,
        "format": "wav",
        "normalize": True,
        "latency": "normal",
    }
    voice = (reference_id or fish_reference_id()).strip()
    if voice:
        payload["reference_id"] = voice
    # Fish speed: 1.0 is default; map our UI range roughly.
    try:
        payload["speed"] = round(min(1.5, max(0.7, float(speed))), 2)
    except (TypeError, ValueError):
        payload["speed"] = 1.0

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "https://api.fish.audio/v1/tts",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "model": fish_model(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            audio = response.read()
            content_type = response.headers.get("Content-Type", "audio/wav")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise WorkflowError(f"Fish Audio 配音失败（{exc.code}）：{detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise WorkflowError(f"无法连接 Fish Audio：{exc.reason}") from exc

    if not audio:
        raise WorkflowError("Fish Audio 返回了空音频。")

    suffix = ".mp3" if "mpeg" in content_type or "mp3" in content_type else ".wav"
    raw = output_wav.with_name(output_wav.stem + f".fish{suffix}")
    raw.write_bytes(audio)
    return _to_wav(raw, output_wav)


def synthesize_fish_local(
    script: str,
    output_wav: Path,
    *,
    speed: float = 1.0,
    reference_id: str | None = None,
) -> Path:
    """Call a self-hosted Fish Speech /v1/tts MessagePack endpoint."""
    try:
        import msgpack
    except ImportError as exc:
        raise WorkflowError("本地 Fish Speech 需要 msgpack，请重新运行 setup.bat。") from exc

    payload: dict[str, object] = {
        "text": normalize_local_fish_text(script),
        "references": [],
        "reference_id": (reference_id or fish_reference_id()).strip() or None,
        "format": "wav",
        "latency": "normal",
        "max_new_tokens": 1024,
        "chunk_length": 200,
        "top_p": 0.8,
        "repetition_penalty": 1.1,
        "temperature": 0.8,
        "streaming": False,
        "use_memory_cache": "on",
        "seed": None,
    }
    body = msgpack.packb(payload, use_bin_type=True)
    url = fish_local_url()
    url += "&format=msgpack" if "?" in url else "?format=msgpack"
    headers = {"Content-Type": "application/msgpack"}
    key = fish_local_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            audio = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise WorkflowError(f"本地 Fish Speech 配音失败（{exc.code}）：{detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise WorkflowError(f"无法连接本地 Fish Speech：{exc.reason}") from exc
    if not audio:
        raise WorkflowError("本地 Fish Speech 返回了空音频。")

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    raw = output_wav.with_name(output_wav.stem + ".fish-local.wav")
    raw.write_bytes(audio)
    try:
        rate = min(1.35, max(0.75, float(speed)))
    except (TypeError, ValueError):
        rate = 1.0
    if abs(rate - 1.0) < 0.01:
        return _to_wav(raw, output_wav)
    if not config.FFMPEG:
        raise WorkflowError("调整 Fish Speech 语速需要 ffmpeg。")
    run_command(
        [
            config.FFMPEG, "-y", "-i", str(raw),
            "-filter:a", f"atempo={rate:.3f}",
            "-ac", "1", "-ar", "22050", str(output_wav),
        ],
        output_wav.parent,
        "调整 Fish Speech 语速",
    )
    return output_wav


def _heygen_upload(path: Path, content_type: str) -> str:
    key = heygen_api_key()
    if not key:
        raise WorkflowError("未配置 HEYGEN_API_KEY，无法使用 HeyGen。")
    data = path.read_bytes()
    request = urllib.request.Request(
        "https://upload.heygen.com/v1/asset",
        data=data,
        method="POST",
        headers={
            "X-Api-Key": key,
            "Content-Type": content_type,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise WorkflowError(f"HeyGen 上传失败（{exc.code}）：{detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise WorkflowError(f"无法连接 HeyGen 上传服务：{exc.reason}") from exc

    if int(payload.get("code") or 0) not in {0, 100}:
        raise WorkflowError(f"HeyGen 上传失败：{payload.get('message') or payload}")
    asset = payload.get("data") or {}
    asset_id = str(asset.get("id") or asset.get("asset_id") or "").strip()
    if not asset_id:
        raise WorkflowError("HeyGen 上传成功但未返回 asset id。")
    return asset_id


def animate_heygen(image: Path, audio: Path, output_mp4: Path) -> Path:
    """Upload photo + speech, create an audio-driven talking photo, download MP4."""
    key = heygen_api_key()
    if not key:
        raise WorkflowError("未配置 HEYGEN_API_KEY，无法使用 HeyGen。")

    image_type = mimetypes.guess_type(str(image))[0] or "image/jpeg"
    if image_type not in {"image/jpeg", "image/png"}:
        # Convert webp/others to jpeg for HeyGen.
        if not config.FFMPEG:
            raise WorkflowError("HeyGen 仅支持 JPG/PNG，请换一张照片或安装 ffmpeg。")
        converted = output_mp4.parent / "heygen-source.jpg"
        run_command(
            [config.FFMPEG, "-y", "-i", str(image), str(converted)],
            output_mp4.parent,
            "转换人像格式",
        )
        image = converted
        image_type = "image/jpeg"

    audio_path = audio
    audio_type = "audio/wav"
    if audio.suffix.lower() != ".wav":
        audio_type = mimetypes.guess_type(str(audio))[0] or "audio/mpeg"

    image_id = _heygen_upload(image, image_type)
    audio_id = _heygen_upload(audio_path, audio_type)

    create_body = json.dumps(
        {
            "type": "image",
            "image": {"type": "asset_id", "asset_id": image_id},
            "audio_asset_id": audio_id,
            "title": "douyin-local-job",
            "resolution": "1080p",
            "aspect_ratio": "9:16",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    created = _http_json(
        "https://api.heygen.com/v3/videos",
        method="POST",
        headers={
            "X-Api-Key": key,
            "Content-Type": "application/json",
        },
        body=create_body,
        timeout=60,
    )
    data = created.get("data") if isinstance(created.get("data"), dict) else created
    video_id = str((data or {}).get("video_id") or (data or {}).get("id") or "").strip()
    if not video_id:
        # Some responses nest under data.video_id
        raise WorkflowError(f"HeyGen 未返回 video_id：{created}")

    deadline = time.time() + 15 * 60
    video_url = ""
    while time.time() < deadline:
        status_payload = _http_json(
            f"https://api.heygen.com/v3/videos/{video_id}",
            headers={"X-Api-Key": key},
            timeout=60,
        )
        status_data = status_payload.get("data") if isinstance(status_payload.get("data"), dict) else status_payload
        status = str((status_data or {}).get("status") or "").lower()
        if status in {"completed", "complete", "success"}:
            video_url = str(
                (status_data or {}).get("video_url")
                or (status_data or {}).get("url")
                or ""
            ).strip()
            break
        if status in {"failed", "error"}:
            raise WorkflowError(f"HeyGen 生成失败：{(status_data or {}).get('error') or status_data}")
        time.sleep(4)

    if not video_url:
        raise WorkflowError("HeyGen 生成超时，请稍后在控制台查看任务状态。")

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    return _download(video_url, output_mp4)
