"""HyperFrames late-stage packaging: kinetic captions, title card, BGM under shots."""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import config
from .proc import WorkflowError, probe_duration
from .providers import hyperframes_bin
from .text import subtitle_timeline


TRANSITION_CSS = {
    "fade": "opacity",
    "dissolve": "opacity",
    "slideleft": "slide-left",
    "slideup": "slide-up",
    "circleopen": "opacity",
    "smoothleft": "slide-left",
}


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _caption_chunks(script: str, duration: float) -> list[tuple[float, float, str]]:
    """Return (local_start, local_duration, text) caption spans."""
    chunks: list[tuple[float, float, str]] = []
    for start, end, text in subtitle_timeline(script, duration):
        chunks.append((start, max(0.4, end - start), text[:28]))
    return chunks


def build_composition(
    work_dir: Path,
    shots: list[dict[str, object]],
    *,
    title: str = "",
    captions: bool = True,
    transition: float = 0.6,
    transition_style: str = "fade",
    bgm: Path | None = None,
    bgm_volume: float = 0.16,
) -> Path:
    """Write a 1080x1920 HyperFrames HTML composition from talking-head shots."""
    work_dir.mkdir(parents=True, exist_ok=True)
    media = work_dir / "media"
    media.mkdir(parents=True, exist_ok=True)

    blend = max(0.0, min(1.2, float(transition)))
    style = TRANSITION_CSS.get(transition_style, "opacity")
    timeline: list[dict[str, object]] = []
    cursor = 0.0

    if title.strip():
        card_dur = 2.4
        timeline.append(
            {
                "kind": "card",
                "start": cursor,
                "duration": card_dur,
                "text": title.strip()[:24],
            }
        )
        cursor += card_dur - (blend if shots else 0.0)

    for index, shot in enumerate(shots):
        video = Path(str(shot["video"]))
        audio = Path(str(shot["audio"]))
        script = str(shot.get("text") or "")
        duration = float(shot.get("duration") or probe_duration(video))
        video_name = f"shot{index + 1}{video.suffix.lower() or '.mp4'}"
        audio_name = f"shot{index + 1}{audio.suffix.lower() or '.wav'}"
        shutil.copy2(video, media / video_name)
        shutil.copy2(audio, media / audio_name)
        timeline.append(
            {
                "kind": "shot",
                "start": cursor,
                "duration": duration,
                "video": f"media/{video_name}",
                "audio": f"media/{audio_name}",
                "text": script,
                "captions": _caption_chunks(script, duration) if captions else [],
            }
        )
        is_last = index == len(shots) - 1
        cursor += duration - (0.0 if is_last else blend)

    total = max(0.5, cursor)
    bgm_src = ""
    if bgm is not None and bgm.is_file():
        bgm_name = f"bgm{bgm.suffix.lower()}"
        shutil.copy2(bgm, media / bgm_name)
        bgm_src = f"media/{bgm_name}"

    (work_dir / "plan.json").write_text(
        json.dumps({"total": total, "blend": blend, "timeline": timeline}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    layers: list[str] = [
        '<div id="bg" class="bg clip" data-start="0" '
        f'data-duration="{total:.3f}" data-track-index="0"></div>'
    ]
    track = 1
    elem = 0

    for item in timeline:
        start = float(item["start"])
        duration = float(item["duration"])
        if item["kind"] == "card":
            elem += 1
            layers.append(
                f'<div id="card-{elem}" class="card clip" data-start="{start:.3f}" '
                f'data-duration="{duration:.3f}" data-track-index="{track}">'
                f'<div class="card-title">{_esc(str(item["text"]))}</div></div>'
            )
            track += 1
            continue

        motion = f"hx-{style}"
        elem += 1
        layers.append(
            f'<video id="shot-video-{elem}" class="clip talking {motion}" data-start="{start:.3f}" '
            f'data-duration="{duration:.3f}" data-track-index="{track}" '
            f'src="{_esc(str(item["video"]))}" muted playsinline></video>'
        )
        track += 1
        elem += 1
        layers.append(
            f'<audio id="shot-audio-{elem}" data-start="{start:.3f}" data-duration="{duration:.3f}" '
            f'data-track-index="{track}" src="{_esc(str(item["audio"]))}"></audio>'
        )
        track += 1
        for cap_start, cap_dur, text in item.get("captions") or []:
            abs_start = start + float(cap_start)
            elem += 1
            layers.append(
                f'<div id="caption-{elem}" class="caption clip" data-start="{abs_start:.3f}" '
                f'data-duration="{float(cap_dur):.3f}" data-track-index="{track}">'
                f'{_esc(str(text))}</div>'
            )
            track += 1

    if bgm_src:
        vol = max(0.0, min(0.6, float(bgm_volume)))
        elem += 1
        layers.append(
            f'<audio id="bgm-{elem}" data-start="0" data-duration="{total:.3f}" '
            f'data-track-index="{track}" data-volume="{vol:.3f}" src="{_esc(bgm_src)}"></audio>'
        )

    composition = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>douyin hyperframes pack</title>
  <style>
    @font-face {{
      font-family: "Microsoft YaHei";
      src: local("Microsoft YaHei"), local("微软雅黑");
      font-weight: 400 900;
    }}
    html, body {{ margin: 0; background: #111; }}
    #stage {{
      position: relative; width: 1080px; height: 1920px; overflow: hidden;
      background: #161613; font-family: "Microsoft YaHei", sans-serif;
    }}
    .bg {{ position: absolute; inset: 0; background:
      radial-gradient(circle at 50% 18%, rgba(242,107,56,.28), transparent 42%),
      linear-gradient(160deg, #1c1b18, #0f0f0d); }}
    .talking, .card {{
      position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover;
    }}
    .card {{
      display: grid; place-items: center;
      background:
        radial-gradient(circle at 50% 40%, rgba(244,196,48,.18), transparent 45%),
        linear-gradient(160deg, #22201a, #12110f);
    }}
    .card-title {{
      max-width: 860px; padding: 0 48px; text-align: center; color: #fff;
      font-size: 92px; font-weight: 900; line-height: 1.25; letter-spacing: -.04em;
      text-shadow: 0 8px 24px rgba(0,0,0,.45);
    }}
    .caption {{
      position: absolute; left: 70px; right: 70px; bottom: 210px;
      text-align: center; color: #fff; font-size: 64px; font-weight: 800;
      line-height: 1.35; letter-spacing: -.03em;
      text-shadow: 0 4px 0 rgba(0,0,0,.55), 0 18px 40px rgba(0,0,0,.35);
    }}
    .hx-opacity {{ animation: hxFade {blend:.2f}s linear both; }}
    .hx-slide-left {{ animation: hxSlideLeft {blend:.2f}s ease both; }}
    .hx-slide-up {{ animation: hxSlideUp {blend:.2f}s ease both; }}
    @keyframes hxFade {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
    @keyframes hxSlideLeft {{ from {{ opacity: 0; transform: translateX(8%); }} to {{ opacity: 1; transform: none; }} }}
    @keyframes hxSlideUp {{ from {{ opacity: 0; transform: translateY(8%); }} to {{ opacity: 1; transform: none; }} }}
  </style>
</head>
<body>
  <div id="stage" data-composition-id="douyin" data-no-timeline data-start="0"
       data-width="1080" data-height="1920" data-duration="{total:.3f}" data-fps="25">
    {chr(10).join(layers)}
  </div>
</body>
</html>
"""
    comp = work_dir / "comp.html"
    comp.write_text(composition, encoding="utf-8")
    return comp


def _chrome_path() -> str | None:
    candidates = [
        Path(os.environ.get("HYPERFRAMES_BROWSER_PATH", "")),
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate)
    return None


def render_composition(comp_html: Path, output: Path) -> Path:
    launcher = hyperframes_bin()
    if not launcher:
        raise WorkflowError("未找到 npx，无法使用 HyperFrames。请先安装 Node.js 22+。")
    output.parent.mkdir(parents=True, exist_ok=True)
    work_dir = comp_html.parent
    relative_out = Path(os.path.relpath(output, work_dir))

    env = os.environ.copy()
    if config.FFMPEG:
        env["PATH"] = str(Path(config.FFMPEG).resolve().parent) + os.pathsep + env.get("PATH", "")
    chrome = _chrome_path()
    if chrome:
        env["HYPERFRAMES_BROWSER_PATH"] = chrome
    env.setdefault("CI", "1")

    command = [
        *launcher,
        "--yes",
        "hyperframes",
        "render",
        ".",
        "-c",
        comp_html.name,
        "-o",
        str(relative_out).replace("\\", "/"),
        "-f",
        "25",
        "-q",
        "draft",
        "--resolution",
        "portrait",
        "--low-memory-mode",
        "-w",
        "1",
    ]
    try:
        process = subprocess.run(
            command,
            cwd=str(work_dir),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
            timeout=1200,
        )
    except subprocess.TimeoutExpired as exc:
        text = (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else ""
        raise WorkflowError(f"HyperFrames 渲染成片超时（>1200s）\n{text}") from exc

    log = (process.stdout or "")[-12000:]
    if process.returncode != 0:
        raise WorkflowError(f"HyperFrames 渲染成片失败（退出码 {process.returncode}）\n{log}")

    rendered = output if output.exists() else work_dir / relative_out
    if not rendered.exists():
        fallback = work_dir / "renders" / f"{comp_html.stem}.mp4"
        if fallback.exists():
            fallback.replace(output)
            rendered = output
    if not rendered.exists():
        raise WorkflowError(f"HyperFrames 已结束，但没有找到输出视频。\n{log}")
    if rendered != output:
        rendered.replace(output)
    return output


def package_with_hyperframes(
    shots: list[tuple[Path, Path, str]],
    output: Path,
    *,
    title: str = "",
    captions: bool = True,
    transition: float = 0.6,
    transition_style: str = "fade",
    bgm: Path | None = None,
    bgm_volume: float = 0.16,
) -> Path:
    work = output.parent / "hyperframes"
    prepared = [
        {
            "video": video,
            "audio": audio,
            "text": text,
            "duration": probe_duration(video),
        }
        for video, audio, text in shots
    ]
    comp = build_composition(
        work,
        prepared,
        title=title,
        captions=captions,
        transition=transition,
        transition_style=transition_style,
        bgm=bgm,
        bgm_volume=bgm_volume,
    )
    return render_composition(comp, output)
