from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path
from typing import Callable

from . import acceleration as gpu_acceleration
from . import config
from .cloud import animate_heygen, synthesize_fish, synthesize_fish_local
from .editing import (
    TRANSITIONS,
    assemble,
    build_stock_segment,
    build_card,
    build_silence,
    burn_captions,
    mix_bgm,
    overlay_stickers,
    overlay_stock_cutaways,
)
from .hyperframes_post import package_with_hyperframes
from .lipsync import gfpgan_ready, prepare_lipsync_audio, sadtalker_profile
from .media_assets import asset_catalog, list_assets, resolve_asset, resolve_assets, resolve_sticker_specs
from .proc import WorkflowError, probe_duration, run_command
from .providers import (
    load_dotenv,
    resolve_avatar_engine,
    resolve_post_engine,
    resolve_tts_engine,
)
from .text import normalize_script, write_animated_ass


Progress = Callable[[int, str], None]


def _notify(progress: Progress | None) -> Progress:
    return progress or (lambda _percent, _message: None)


def _tts(
    script: str,
    output_wav: Path,
    speed: float,
    voice: str | None = None,
    *,
    tts_engine: str = "piper",
) -> str:
    """Synthesize speech. Returns the human-readable engine/voice label used."""
    load_dotenv()
    engine = resolve_tts_engine(tts_engine)
    if engine == 'kokoro':
        script_path = output_wav.with_suffix('.txt')
        script_path.write_text(script, encoding='utf-8')
        run_command([config.PYTHON, '-m', 'pipeline.kokoro_tts',
                     '--text-file', str(script_path), '--output', str(output_wav),
                     '--speed', str(speed), '--voice', voice or 'kokoro:3'],
                    config.ROOT, '生成 Kokoro 中文配音', timeout=300)
        return 'Kokoro 中文自然配音'
    if engine == "fish_local":
        synthesize_fish_local(script, output_wav, speed=speed, reference_id=voice if voice and not str(voice).startswith("zh_CN-") else None)
        return f"Fish Speech 本地{f' · {voice}' if voice and not str(voice).startswith('zh_CN-') else ''}"
    if engine == "fish":
        synthesize_fish(script, output_wav, speed=speed, reference_id=voice if voice and not str(voice).startswith("zh_CN-") else None)
        return f"Fish Audio{f' · {voice}' if voice and not str(voice).startswith('zh_CN-') else ''}"

    script_path = output_wav.with_suffix(".txt")
    script_path.write_text(script, encoding="utf-8")
    model = config.resolve_voice(voice)
    run_command(
        [
            config.PYTHON,
            "-m", "piper",
            "--model", model,
            "--data-dir", str(config.PIPER_DIR),
            "--input-file", str(script_path),
            "--output-file", str(output_wav),
            "--sentence-silence", "0.12",
            "--length-scale", f"{1.0 / speed:.3f}",
        ],
        config.ROOT,
        "生成配音",
    )
    return model


def _animate(
    image: Path,
    audio: Path,
    raw_dir: Path,
    portrait_mode: str,
    *,
    avatar_engine: str = "sadtalker",
    acceleration: object = "auto",
    mouth_style: str = "natural",
    face_enhance: bool = False,
) -> Path:
    load_dotenv()
    engine = resolve_avatar_engine(avatar_engine)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if engine == "heygen":
        return animate_heygen(image, audio, raw_dir / "heygen.mp4")

    report = config.runtime_report()
    if not report["ready"]:
        raise WorkflowError("运行环境未安装完整：" + "、".join(report["missing"]))
    profile = sadtalker_profile(portrait_mode, mouth_style)
    lip_audio = prepare_lipsync_audio(audio, raw_dir / "lipsync-16k.wav")
    command = [
        config.PYTHON,
        str(config.SADTALKER_DIR / "inference.py"),
        "--driven_audio", str(lip_audio),
        "--source_image", str(image),
        "--checkpoint_dir", str(config.SADTALKER_DIR / "checkpoints"),
        "--result_dir", str(raw_dir),
        "--size", str(profile.size),
        "--batch_size", str(profile.batch_size),
        "--preprocess", profile.preprocess,
        "--expression_scale", f"{profile.expression_scale:.2f}",
        "--pose_style", str(profile.pose_style),
    ]
    if profile.still:
        command.append("--still")
    if face_enhance and gfpgan_ready():
        command += ["--enhancer", "gfpgan"]
    use_gpu = gpu_acceleration.model_gpu_enabled(acceleration)
    if not use_gpu:
        command.append("--cpu")
    try:
        run_command(command, config.SADTALKER_DIR, "生成数字人口播")
    except WorkflowError:
        if not use_gpu:
            raise
        run_command([*command, "--cpu"], config.SADTALKER_DIR, "生成数字人口播（GPU 回退 CPU）")
    videos = sorted(raw_dir.glob("*.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not videos:
        raise WorkflowError("SadTalker 已结束，但没有找到输出视频。")
    return videos[0]


def _compose(
    raw_video: Path,
    script: str,
    output: Path,
    captions: bool,
    acceleration: object = "auto",
    face_enhance: bool = False,
) -> None:
    if not config.FFMPEG:
        raise WorkflowError("找不到 ffmpeg，请重新运行 setup.bat。")
    filters = (
        "[0:v]split=2[base][front];"
        "[base]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
        "crop=1080:1920,gblur=sigma=24[bg];"
        "[front]scale=1000:1580:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"unsharp=5:5:{'0.55' if face_enhance else '0.28'}:3:3:0[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2-70"
    )
    if captions:
        duration = probe_duration(raw_video)
        write_animated_ass(script, duration, output.parent / "captions.ass")
        font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        if font_dir.exists():
            escaped_font_dir = str(font_dir).replace("\\", "/").replace(":", "\\:")
            filters += f",ass=captions.ass:fontsdir='{escaped_font_dir}'"
        else:
            filters += ",ass=captions.ass"
    filters += "[outv]"
    command = [
        config.FFMPEG,
        "-y",
        "-i", str(raw_video),
        "-filter_complex", filters,
        "-map", "[outv]",
        "-map", "0:a?",
    ]
    tail = [
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
        str(output),
    ]
    encode, hardware = gpu_acceleration.video_encode_args(acceleration)
    try:
        run_command([*command, *encode, *tail], output.parent, "合成竖屏视频")
    except WorkflowError:
        if not hardware:
            raise
        run_command(
            [*command, *gpu_acceleration.CPU_VIDEO_ARGS, *tail],
            output.parent,
            "合成竖屏视频（GPU 回退 CPU）",
        )


def generate_video(
    image: Path,
    text: str,
    job_dir: Path,
    portrait_mode: str = "close",
    captions: bool = True,
    speed: float = 1.0,
    voice: str | None = None,
    bgm: object = None,
    bgm_volume: float = 0.16,
    tts_engine: str = "piper",
    avatar_engine: str = "sadtalker",
    post_engine: str = "ffmpeg",
    acceleration: object = "auto",
    mouth_style: str = "natural",
    face_enhance: bool = False,
    progress: Progress | None = None,
    stock_assets: object = None,
    stickers: object = None,
) -> Path:
    """Run text-to-speech, portrait animation and vertical composition."""
    notify = _notify(progress)
    load_dotenv()
    script = normalize_script(text)
    _validate_script(script)
    mode = _validate_mode(portrait_mode)
    speed = _validate_speed(speed)
    post = resolve_post_engine(post_engine)
    try:
        volume = min(0.6, max(0.0, float(bgm_volume)))
    except (TypeError, ValueError):
        volume = 0.16
    job_dir.mkdir(parents=True, exist_ok=True)

    audio = job_dir / "speech.wav"
    notify(8, "正在生成中文配音")
    voice_label = _tts(script, audio, speed, voice, tts_engine=tts_engine)
    notify(12, f"配音完成：{voice_label}")

    notify(25, "正在分析照片并驱动口型")
    raw_video = _animate(
        image,
        audio,
        job_dir / "raw",
        mode,
        avatar_engine=avatar_engine,
        acceleration=acceleration,
        mouth_style=mouth_style,
        face_enhance=face_enhance,
    )

    bgm_path = _resolve_bgm(bgm, job_dir)
    stocks = resolve_assets(stock_assets, "stock", limit=4)
    sticker_specs = resolve_sticker_specs(stickers)

    if post == "hyperframes":
        notify(80, "正在用 HyperFrames 包装竖屏成片")
        # Keep picture-only compose without burned ASS; HF draws captions.
        plate = job_dir / "plate.mp4"
        _compose(
            raw_video,
            script,
            plate,
            captions=False,
            acceleration=acceleration,
            face_enhance=face_enhance,
        )
        hf_plate = plate
        if stocks:
            notify(84, "正在插入素材画面")
            hf_plate = overlay_stock_cutaways(
                plate, stocks, job_dir / "plate-with-stock.mp4", acceleration=acceleration
            )
        packaged = package_with_hyperframes(
            [(hf_plate, audio, script)],
            job_dir / ("packaged.mp4" if sticker_specs else "final.mp4"),
            title="",
            captions=captions,
            transition=0.0,
            bgm=bgm_path,
            bgm_volume=volume,
        )
        final_video = packaged
        if sticker_specs:
            notify(96, "正在叠加贴纸表情")
            final_video = overlay_stickers(
                packaged, sticker_specs, job_dir / "final.mp4", acceleration=acceleration
            )
        notify(100, "生成完成")
        return final_video

    notify(88, "正在合成竖屏画面和缩放大字")
    needs_post = bool(stocks or sticker_specs or bgm_path)
    composed = job_dir / ("portrait.mp4" if stocks else "composed.mp4" if needs_post else "final.mp4")
    # Captions are applied after full-screen cutaways so they stay readable.
    _compose(
        raw_video,
        script,
        composed,
        captions and not stocks,
        acceleration=acceleration,
        face_enhance=face_enhance,
    )
    visual = composed
    if stocks:
        notify(91, "正在插入素材画面")
        visual = overlay_stock_cutaways(
            visual, stocks, job_dir / "stock-cutaways.mp4", acceleration=acceleration
        )
        if captions:
            visual = burn_captions(
                visual, script, job_dir / "captioned.mp4", acceleration=acceleration
            )
    if sticker_specs:
        notify(93, "正在叠加贴纸表情")
        visual = overlay_stickers(
            visual, sticker_specs, job_dir / "stickered.mp4", acceleration=acceleration
        )

    if bgm_path is not None:
        notify(94, f"正在混入背景音乐：{bgm_path.name}")
        final_video = mix_bgm(visual, bgm_path, job_dir / "final.mp4", volume)
    else:
        final_video = job_dir / "final.mp4"
        if visual != final_video:
            visual.replace(final_video)

    notify(100, "生成完成")
    return final_video

def build_shot_timeline(
    shot_names: list[str],
    shot_scripts: list[str],
    durations: list[float],
    transition: float,
) -> list[dict[str, object]]:
    """Describe every shot with its start/end on the final timeline."""
    timeline: list[dict[str, object]] = []
    cursor = 0.0
    for index, (name, text, duration) in enumerate(zip(shot_names, shot_scripts, durations)):
        start = cursor
        end = start + duration
        timeline.append(
            {
                "index": index + 1,
                "name": name,
                "text": text[:40],
                "duration": round(duration, 2),
                "start": round(start, 2),
                "end": round(end, 2),
            }
        )
        # 相邻镜头通过交叉淡化重叠，下一个镜头的起点要往前挪。
        cursor = end - (transition if index < len(durations) - 1 else 0.0)
    return timeline


def _validate_script(script: str, limit: int = 3000) -> None:
    if not script:
        raise WorkflowError("口播文案不能为空。")
    if len(script) > limit:
        raise WorkflowError(f"文案不能超过 {limit} 字，请拆分后生成。")


def _validate_mode(mode: str) -> str:
    if mode not in {"close", "full"}:
        raise WorkflowError("未知的照片取景方式。")
    return mode


def _validate_speed(speed: float) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("语速参数无效。") from exc
    return min(1.35, max(0.75, value))


def _resolve_bgm(payload_value: object, upload_dir: Path) -> Path | None:
    """Accept an uploaded data URL or a file stored in assets/bgm."""
    value = str(payload_value or "").strip()
    if not value:
        return None

    if value.startswith("data:audio/"):
        header, _, encoded = value.partition(",")
        if not encoded:
            return None
        suffix = ".mp3" if "mpeg" in header else ".m4a" if "mp4" in header else ".wav"
        destination = upload_dir / f"custom-bgm-{uuid.uuid4().hex[:8]}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.write_bytes(base64.b64decode(encoded, validate=False))
        except (ValueError, TypeError):
            return None
        return destination

    return resolve_asset(value, "bgm")


def list_bgm() -> list[str]:
    return [str(item["id"]) for item in list_assets("bgm")]


def list_media_assets() -> dict[str, list[dict[str, object]]]:
    """Return stock, sticker and music libraries for an API or desktop UI."""
    return asset_catalog()


def generate_edited_video(
    image: Path,
    script: str,
    job_dir: Path,
    portrait_mode: str = "close",
    captions: bool = True,
    speed: float = 1.0,
    segments: int = 3,
    title: str = "",
    transition: float = 0.6,
    transition_style: str = "fade",
    bgm: object = None,
    bgm_volume: float = 0.16,
    voice: str | None = None,
    tts_engine: str = "piper",
    avatar_engine: str = "sadtalker",
    post_engine: str = "ffmpeg",
    acceleration: object = "auto",
    mouth_style: str = "natural",
    face_enhance: bool = False,
    progress: Progress | None = None,
    stock_assets: object = None,
    stickers: object = None,
) -> tuple[Path, list[dict[str, object]]]:
    """One text, one photo: auto cut into multiple shots, then assemble a film.

    Returns the final file together with a per-shot timeline so the UI can show
    how long every shot runs and when it appears in the finished film.
    """
    notify = _notify(progress)
    load_dotenv()
    script = normalize_script(script)
    _validate_script(script, limit=3600)
    mode = _validate_mode(portrait_mode)
    speed = _validate_speed(speed)
    post = resolve_post_engine(post_engine)

    pieces = _split_for_shots(script, int(segments))
    style = transition_style if transition_style in TRANSITIONS else "fade"
    try:
        blend = min(2.0, max(0.0, float(transition)))
    except (TypeError, ValueError):
        blend = 0.6
    try:
        volume = min(0.6, max(0.0, float(bgm_volume)))
    except (TypeError, ValueError):
        volume = 0.16

    job_dir.mkdir(parents=True, exist_ok=True)
    work = job_dir / "shots"
    work.mkdir(parents=True, exist_ok=True)
    stocks = resolve_assets(stock_assets, "stock", limit=6)
    sticker_specs = resolve_sticker_specs(stickers)

    plan = [(piece, index + 1) for index, piece in enumerate(pieces)]
    if title.strip() and post == "ffmpeg":
        plan.insert(0, ("", 0))
    total = len(plan) if post == "ffmpeg" else len(pieces)

    def stage(index: int) -> int:
        return 6 + int(74 * index / max(1, total))

    shots: list[tuple[Path, Path]] = []
    hf_shots: list[tuple[Path, Path, str]] = []
    names: list[str] = []
    scripts: list[str] = []

    for order, (piece, number) in enumerate(plan):
        notify(stage(order), f"正在制作第 {order + 1}/{len(plan)} 个镜头")
        if number == 0:
            card_video = build_card(
                work / "card.mp4",
                title,
                background=image,
                acceleration=acceleration,
            )
            card_audio = build_silence(work / "card.wav", probe_duration(card_video))
            shots.append((card_video, card_audio))
            names.append("封面卡")
            scripts.append(title)
            continue

        audio = work / f"shot{number}.wav"
        notify(stage(order) + 1, f"正在生成第 {order + 1}/{len(plan)} 个镜头的配音")
        voice_label = _tts(piece, audio, speed, voice, tts_engine=tts_engine)
        notify(stage(order) + 1, f"配音完成：{voice_label}")

        # When HyperFrames draws captions, skip burned-in ASS on plates.
        burn_captions = captions and post == "ffmpeg"
        notify(stage(order) + 4, f"正在给第 {order + 1}/{len(plan)} 个镜头合成竖屏")
        composed = work / f"shot{number}.mp4"
        # Alternate portrait and selected B-roll.  Shot one always establishes
        # the speaker; later even shots can be rendered without another costly
        # face-animation pass.
        use_stock = bool(stocks) and number > 1 and number % 2 == 0
        if use_stock:
            stock = stocks[((number // 2) - 1) % len(stocks)]
            notify(stage(order) + 2, f"正在生成素材镜头：{stock.stem}")
            build_stock_segment(
                stock,
                audio,
                composed,
                script=piece,
                captions=burn_captions,
                acceleration=acceleration,
            )
        else:
            notify(stage(order) + 2, f"正在驱动第 {order + 1}/{len(plan)} 个镜头的口型")
            raw_video = _animate(
                image,
                audio,
                work / f"raw{number}",
                mode,
                avatar_engine=avatar_engine,
                acceleration=acceleration,
                mouth_style=mouth_style,
                face_enhance=face_enhance,
            )
            _compose(
                raw_video,
                piece,
                composed,
                burn_captions,
                acceleration=acceleration,
                face_enhance=face_enhance,
            )
        shots.append((composed, audio))
        hf_shots.append((composed, audio, piece))
        names.append(f"镜头 {number}")
        scripts.append(piece)

    durations = [probe_duration(video) for video, _ in shots]
    if post == "hyperframes" and title.strip():
        # Cover card is authored inside HyperFrames; prepend it on the UI timeline.
        timeline = build_shot_timeline(
            ["封面卡", *names],
            [title, *scripts],
            [2.4, *durations],
            blend,
        )
    else:
        timeline = build_shot_timeline(names, scripts, durations, blend)

    bgm_path = _resolve_bgm(bgm, work)

    if post == "hyperframes":
        notify(82, "正在用 HyperFrames 自动剪辑与包装")
        packaged = package_with_hyperframes(
            hf_shots,
            job_dir / ("packaged.mp4" if sticker_specs else "final.mp4"),
            title=title.strip(),
            captions=captions,
            transition=blend,
            transition_style=style,
            bgm=bgm_path,
            bgm_volume=volume,
        )
        final_video = packaged
        if sticker_specs:
            notify(96, "正在叠加贴纸表情")
            final_video = overlay_stickers(
                packaged, sticker_specs, job_dir / "final.mp4", acceleration=acceleration
            )
        notify(100, "剪辑完成")
        return final_video, timeline

    notify(82, "正在自动剪辑：对齐转场与音轨")
    merged = assemble(
        shots,
        job_dir / "edited_raw.mp4",
        blend,
        style,
        acceleration=acceleration,
    )

    visual = merged
    if sticker_specs:
        notify(91, "正在叠加贴纸表情")
        visual = overlay_stickers(
            merged, sticker_specs, job_dir / "edited-stickered.mp4", acceleration=acceleration
        )

    if bgm_path is not None:
        notify(94, f"正在混入背景音乐：{bgm_path.name}")
        final_video = mix_bgm(visual, bgm_path, job_dir / "final.mp4", volume)
    else:
        final_video = job_dir / "final.mp4"
        if visual != final_video:
            visual.replace(final_video)

    notify(100, "剪辑完成")
    return final_video, timeline


def _split_for_shots(script: str, segments: int) -> list[str]:
    """Split the script into sentence groups so each group becomes one shot."""
    try:
        count = int(segments)
    except (TypeError, ValueError):
        count = 3
    count = max(1, min(6, count))

    normalized = script.replace("\n", "。").replace("！", "。").replace("？", "。")
    sentences = [part.strip() for part in normalized.split("。") if part.strip()]
    if not sentences:
        return [script]
    if count >= len(sentences):
        return sentences

    size = len(sentences) / count
    pieces: list[str] = []
    for index in range(count):
        start = int(round(index * size))
        end = int(round((index + 1) * size))
        if end <= start:
            continue
        pieces.append("。".join(sentences[start:end]) + "。")
    return pieces or [script]


__all__ = [
    "WorkflowError",
    "build_shot_timeline",
    "generate_video",
    "generate_edited_video",
    "list_bgm",
    "list_media_assets",
]
