"""Quality helpers for the local SadTalker lip-sync stage."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import config
from .proc import WorkflowError, run_command


MOUTH_STYLES = {"natural", "clear", "expressive"}


@dataclass(frozen=True)
class SadTalkerProfile:
    """Resolved arguments for one SadTalker invocation."""

    mouth_style: str
    preprocess: str
    size: int
    batch_size: int
    expression_scale: float
    pose_style: int
    still: bool


def normalize_mouth_style(value: object) -> str:
    style = str(value or "natural").strip().lower()
    return style if style in MOUTH_STYLES else "natural"


def _number_from_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


def _integer_from_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


def sadtalker_profile(portrait_mode: str, mouth_style: object = "natural") -> SadTalkerProfile:
    """Choose motion parameters that stay stable on portraits and CPU hosts."""

    style = normalize_mouth_style(mouth_style)
    full_frame = portrait_mode == "full"
    preprocess = "full" if full_frame else "crop"
    # Stay close to the model's native 1.0 scale.  Large values exaggerate
    # teeth and lip boundaries, especially with a smiling source portrait.
    expression = {"natural": 1.06, "clear": 1.16, "expressive": 1.28}[style]
    pose = {"natural": 3, "clear": 1, "expressive": 8}[style]
    expression = _number_from_env(
        "DOUYIN_SADTALKER_EXPRESSION_SCALE", expression, 0.8, 1.4
    )
    pose = _integer_from_env("DOUYIN_SADTALKER_POSE_STYLE", pose, 0, 45)

    configured = os.environ.get("DOUYIN_SADTALKER_PREPROCESS", "").strip().lower()
    if configured in {"crop", "extcrop", "resize", "full", "extfull"}:
        preprocess = configured

    checkpoint_512 = config.SADTALKER_DIR / "checkpoints" / "SadTalker_V0.0.2_512.safetensors"
    size = 512 if checkpoint_512.exists() else 256
    requested_size = _integer_from_env("DOUYIN_SADTALKER_SIZE", size, 256, 512)
    size = 512 if requested_size == 512 and checkpoint_512.exists() else 256

    # Full-frame paste-back needs a fixed pose to avoid a visible seam. Clear
    # mode also holds the pose so the stronger mouth movement remains legible.
    still = full_frame or style == "clear" or "full" in preprocess
    env_still = os.environ.get("DOUYIN_SADTALKER_STILL", "").strip().lower()
    if env_still in {"1", "true", "yes", "on"}:
        still = True
    elif env_still in {"0", "false", "no", "off"} and "full" not in preprocess:
        still = False

    return SadTalkerProfile(
        mouth_style=style,
        preprocess=preprocess,
        size=size,
        batch_size=1,
        expression_scale=expression,
        pose_style=pose,
        still=still,
    )


def gfpgan_ready() -> bool:
    """Return true only when enhancement can run without a surprise download."""

    weights = config.SADTALKER_DIR / "gfpgan" / "weights"
    required = (
        "GFPGANv1.4.pth",
        "alignment_WFLW_4HG.pth",
        "detection_Resnet50_Final.pth",
        "parsing_parsenet.pth",
    )
    return all((weights / name).is_file() for name in required)


def prepare_lipsync_audio(source: Path, destination: Path) -> Path:
    """Build a level-controlled 16 kHz mono track for the mel encoder.

    Fish and Piper can emit different rates and levels.  Explicit conversion
    keeps phoneme timing deterministic.  This is optional: failure returns the
    original speech so generation itself remains reliable.
    """

    source = source.resolve()
    destination = destination.resolve()
    if not config.FFMPEG or not source.is_file():
        return source
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        config.FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        "-af",
        (
            "highpass=f=55,lowpass=f=7600,"
            "acompressor=threshold=-24dB:ratio=2:attack=5:release=80:makeup=2dB,"
            "alimiter=limit=0.97"
        ),
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    try:
        run_command(command, destination.parent, "优化口型驱动音频")
    except WorkflowError:
        destination.unlink(missing_ok=True)
        return source
    return destination if destination.is_file() and destination.stat().st_size > 44 else source


__all__ = [
    "MOUTH_STYLES",
    "SadTalkerProfile",
    "gfpgan_ready",
    "normalize_mouth_style",
    "prepare_lipsync_audio",
    "sadtalker_profile",
]
