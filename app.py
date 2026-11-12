from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from pipeline import config, history, presets
from pipeline.acceleration import acceleration_report, normalize_mode, selection_label
from pipeline.cloud import synthesize_fish_local
from pipeline.fish_voices import (
    audio_extension,
    list_fish_voices,
    remove_fish_voice,
    resolve_voice_asset,
    save_fish_voice,
)
from pipeline.media_assets import list_media_assets, resolve_media_asset
from pipeline.proc import probe_duration
from pipeline.providers import load_dotenv, provider_status, resolve_tts_engine
from pipeline.runner import (
    WorkflowError,
    generate_edited_video,
    generate_video,
    list_bgm,
)


WEB_DIR = config.ROOT / "web"
MAX_BODY_BYTES = 40 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
jobs: dict[str, dict[str, object]] = {}
jobs_lock = threading.Lock()
generation_lock = threading.Lock()


def update_job(job_id: str, **values: object) -> None:
    with jobs_lock:
        jobs[job_id].update(values)


def _read_job(job_id: str) -> dict[str, object] | None:
    with jobs_lock:
        job = jobs.get(job_id)
        return dict(job) if job else None


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _voice_label(voice: str) -> str:
    for item in config.list_voices():
        if item["id"] == voice:
            return str(item["label"])
    return voice


def _job_summary(payload: dict[str, object], segments: int, voice: str) -> str:
    shots = max(1, min(6, segments))
    parts = [f"{shots} 个镜头"]
    if _as_bool(payload.get("captions"), True):
        parts.append("缩放大字")
    if _as_bool(payload.get("introCard")):
        parts.append("封面卡")
    if str(payload.get("bgm") or "").strip():
        parts.append("背景音乐")
    if payload.get("stockAssets"):
        parts.append("素材混剪")
    if payload.get("stickers"):
        parts.append("动态贴纸")
    if str(payload.get("avatar") or "").strip():
        parts.append("虚拟人像")
    tts = str(payload.get("ttsEngine") or "piper")
    avatar = str(payload.get("avatarEngine") or "sadtalker")
    post = str(payload.get("postEngine") or "ffmpeg")
    parts.append(f"配音：{tts}")
    parts.append(f"口型：{avatar}")
    parts.append(f"后期：{post}")
    parts.append(f"加速：{selection_label(payload.get('acceleration'))}")
    parts.append(f"音色：{_voice_label(voice)}")
    parts.append(f"取景：{'头像特写' if payload.get('portraitMode') == 'close' else '整张照片'}")
    mouth_labels = {"natural": "自然", "clear": "清晰", "expressive": "活泼"}
    parts.append(f"口型：{mouth_labels.get(str(payload.get('mouthStyle') or 'natural'), '自然')}")
    return "本片包含：" + " · ".join(parts)


def _resolve_job_voice(payload: dict[str, object]) -> str:
    """Piper voices are local ids; Fish uses optional reference_id from the form."""
    raw = str(payload.get("voice") or "").strip()
    if str(payload.get('ttsEngine')) == 'kokoro':
        from pipeline.kokoro_tts import VOICES
        return raw if raw in {v['id'] for v in VOICES} else 'kokoro:3'
    if resolve_tts_engine(payload.get("ttsEngine")) in {"fish", "fish_local"}:
        return raw
    return config.resolve_voice(raw)


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    return f"{minutes} 分 {secs} 秒" if minutes else f"{secs} 秒"


def _decode_data_url(data_url: str) -> tuple[str, bytes]:
    header, _, encoded = data_url.partition(",")
    if not encoded:
        raise ValueError("上传的数据不完整，请重新选择文件。")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("上传的数据无法解析，请重新选择文件。") from exc
    return header, payload


def _image_extension(payload: bytes) -> str:
    """Detect supported image types from bytes instead of trusting the browser."""
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("照片内容不是有效的 JPG、PNG 或 WebP 文件。")


def run_job(job_id: str, image_path: Path, payload: dict[str, object]) -> None:
    job_dir = config.OUTPUT_DIR / job_id
    segments = _as_int(payload.get("segments"), 1)
    voice = _resolve_job_voice(payload)
    tts_engine = str(payload.get("ttsEngine") or "piper")
    avatar_engine = str(payload.get("avatarEngine") or "sadtalker")
    post_engine = str(payload.get("postEngine") or "ffmpeg")
    acceleration = normalize_mode(payload.get("acceleration"))
    title = str(payload.get("title", "")).strip() or str(payload.get("text", "")).strip()[:16]
    started_at = float(payload.get("_started_at") or time.time())  # type: ignore[arg-type]

    def progress(percent: int, message: str) -> None:
        update_job(job_id, progress=percent, message=message, updated_at=time.time())

    acquired = generation_lock.acquire(blocking=False)
    if not acquired:
        update_job(job_id, status="queued", progress=2, message="正在等待前一个生成任务完成")
        generation_lock.acquire()
    try:
        update_job(job_id, status="running", message="开始处理", updated_at=time.time())
        update_job(job_id, acceleration=selection_label(acceleration))
        if segments > 1:
            result, timeline = generate_edited_video(
                image=image_path,
                script=str(payload["text"]),
                job_dir=job_dir,
                portrait_mode=str(payload.get("portraitMode", "close")),
                captions=_as_bool(payload.get("captions"), True),
                speed=float(payload.get("speed", 1.0)),
                segments=segments,
                title=str(payload.get("title", "")) if _as_bool(payload.get("introCard")) else "",
                transition_style=str(payload.get("transitionStyle", "fade")),
                bgm=payload.get("bgm"),
                bgm_volume=float(payload.get("bgmVolume", 0.16)),
                voice=voice,
                tts_engine=tts_engine,
                avatar_engine=avatar_engine,
                post_engine=post_engine,
                acceleration=acceleration,
                stock_assets=payload.get("stockAssets"),
                stickers=payload.get("stickers"),
                mouth_style=str(payload.get("mouthStyle") or "natural"),
                face_enhance=_as_bool(payload.get("faceEnhance")),
                progress=progress,
            )
        else:
            result = generate_video(
                image=image_path,
                text=str(payload["text"]),
                job_dir=job_dir,
                portrait_mode=str(payload.get("portraitMode", "close")),
                captions=_as_bool(payload.get("captions"), True),
                speed=float(payload.get("speed", 1.0)),
                voice=voice,
                bgm=payload.get("bgm"),
                bgm_volume=float(payload.get("bgmVolume", 0.16)),
                tts_engine=tts_engine,
                avatar_engine=avatar_engine,
                post_engine=post_engine,
                acceleration=acceleration,
                stock_assets=payload.get("stockAssets"),
                stickers=payload.get("stickers"),
                mouth_style=str(payload.get("mouthStyle") or "natural"),
                face_enhance=_as_bool(payload.get("faceEnhance")),
                progress=progress,
            )
            duration = probe_duration(result)
            timeline = [
                {
                    "index": 1,
                    "name": "镜头 1",
                    "text": str(payload.get("text", ""))[:40],
                    "duration": round(duration, 2),
                    "start": 0.0,
                    "end": round(duration, 2),
                }
            ]

        elapsed = time.time() - started_at
        total_duration = probe_duration(result)
        timeline = _backfill_timeline(timeline, total_duration)
        summary = _job_summary(payload, segments, voice)
        timing = f"成片时长 {_format_duration(total_duration)} · 生成耗时 {_format_duration(elapsed)}"

        update_job(
            job_id,
            status="complete",
            progress=100,
            message="生成完成",
            result=f"/outputs/{job_id}/{result.name}",
            summary=summary,
            timing=timing,
            duration=round(total_duration, 2),
            elapsed=round(elapsed, 2),
            timeline=timeline,
            voice=voice,
            updated_at=time.time(),
        )
        history.record(
            job_id,
            title=title,
            status="complete",
            duration=total_duration,
            elapsed=elapsed,
            voice=voice,
            shots=segments,
            timeline=timeline,
            file=f"{job_id}/{result.name}",
            poster=f"{job_id}/{image_path.name}",
            created_at=started_at,
        )
    except Exception as exc:
        elapsed = time.time() - started_at
        message = str(exc) if isinstance(exc, WorkflowError) else f"未预期错误：{exc}"
        update_job(job_id, status="failed", message=message, updated_at=time.time())
        history.record(
            job_id,
            title=title,
            status="failed",
            duration=0.0,
            elapsed=elapsed,
            voice=voice,
            shots=segments,
            error=message,
            poster=f"{job_id}/{image_path.name}",
            created_at=started_at,
        )
    finally:
        generation_lock.release()


def _backfill_timeline(
    timeline: list[dict[str, object]], total: float
) -> list[dict[str, object]]:
    """Keep the per-shot plan consistent with the measured final duration."""
    if not timeline or total <= 0:
        return timeline
    for index, shot in enumerate(timeline):
        shot["index"] = index + 1
    if abs(float(timeline[-1].get("end") or 0) - total) < 0.05:
        return timeline
    # 以实测成片时长缩放所有时间点，同时保留镜头间的转场重叠。
    planned = float(timeline[-1].get("end") or 0) or total
    scale = total / planned
    for shot in timeline:
        shot["duration"] = round(float(shot.get("duration") or 0) * scale, 2)
        shot["start"] = round(float(shot.get("start") or 0) * scale, 2)
        shot["end"] = round(float(shot.get("end") or 0) * scale, 2)
    return timeline


class Handler(BaseHTTPRequestHandler):
    server_version = "TalkingPortrait/1.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _json(self, value: object, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = path.stat().st_size
        start, end = 0, max(0, size - 1)
        partial = False
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes=") and "," not in range_header:
            try:
                first, last = range_header.removeprefix("bytes=").split("-", 1)
                if first:
                    start = int(first)
                    end = int(last) if last else end
                elif last:
                    length = int(last)
                    start = max(0, size - length)
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
                partial = True
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        content_length = max(0, end - start + 1)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = content_length
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        if route == "/api/ping":
            self._json({"ok": True})
            return
        if route == "/api/health":
            load_dotenv()
            report = config.runtime_report()
            report["bgm"] = list_bgm()
            report["avatars"] = config.list_avatars()
            report["providers"] = provider_status()
            report["acceleration"] = acceleration_report()
            self._json(report)
            return
        if route == "/api/bgm":
            self._json({"tracks": list_bgm()})
            return
        if route == "/api/media":
            self._json(list_media_assets())
            return
        if route == "/api/voices":
            if parsed.query == 'engine=kokoro':
                from pipeline.kokoro_tts import VOICES
                self._json({'voices': VOICES, 'current': 'kokoro:3'})
                return
            self._json({"voices": config.list_voices(), "current": config.resolve_voice(None)})
            return
        if route == "/api/fish/voices":
            self._json({"voices": list_fish_voices()})
            return
        if route == "/api/avatars":
            self._json({"avatars": config.list_avatars()})
            return
        if route == "/api/presets":
            self._json({"industries": presets.industries()})
            return
        if route == "/api/history":
            entries = history.listing()
            for entry in entries:
                if entry.get("file"):
                    entry["url"] = f"/outputs/{entry['file']}"
                if entry.get("poster"):
                    entry["posterUrl"] = f"/outputs/{entry['poster']}"
            self._json({"entries": entries})
            return
        if route == "/api/jobs/active":
            with jobs_lock:
                active = [
                    dict(item)
                    for item in jobs.values()
                    if item.get("status") in {"queued", "running"}
                ]
            active.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
            self._json({"job": active[0] if active else None})
            return
        if route.startswith("/api/jobs/"):
            job_id = route.rsplit("/", 1)[-1]
            snapshot = _read_job(job_id)
            if snapshot is None:
                # 服务重启后内存任务丢失，回落到持久化历史。
                stored = history.get(job_id)
                if stored is None:
                    self._json({"error": "任务不存在"}, HTTPStatus.NOT_FOUND)
                else:
                    self._json(
                        {
                            **stored,
                            "progress": 100,
                            "message": stored.get("error") or "生成完成",
                            "result": f"/outputs/{stored['file']}" if stored.get("file") else "",
                        }
                    )
            else:
                self._json(snapshot)
            return
        if route.startswith("/avatars/"):
            candidate = config.resolve_avatar(route.removeprefix("/avatars/"))
            if candidate is None:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._file(candidate)
            return
        if route.startswith("/fish-voices/"):
            parts = route.removeprefix("/fish-voices/").split("/", 1)
            candidate = resolve_voice_asset(parts[0], parts[1] if len(parts) > 1 else "")
            if candidate is None:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._file(candidate)
            return
        if route.startswith("/assets/"):
            parts = route.removeprefix("/assets/").split("/", 1)
            candidate = resolve_media_asset(parts[0], parts[1] if len(parts) > 1 else "")
            if candidate is None:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._file(candidate)
            return
        if route.startswith("/outputs/"):
            relative = Path(route.removeprefix("/outputs/") or ".")
            candidate = (config.OUTPUT_DIR / relative).resolve()
            if config.OUTPUT_DIR.resolve() not in candidate.parents:
                self.send_error(HTTPStatus.FORBIDDEN)
            else:
                self._file(candidate)
            return
        asset = "index.html" if route == "/" else route.lstrip("/")
        candidate = (WEB_DIR / asset).resolve()
        if WEB_DIR.resolve() not in candidate.parents and candidate != WEB_DIR.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
        else:
            self._file(candidate)

    def do_DELETE(self) -> None:
        route = unquote(urlparse(self.path).path)
        if route.startswith("/api/fish/voices/"):
            voice_id = route.rsplit("/", 1)[-1]
            if remove_fish_voice(voice_id):
                self._json({"removed": voice_id})
            else:
                self._json({"error": "音色不存在"}, HTTPStatus.NOT_FOUND)
            return
        if route.startswith("/api/history/"):
            job_id = route.rsplit("/", 1)[-1]
            if job_id == "all":
                history.clear()
                self._json({"cleared": True})
            elif history.remove(job_id):
                self._json({"removed": job_id})
            else:
                self._json({"error": "记录不存在"}, HTTPStatus.NOT_FOUND)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/fish/voices":
            self._create_fish_voice()
            return
        if route == "/api/fish/preview":
            self._preview_fish_voice()
            return
        if route != "/api/generate":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("请求为空或总大小超过 40MB，请压缩后重试。")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            text = str(payload.get("text", "")).strip()
            if not text:
                raise ValueError("请输入口播文案。")

            # 优先使用上传的照片；未上传时回落到虚拟人像库里的形象。
            avatar_name = str(payload.get("avatar", "")).strip()
            image_bytes: bytes | None = None
            extension = ".jpg"
            image_data = str(payload.get("imageData", ""))
            if image_data:
                if not image_data.startswith("data:image/") or ";base64," not in image_data:
                    raise ValueError("请上传 JPG、PNG 或 WebP 人像照片。")
                _header, image_bytes = _decode_data_url(image_data)
                if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
                    raise ValueError("照片为空或超过 12MB。")
                extension = _image_extension(image_bytes)
            elif not config.resolve_avatar(avatar_name):
                raise ValueError("请上传照片，或从虚拟人像库中选择一个形象。")

            bgm = payload.get("bgm")
            if isinstance(bgm, str) and bgm.startswith("data:audio/"):
                _, audio_bytes = _decode_data_url(bgm)
                if not audio_bytes or len(audio_bytes) > 18 * 1024 * 1024:
                    raise ValueError("背景音乐为空或超过 18MB。")
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        report = config.runtime_report()
        avatar_engine = str(payload.get("avatarEngine") or "sadtalker").strip().lower()
        # HeyGen can run without local SadTalker checkpoints; still need ffmpeg for packaging.
        local_missing = [item for item in report["missing"] if "SadTalker" not in item and "Piper" not in item]
        tts_engine = str(payload.get("ttsEngine") or "piper").strip().lower()
        if tts_engine not in {"fish", "fish_local", "kokoro"} and any("Piper" in item for item in report["missing"]):
            local_missing.append("Piper 中文声音")
        if avatar_engine != "heygen" and any("SadTalker" in item for item in report["missing"]):
            local_missing.extend([item for item in report["missing"] if "SadTalker" in item])
        if local_missing:
            self._json(
                {"error": "运行环境未安装完整，请先双击 setup.bat，或改选已配置的云端引擎。", "missing": local_missing},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return

        job_id = uuid.uuid4().hex[:12]
        job_dir = config.OUTPUT_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        image_path = job_dir / f"portrait{extension}"
        if image_bytes is not None:
            image_path.write_bytes(image_bytes)
        else:
            source = config.resolve_avatar(avatar_name)
            assert source is not None
            image_path.write_bytes(source.read_bytes())

        payload["_started_at"] = time.time()
        with jobs_lock:
            jobs[job_id] = {
                "id": job_id,
                "status": "running",
                "progress": 2,
                "message": "任务已创建",
                "voice": str(payload.get("voice") or ""),
                "acceleration": selection_label(payload.get("acceleration")),
                "created_at": time.time(),
            }
        thread = threading.Thread(target=run_job, args=(job_id, image_path, payload), daemon=True)
        thread.start()
        self._json({"jobId": job_id}, HTTPStatus.ACCEPTED)

    def _read_json_body(self, limit: int = MAX_BODY_BYTES) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > limit:
            raise ValueError("请求为空或文件过大。")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求格式不正确。")
        return value

    def _create_fish_voice(self) -> None:
        try:
            payload = self._read_json_body(24 * 1024 * 1024)
            header, audio = _decode_data_url(str(payload.get("audioData") or ""))
            suffix = audio_extension(header, audio)
            voice = save_fish_voice(
                str(payload.get("label") or ""),
                str(payload.get("transcript") or ""),
                audio,
                suffix,
            )
            self._json({"voice": voice}, HTTPStatus.CREATED)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _preview_fish_voice(self) -> None:
        try:
            payload = self._read_json_body(64 * 1024)
            script = str(payload.get("text") or "").strip()
            if not script:
                raise ValueError("请先输入一小段试听文字。")
            if len(script) > 500:
                raise ValueError("试听文字请控制在 500 字以内。")
            preview_dir = config.OUTPUT_DIR / "fish-preview"
            preview_dir.mkdir(parents=True, exist_ok=True)
            output = preview_dir / f"{uuid.uuid4().hex[:12]}.wav"
            synthesize_fish_local(
                script,
                output,
                speed=float(payload.get("speed") or 1.0),
                reference_id=str(payload.get("voice") or "").strip() or None,
            )
            self._json({"url": f"/outputs/fish-preview/{output.name}"})
        except (ValueError, OSError, WorkflowError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def main() -> None:
    parser = argparse.ArgumentParser(description="本地照片数字人口播自动剪辑工作流")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config.BGM_DIR.mkdir(parents=True, exist_ok=True)
    config.AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"数字人口播自动剪辑工作流已启动：{url}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
