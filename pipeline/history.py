"""Persistent generation history backed by outputs/history.json."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from . import config


MAX_ENTRIES = 200
_lock = threading.Lock()


def _read() -> list[dict[str, Any]]:
    path = config.HISTORY_FILE
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _write(entries: list[dict[str, Any]]) -> None:
    path = config.HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再替换，避免中断时留下损坏的 JSON。
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(path)


def _exists(entry: dict[str, Any]) -> bool:
    # Failed jobs have no result file but are still useful diagnostic history.
    if entry.get("status") != "complete":
        return True
    relative = str(entry.get("file") or "")
    if not relative:
        return False
    candidate = (config.OUTPUT_DIR / relative).resolve()
    if config.OUTPUT_DIR.resolve() not in candidate.parents:
        return False
    return candidate.is_file()


def record(
    job_id: str,
    *,
    title: str,
    status: str,
    duration: float,
    elapsed: float,
    voice: str,
    shots: int,
    timeline: list[dict[str, Any]] | None = None,
    file: str = "",
    poster: str = "",
    error: str = "",
    created_at: float | None = None,
) -> None:
    """Append or replace one job's history entry."""
    entry = {
        "id": job_id,
        "title": title.strip()[:40] or "未命名成片",
        "status": status,
        "duration": round(float(duration), 2),
        "elapsed": round(float(elapsed), 2),
        "voice": voice,
        "shots": int(shots),
        "timeline": timeline or [],
        "file": file,
        "poster": poster,
        "error": error[:400],
        "created_at": created_at or time.time(),
    }
    with _lock:
        entries = [item for item in _read() if item.get("id") != job_id]
        entries.append(entry)
        entries.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        _write(entries[:MAX_ENTRIES])


def listing(limit: int = 50) -> list[dict[str, Any]]:
    """Newest first, dropping entries whose video has been deleted from disk."""
    entries = _read()
    alive = [entry for entry in entries if _exists(entry)]
    if len(alive) != len(entries):
        with _lock:
            _write(alive)
    alive.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    return alive[: max(1, limit)]


def get(job_id: str) -> dict[str, Any] | None:
    for entry in _read():
        if entry.get("id") == job_id:
            return entry
    return None


def remove(job_id: str) -> bool:
    with _lock:
        entries = _read()
        remaining = [item for item in entries if item.get("id") != job_id]
        if len(remaining) == len(entries):
            return False
        _write(remaining)
    return True


def clear() -> int:
    with _lock:
        count = len(_read())
        _write([])
    return count
