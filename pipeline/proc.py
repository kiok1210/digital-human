from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from . import config


class WorkflowError(RuntimeError):
    """面向使用者的工作流错误，服务端会直接展示这条信息。"""


def run_command(
    command: list[str],
    cwd: Path,
    label: str,
    timeout: int | None = None,
    *,
    hide_window: bool = True,
) -> str:
    """Run a subprocess and raise WorkflowError with recent output on failure."""
    creationflags = 0
    if hide_window and os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        process = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else ""
        raise WorkflowError(f"{label}超时（>{timeout}s）\n{output}") from exc
    output = process.stdout[-12000:]
    if process.returncode != 0:
        raise WorkflowError(f"{label}失败（退出码 {process.returncode}）\n{output}")
    return output


_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_duration(media: Path) -> float:
    """Read a media duration, falling back to ffmpeg when ffprobe is missing."""
    if not media.exists():
        raise WorkflowError(f"文件不存在：{media.name}")

    if config.FFPROBE:
        output = run_command(
            [
                config.FFPROBE,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(media),
            ],
            media.parent,
            "读取媒体时长",
        )
        try:
            return float(json.loads(output)["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"无法读取时长：{media.name}") from exc

    if not config.FFMPEG:
        raise WorkflowError("找不到 ffmpeg 与 ffprobe，请重新运行 setup.bat。")

    # imageio-ffmpeg only ships ffmpeg, so parse its banner instead. A null
    # output keeps ffmpeg from exiting with "at least one output file".
    output = run_command(
        [config.FFMPEG, "-hide_banner", "-i", str(media), "-f", "null", "-"],
        media.parent,
        "读取媒体时长",
    )
    match = _DURATION_PATTERN.search(output)
    if not match:
        raise WorkflowError(f"无法读取时长：{media.name}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
