from __future__ import annotations

import re
from pathlib import Path


_END_PUNCTUATION = "。！？!?；;，,：:\n"


def normalize_script(text: str) -> str:
    """Normalize user text for speech while keeping natural punctuation."""
    text = text.replace("【", "").replace("】", "")
    text = re.sub(r"[\t\r ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_caption_chunks(text: str, max_chars: int = 11) -> list[str]:
    """Split Chinese-first copy into short, readable subtitle chunks."""
    text = normalize_script(text)
    if not text:
        return []

    chunks: list[str] = []
    current: list[str] = []
    visible = 0

    def flush() -> None:
        nonlocal current, visible
        value = "".join(current).strip()
        if value:
            chunks.append(value)
        current = []
        visible = 0

    for char in text:
        if char == "\n":
            flush()
            continue
        current.append(char)
        if not char.isspace() and char not in _END_PUNCTUATION:
            visible += 1
        if char in _END_PUNCTUATION or visible >= max_chars:
            flush()
    flush()
    return chunks


def _speech_weight(text: str) -> float:
    weight = 0.0
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            weight += 1.0
        elif char.isalnum():
            weight += 0.55
        elif char in "，,：:":
            weight += 0.25
        elif char in "。！？!?；;":
            weight += 0.45
    return max(weight, 0.5)


def subtitle_timeline(text: str, duration: float) -> list[tuple[float, float, str]]:
    chunks = split_caption_chunks(text)
    if not chunks or duration <= 0:
        return []
    weights = [_speech_weight(chunk) for chunk in chunks]
    total = sum(weights)
    cursor = 0.0
    result: list[tuple[float, float, str]] = []
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        end = duration if index == len(chunks) - 1 else cursor + duration * weight / total
        result.append((cursor, max(cursor + 0.08, end), chunk))
        cursor = end
    return result


def ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def write_animated_ass(text: str, duration: float, destination: Path) -> Path:
    """Create large pop-in subtitles for a 1080x1920 vertical video."""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,Microsoft YaHei,76,&H00FFFFFF,&H0000D7FF,&H00131313,&H80000000,-1,0,0,0,100,100,2,0,1,7,3,2,70,70,190,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for index, (start, end, chunk) in enumerate(subtitle_timeline(text, duration)):
        colour = "&H0000D7FF&" if index % 3 == 1 else "&H00FFFFFF&"
        tags = (
            r"{\an2\c" + colour
            + r"\fscx72\fscy72\t(0,140,\fscx114\fscy114)"
            + r"\t(140,300,\fscx100\fscy100)\fad(60,90)}"
        )
        lines.append(
            "Dialogue: 0,"
            f"{ass_timestamp(start)},{ass_timestamp(end)},Pop,,0,0,0,,{tags}{escape_ass(chunk)}\n"
        )
    destination.write_text("".join(lines), encoding="utf-8-sig")
    return destination

