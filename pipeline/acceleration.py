"""GPU discovery and conservative acceleration selection.

Detection separates three different facts that are often confused:
the computer has a GPU, FFmpeg can encode with it, and PyTorch can run
SadTalker on it.  A device may satisfy only the first or second condition.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from . import config


CPU_VIDEO_ARGS = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
_VIDEO_ARGS = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20", "-pix_fmt", "yuv420p"],
    "h264_amf": ["-c:v", "h264_amf", "-quality", "balanced", "-qp_i", "20", "-qp_p", "20", "-pix_fmt", "yuv420p"],
    "h264_qsv": ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "20", "-pix_fmt", "nv12"],
    "h264_videotoolbox": ["-c:v", "h264_videotoolbox", "-q:v", "65", "-pix_fmt", "yuv420p"],
}


def normalize_mode(value: object) -> str:
    mode = str(value or os.environ.get("DOUYIN_ACCELERATION", "auto")).strip().lower()
    return mode if mode in {"auto", "cpu", "gpu"} else "auto"


def _run(command: list[str], timeout: int = 8) -> subprocess.CompletedProcess[str] | None:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        return subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _vendor(name: str) -> str:
    lowered = name.lower()
    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered:
        return "nvidia"
    if "amd" in lowered or "radeon" in lowered or "advanced micro devices" in lowered:
        return "amd"
    if "intel" in lowered or "iris" in lowered or "arc" in lowered:
        return "intel"
    if "apple" in lowered:
        return "apple"
    return "other"


def _nvidia_devices() -> list[dict[str, object]]:
    result = _run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    if not result or result.returncode != 0:
        return []
    devices: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            memory_mb = int(float(parts[2]))
        except ValueError:
            memory_mb = 0
        devices.append(
            {
                "id": f"nvidia-{parts[0]}",
                "name": parts[1],
                "vendor": "nvidia",
                "memory_mb": memory_mb,
                "driver": parts[3],
            }
        )
    return devices


def _windows_devices() -> list[dict[str, object]]:
    if os.name != "nt":
        return []
    command = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress"
    )
    result = _run(["powershell.exe", "-NoProfile", "-Command", command], timeout=12)
    if not result or result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    devices: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        name = str(row.get("Name") or "").strip()
        if not name:
            continue
        if any(marker in name.lower() for marker in ("virtual", "remote display", "microsoft basic")):
            continue
        try:
            memory_mb = max(0, int(row.get("AdapterRAM") or 0) // 1024 // 1024)
        except (TypeError, ValueError):
            memory_mb = 0
        devices.append(
            {
                "id": f"windows-{index}",
                "name": name,
                "vendor": _vendor(name),
                "memory_mb": memory_mb,
                "driver": str(row.get("DriverVersion") or ""),
            }
        )
    return devices


def _linux_devices() -> list[dict[str, object]]:
    if os.name == "nt":
        return []
    result = _run(["lspci"], timeout=5)
    if not result or result.returncode != 0:
        return []
    devices: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        if not re.search(r"VGA|3D controller|Display controller", line, re.I):
            continue
        name = line.split(": ", 1)[-1].strip()
        devices.append(
            {
                "id": f"linux-{len(devices)}",
                "name": name,
                "vendor": _vendor(name),
                "memory_mb": 0,
                "driver": "",
            }
        )
    return devices


def _dedupe_devices(devices: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for item in devices:
        name = str(item["name"]).lower()
        existing = next(
            (
                saved
                for saved in merged
                if name in str(saved["name"]).lower() or str(saved["name"]).lower() in name
            ),
            None,
        )
        if existing:
            if int(item.get("memory_mb") or 0) > int(existing.get("memory_mb") or 0):
                existing["memory_mb"] = item["memory_mb"]
            if item.get("driver"):
                existing["driver"] = item["driver"]
            continue
        merged.append(dict(item))
    return merged


def _torch_version() -> str:
    try:
        return importlib.metadata.version("torch")
    except importlib.metadata.PackageNotFoundError:
        return ""


def _ffmpeg_encoders() -> set[str]:
    if not config.FFMPEG:
        return set()
    result = _run([config.FFMPEG, "-hide_banner", "-encoders"], timeout=10)
    if not result or result.returncode != 0:
        return set()
    return {name for name in _VIDEO_ARGS if re.search(rf"\b{re.escape(name)}\b", result.stdout)}


def _test_encoder(name: str) -> tuple[bool, str]:
    if not config.FFMPEG:
        return False, "未安装 FFmpeg"
    result = _run(
        [
            config.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:r=10",
            "-frames:v",
            "1",
            "-c:v",
            name,
            "-f",
            "null",
            "-",
        ],
        timeout=12,
    )
    if result and result.returncode == 0:
        return True, "硬件编码自检通过"
    output = result.stdout if result else "无法启动编码器"
    driver = re.search(r"minimum required .*? driver.*?(\d[\d.]*)", output, re.I)
    if driver:
        return False, f"显卡驱动过旧，需要 {driver.group(1)} 或更高版本"
    first = next((line.strip() for line in output.splitlines() if line.strip()), "硬件编码自检失败")
    return False, first[:160]


def _candidate_encoders(vendors: set[str], available: set[str]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    if "nvidia" in vendors and "h264_nvenc" in available:
        candidates.append(("h264_nvenc", "NVIDIA NVENC"))
    if "amd" in vendors and "h264_amf" in available:
        candidates.append(("h264_amf", "AMD AMF"))
    if "intel" in vendors and "h264_qsv" in available:
        candidates.append(("h264_qsv", "Intel Quick Sync"))
    if "apple" in vendors and "h264_videotoolbox" in available:
        candidates.append(("h264_videotoolbox", "Apple VideoToolbox"))
    return candidates


@lru_cache(maxsize=1)
def acceleration_report() -> dict[str, object]:
    devices = _dedupe_devices([*_nvidia_devices(), *_windows_devices(), *_linux_devices()])
    vendors = {str(item["vendor"]) for item in devices}
    encoders = _ffmpeg_encoders()

    video_encoder = ""
    video_backend = "CPU"
    video_reason = "未发现可用的硬件 H.264 编码器"
    for encoder, label in _candidate_encoders(vendors, encoders):
        ready, reason = _test_encoder(encoder)
        if ready:
            video_encoder, video_backend, video_reason = encoder, label, reason
            break
        if video_reason.startswith("未发现"):
            video_reason = reason

    torch_version = _torch_version()
    max_memory = max((int(item.get("memory_mb") or 0) for item in devices), default=0)
    model_backend = "CPU"
    model_ready = False
    if "nvidia" in vendors and torch_version and "+cpu" not in torch_version.lower() and max_memory >= 4096:
        model_backend = "CUDA"
        model_ready = True
        model_reason = "CUDA PyTorch 与显存条件满足"
    elif "amd" in vendors and "rocm" in torch_version.lower() and max_memory >= 4096:
        model_backend = "ROCm"
        model_ready = True
        model_reason = "ROCm PyTorch 与显存条件满足"
    elif not torch_version:
        model_reason = "未安装 PyTorch"
    elif "+cpu" in torch_version.lower():
        model_reason = f"当前 PyTorch {torch_version} 是 CPU 版"
    elif max_memory and max_memory < 4096:
        model_reason = f"最大显存 {max_memory}MB，口型模型安全阈值为 4096MB"
    elif "amd" in vendors and os.name == "nt":
        model_reason = "Windows 下 SadTalker 不支持 AMD ROCm；可使用 AMF 加速视频编码"
    else:
        model_reason = "SadTalker 未发现兼容的 CUDA 或 ROCm 运行环境"

    gpu_ready = bool(video_encoder or model_ready)
    if not devices:
        summary = "未发现独立 GPU"
    elif gpu_ready:
        active = []
        if model_ready:
            active.append(f"口型 {model_backend}")
        if video_encoder:
            active.append(f"编码 {video_backend}")
        summary = "GPU 可用：" + "、".join(active)
    else:
        summary = f"已发现 {devices[0]['name']}，当前仅可使用 CPU"

    return {
        "devices": devices,
        "torch_version": torch_version,
        "model": {"ready": model_ready, "backend": model_backend, "reason": model_reason},
        "video": {
            "ready": bool(video_encoder),
            "backend": video_backend,
            "encoder": video_encoder,
            "reason": video_reason,
        },
        "gpu_ready": gpu_ready,
        "summary": summary,
        "options": [
            {"id": "auto", "label": "自动选择（推荐）", "ready": True},
            {"id": "cpu", "label": "仅使用 CPU", "ready": True},
            {
                "id": "gpu",
                "label": "GPU 优先" if gpu_ready else "GPU 优先（当前不可用）",
                "ready": gpu_ready,
            },
        ],
        "current": normalize_mode(None),
    }


def video_encode_args(mode: object) -> tuple[list[str], bool]:
    if normalize_mode(mode) == "cpu":
        return list(CPU_VIDEO_ARGS), False
    encoder = str(acceleration_report()["video"]["encoder"])  # type: ignore[index]
    if encoder in _VIDEO_ARGS:
        return list(_VIDEO_ARGS[encoder]), True
    return list(CPU_VIDEO_ARGS), False


def model_gpu_enabled(mode: object) -> bool:
    return normalize_mode(mode) != "cpu" and bool(acceleration_report()["model"]["ready"])  # type: ignore[index]


def selection_label(mode: object) -> str:
    selected = normalize_mode(mode)
    report = acceleration_report()
    if selected == "cpu":
        return "CPU"
    if not report["gpu_ready"]:
        return "自动回退 CPU" if selected == "auto" else "GPU 不可用，回退 CPU"
    active: list[str] = []
    if model_gpu_enabled(selected):
        active.append(str(report["model"]["backend"]))  # type: ignore[index]
    if report["video"]["ready"]:  # type: ignore[index]
        active.append(str(report["video"]["backend"]))  # type: ignore[index]
    return " + ".join(active) if active else "CPU"


__all__ = [
    "CPU_VIDEO_ARGS",
    "acceleration_report",
    "model_gpu_enabled",
    "normalize_mode",
    "selection_label",
    "video_encode_args",
]
