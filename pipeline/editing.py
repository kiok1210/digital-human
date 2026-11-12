"""Automatic editing helpers: title cards, crossfade assembly and BGM mixing."""

from __future__ import annotations

import os
from pathlib import Path

from . import acceleration as gpu_acceleration
from . import config
from .proc import WorkflowError, probe_duration, run_command
from .text import write_animated_ass

# 与前端「转场风格」下拉框一一对应。
TRANSITIONS = (
    "fade",
    "dissolve",
    "slideleft",
    "slideup",
    "circleopen",
    "smoothleft",
)

_FONT_CANDIDATES = ("msyhbd.ttc", "msyh.ttc", "simhei.ttf", "SourceHanSansSC-Bold.otf")

WIDTH = 1080
HEIGHT = 1920
FPS = 25


def _is_still(path: Path) -> bool:
    return path.suffix.lower() in config.IMAGE_SUFFIXES


def _looping_input(path: Path) -> list[str]:
    """FFmpeg input arguments that make short visual assets last long enough."""
    if _is_still(path):
        return ["-loop", "1", "-framerate", str(FPS), "-i", str(path)]
    return ["-stream_loop", "-1", "-i", str(path)]


def _caption_filter(script: str, duration: float, output: Path) -> str:
    ass = output.parent / f"{output.stem}-captions.ass"
    write_animated_ass(script, duration, ass)
    expression = f"ass='{_filter_path(ass)}'"
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    if font_dir.exists():
        expression += f":fontsdir='{_filter_path(font_dir)}'"
    return expression


def _require_ffmpeg() -> None:
    if not config.FFMPEG:
        raise WorkflowError("找不到 ffmpeg，请重新运行 setup.bat。")


def _run_video_encode(
    command: list[str],
    tail: list[str],
    output: Path,
    label: str,
    acceleration: object,
) -> None:
    """Use a verified hardware encoder and retry with x264 if it still fails."""
    encode, hardware = gpu_acceleration.video_encode_args(acceleration)
    try:
        run_command([*command, *encode, *tail], output.parent, label)
    except WorkflowError:
        if not hardware:
            raise
        run_command(
            [*command, *gpu_acceleration.CPU_VIDEO_ARGS, *tail],
            output.parent,
            f"{label}（GPU 回退 CPU）",
        )


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")


def _font_file() -> str | None:
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in _FONT_CANDIDATES:
        candidate = fonts / name
        if candidate.exists():
            return _filter_path(candidate)
    return None


def build_silence(output: Path, duration: float) -> Path:
    """Create a silent wav so cards can join the crossfade chain."""
    _require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            config.FFMPEG, "-y",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", f"{max(0.1, duration):.3f}",
            "-c:a", "pcm_s16le",
            str(output),
        ],
        output.parent,
        "生成静音音轨",
    )
    return output


def build_card(
    output: Path,
    title: str,
    duration: float = 2.6,
    background: Path | None = None,
    fade_in: bool = True,
    acceleration: object = "auto",
) -> Path:
    """Render a 1080x1920, 25fps silent card, optionally over a blurred photo."""
    _require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)
    body = title.strip()

    command = [config.FFMPEG, "-y"]
    if background is not None and background.exists():
        command += ["-loop", "1", "-i", str(background)]
        chain = (
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},gblur=sigma=28,eq=brightness=-0.12:saturation=0.82"
        )
    else:
        command += ["-f", "lavfi", "-i", f"color=c=0x161613:s={WIDTH}x{HEIGHT}:r={FPS}"]
        chain = "null"

    filters = [f"[0:v]{chain},setsar=1[base]"]
    label = "base"
    font = _font_file()
    if font and body:
        text_file = output.parent / "card_title.txt"
        text_file.write_text(body, encoding="utf-8")
        draw = (
            f"drawtext=fontfile='{font}':textfile=card_title.txt:"
            "fontcolor=white:fontsize=92:line_spacing=16:"
            "x=(w-text_w)/2:y=(h-text_h)/2-60:"
            "shadowcolor=black@0.55:shadowx=0:shadowy=6"
        )
        filters.append(f"[{label}]{draw}[title]")
        label = "title"

    if fade_in:
        filters.append(f"[{label}]fade=t=in:st=0:d=0.5[card]")
        label = "card"

    command += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{label}]",
        "-t", f"{max(0.2, duration):.3f}",
        "-r", str(FPS),
    ]
    _run_video_encode(command, ["-an", str(output)], output, "生成封面镜头", acceleration)
    return output


def _video_filters(count: int) -> list[str]:
    # xfade refuses to initialise unless every input reports a valid constant
    # frame rate. Two traps, both verified against ffmpeg 7.1:
    #   * setpts=PTS-STARTPTS placed *after* fps= makes the pad report rate 1/0
    #   * settb=AVTB (1/1000000) does the same
    # So reset the timestamps first, then normalise frame rate and pixel format.
    return [
        f"[{index}:v]setpts=PTS-STARTPTS,fps={FPS},"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{index}]"
        for index in range(count)
    ]


def _audio_filters(count: int) -> list[str]:
    return [
        f"[{count + index}:a]aformat=sample_fmts=fltp:sample_rates=44100:"
        f"channel_layouts=stereo,asetpts=PTS-STARTPTS[a{index}]"
        for index in range(count)
    ]


def _chain_xfade(
    filters: list[str], durations: list[float], blend: float, style: str
) -> tuple[str, str, float]:
    count = len(durations)
    previous = "v0"
    consumed = 0.0
    for index in range(count - 1):
        consumed += durations[index]
        offset = max(0.0, consumed - (index + 1) * blend)
        label = "vc" if index == count - 2 else f"vx{index}"
        filters.append(
            f"[{previous}][v{index + 1}]xfade=transition={style}:"
            f"duration={blend:.3f}:offset={offset:.3f}[{label}]"
        )
        previous = label

    previous_audio = "a0"
    for index in range(count - 1):
        label = "ac" if index == count - 2 else f"ax{index}"
        filters.append(
            f"[{previous_audio}][a{index + 1}]acrossfade=d={blend:.3f}:c1=tri:c2=tri[{label}]"
        )
        previous_audio = label

    total = sum(durations) - (count - 1) * blend
    return previous, previous_audio, max(0.2, total)


def _chain_concat(filters: list[str], durations: list[float]) -> tuple[str, str, float]:
    count = len(durations)
    filters.append(
        "".join(f"[v{index}]" for index in range(count)) + f"concat=n={count}:v=1:a=0[vc]"
    )
    filters.append(
        "".join(f"[a{index}]" for index in range(count)) + f"concat=n={count}:v=0:a=1[ac]"
    )
    return "vc", "ac", max(0.2, sum(durations))


def assemble(
    segments: list[tuple[Path, Path]],
    output: Path,
    transition: float = 0.6,
    transition_style: str = "fade",
    acceleration: object = "auto",
) -> Path:
    """Join (silent video, audio) segments into one film with automatic transitions."""
    _require_ffmpeg()
    if not segments:
        raise WorkflowError("没有可剪辑的镜头。")

    durations = [probe_duration(video) for video, _ in segments]
    count = len(segments)
    style = transition_style if transition_style in TRANSITIONS else "fade"

    command = [config.FFMPEG, "-y"]
    for video, _ in segments:
        command += ["-i", str(video)]
    for _, audio in segments:
        command += ["-i", str(audio)]

    filters = _video_filters(count) + _audio_filters(count)
    blend = max(0.0, min(float(transition), min(durations) * 0.4))

    if count == 1:
        video_label, audio_label, total = "v0", "a0", max(0.2, durations[0])
    elif blend < 0.05:
        video_label, audio_label, total = _chain_concat(filters, durations)
    else:
        video_label, audio_label, total = _chain_xfade(filters, durations, blend, style)

    # 音频按成片长度补齐或截断，保证与画面一致。
    filters.append(
        f"[{audio_label}]apad=whole_dur={total:.3f},atrim=0:{total:.3f}[outa]"
    )

    command += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{video_label}]",
        "-map", "[outa]",
    ]
    tail = [
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
        str(output),
    ]
    _run_video_encode(command, tail, output, "自动剪辑成片", acceleration)
    return output


def mix_bgm(video: Path, bgm: Path, output: Path, volume: float = 0.16) -> Path:
    """Loop the background music under the film with a gentle fade out."""
    _require_ffmpeg()
    duration = probe_duration(video)
    volume = max(0.0, min(0.6, float(volume)))
    fade_start = max(0.0, duration - 1.5)
    filters = (
        f"[1:a]volume={volume:.3f},afade=t=in:st=0:d=1.0,"
        f"afade=t=out:st={fade_start:.3f}:d=1.5[bg];"
        "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"
    )
    run_command(
        [
            config.FFMPEG, "-y",
            "-i", str(video),
            "-stream_loop", "-1", "-i", str(bgm),
            "-filter_complex", filters,
            "-map", "0:v", "-map", "[mix]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            "-shortest",
            str(output),
        ],
        output.parent,
        "混入背景音乐",
    )
    return output


def build_stock_segment(
    stock: Path,
    audio: Path,
    output: Path,
    *,
    script: str = "",
    captions: bool = True,
    acceleration: object = "auto",
) -> Path:
    """Turn a stock image/video into a vertical B-roll shot using speech audio."""
    _require_ffmpeg()
    if not stock.is_file():
        raise WorkflowError(f"素材不存在：{stock.name}")
    duration = probe_duration(audio)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [config.FFMPEG, "-y", *_looping_input(stock), "-i", str(audio)]
    chain = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1,"
        f"trim=duration={duration:.3f},setpts=PTS-STARTPTS"
    )
    if captions and script.strip():
        chain += "," + _caption_filter(script, duration, output)
    chain += "[outv]"
    command += [
        "-filter_complex", chain,
        "-map", "[outv]",
        "-map", "1:a:0",
        "-t", f"{duration:.3f}",
    ]
    tail = [
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(output),
    ]
    _run_video_encode(command, tail, output, "生成素材镜头", acceleration)
    return output


def burn_captions(
    video: Path,
    script: str,
    output: Path,
    *,
    acceleration: object = "auto",
) -> Path:
    """Burn animated captions after cutaways so text always stays visible."""
    _require_ffmpeg()
    duration = probe_duration(video)
    output.parent.mkdir(parents=True, exist_ok=True)
    caption_filter = _caption_filter(script, duration, output)
    command = [
        config.FFMPEG, "-y",
        "-i", str(video),
        "-vf", caption_filter,
        "-map", "0:v:0", "-map", "0:a?",
        "-t", f"{duration:.3f}",
    ]
    tail = [
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(output),
    ]
    _run_video_encode(command, tail, output, "叠加动态字幕", acceleration)
    return output


def overlay_stock_cutaways(
    video: Path,
    stocks: list[Path],
    output: Path,
    *,
    acceleration: object = "auto",
) -> Path:
    """Insert full-frame B-roll windows while preserving the original speech."""
    _require_ffmpeg()
    usable = [path for path in stocks[:6] if path.is_file()]
    if not usable:
        return video
    total = probe_duration(video)
    # Very short clips do not have enough room for a useful cutaway.
    if total < 1.2:
        return video

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [config.FFMPEG, "-y", "-i", str(video)]
    for stock in usable:
        command += _looping_input(stock)

    filters: list[str] = []
    previous = "0:v"
    window = min(3.2, max(1.2, total / (len(usable) * 2.4)))
    for index, _stock in enumerate(usable, start=1):
        center = total * index / (len(usable) + 1)
        start = max(0.25, min(total - window, center - window / 2))
        end = min(total, start + window)
        asset_label = f"cut{index}"
        out_label = "cutv" if index == len(usable) else f"cutout{index}"
        filters.append(
            f"[{index}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1,setpts=PTS-STARTPTS[{asset_label}]"
        )
        filters.append(
            f"[{previous}][{asset_label}]overlay=0:0:eof_action=pass:"
            f"enable='between(t\\,{start:.3f}\\,{end:.3f})'[{out_label}]"
        )
        previous = out_label

    command += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{previous}]", "-map", "0:a?",
        "-t", f"{total:.3f}",
    ]
    tail = [
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(output),
    ]
    _run_video_encode(command, tail, output, "插入素材画面", acceleration)
    return output


def _number(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def overlay_stickers(
    video: Path,
    stickers: list[dict[str, object]],
    output: Path,
    *,
    acceleration: object = "auto",
) -> Path:
    """Overlay timed PNG/WebP/GIF/video reactions on a finished film."""
    _require_ffmpeg()
    usable = [spec for spec in stickers[:8] if isinstance(spec.get("path"), Path) and spec["path"].is_file()]
    if not usable:
        return video
    total = probe_duration(video)
    output.parent.mkdir(parents=True, exist_ok=True)

    command = [config.FFMPEG, "-y", "-i", str(video)]
    for spec in usable:
        command += _looping_input(spec["path"])  # type: ignore[arg-type]

    positions = {
        "top-left": ("56", "100"),
        "top-right": ("W-w-56", "100"),
        "bottom-left": ("56", "H-h-360"),
        "bottom-right": ("W-w-56", "H-h-360"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }
    position_cycle = ("top-right", "top-left", "bottom-right", "bottom-left")
    filters: list[str] = []
    previous = "0:v"
    default_duration = min(2.8, max(1.1, total / max(2.0, len(usable) * 1.5)))
    for index, spec in enumerate(usable, start=1):
        visible = max(0.35, min(total, _number(spec.get("duration"), default_duration)))
        default_start = max(0.0, total * index / (len(usable) + 1) - visible / 2)
        start = max(0.0, min(max(0.0, total - visible), _number(spec.get("start"), default_start)))
        end = min(total, start + visible)
        width = int(max(72, min(420, _number(spec.get("width"), 230))))
        position = str(spec.get("position") or position_cycle[(index - 1) % len(position_cycle)])
        x, y = positions.get(position, positions[position_cycle[(index - 1) % len(position_cycle)]])
        sticker_label = f"st{index}"
        out_label = "stickered" if index == len(usable) else f"stout{index}"
        filters.append(
            f"[{index}:v]fps={FPS},scale={width}:-1:flags=lanczos,format=rgba,"
            f"setpts=PTS-STARTPTS[{sticker_label}]"
        )
        filters.append(
            f"[{previous}][{sticker_label}]overlay=x={x}:y={y}:eof_action=pass:"
            f"enable='between(t\\,{start:.3f}\\,{end:.3f})'[{out_label}]"
        )
        previous = out_label

    command += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{previous}]", "-map", "0:a?",
        "-t", f"{total:.3f}",
    ]
    tail = [
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(output),
    ]
    _run_video_encode(command, tail, output, "叠加贴纸表情", acceleration)
    return output
