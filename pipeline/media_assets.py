"""Local stock, sticker and music library used by the automatic editor.

The module deliberately owns path validation.  API handlers and the editing
pipeline can therefore pass user supplied asset ids here without ever opening
files outside ``assets``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from . import config


ASSET_DIRS: dict[str, Path] = {
    "stock": config.STOCK_DIR,
    "stickers": config.STICKER_DIR,
    "bgm": config.BGM_DIR,
}

ASSET_SUFFIXES: dict[str, set[str]] = {
    "stock": config.IMAGE_SUFFIXES | config.VIDEO_SUFFIXES,
    "stickers": config.STICKER_SUFFIXES,
    "bgm": config.AUDIO_SUFFIXES,
}

_KIND_ALIASES = {"sticker": "stickers", "music": "bgm", "audio": "bgm"}


def _kind(value: object) -> str:
    name = str(value or "").strip().lower()
    return _KIND_ALIASES.get(name, name)


def ensure_asset_dirs() -> None:
    """Create the three library folders on first run."""
    for directory in ASSET_DIRS.values():
        directory.mkdir(parents=True, exist_ok=True)


def _media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in config.AUDIO_SUFFIXES:
        return "audio"
    if suffix in config.VIDEO_SUFFIXES or suffix in {".gif"}:
        return "video"
    return "image"


def list_assets(kind: str) -> list[dict[str, object]]:
    """List usable files of one kind with stable relative ids."""
    kind = _kind(kind)
    if kind not in ASSET_DIRS:
        return []
    ensure_asset_dirs()
    base = ASSET_DIRS[kind]
    suffixes = ASSET_SUFFIXES[kind]
    items: list[dict[str, object]] = []
    for path in sorted(base.rglob("*"), key=lambda item: item.as_posix().lower()):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        relative = path.relative_to(base).as_posix()
        items.append(
            {
                "id": relative,
                "kind": kind,
                "label": path.stem.replace("_", " ").replace("-", " "),
                "filename": path.name,
                "type": _media_type(path),
                "media_type": _media_type(path),
                "url": f"/assets/{kind}/{quote(relative, safe='/')}",
                "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
            }
        )
    return items


def asset_catalog() -> dict[str, list[dict[str, object]]]:
    """Return the complete local library, ready for a JSON API response."""
    return {kind: list_assets(kind) for kind in ("stock", "stickers", "bgm")}


def list_media_assets() -> dict[str, list[dict[str, object]]]:
    """Public name used by the HTTP layer."""
    return asset_catalog()


def resolve_asset(value: object, kind: str) -> Path | None:
    """Resolve a library id while rejecting absolute paths and traversal."""
    kind = _kind(kind)
    if kind not in ASSET_DIRS:
        return None
    if isinstance(value, dict):
        value = value.get("id") or value.get("asset") or value.get("name")
    name = str(value or "").strip().replace("\\", "/")
    if not name:
        return None
    candidate_name = Path(name)
    if candidate_name.is_absolute() or ".." in candidate_name.parts:
        return None
    base = ASSET_DIRS[kind].resolve()
    candidate = (base / candidate_name).resolve()
    if candidate.parent != base and base not in candidate.parents:
        return None
    if not candidate.is_file() or candidate.suffix.lower() not in ASSET_SUFFIXES[kind]:
        return None
    return candidate


def resolve_media_asset(kind: str, asset_id: object) -> Path | None:
    """Public safe resolver used by the HTTP layer."""
    return resolve_asset(asset_id, kind)


def _as_items(value: object) -> Iterable[object]:
    if isinstance(value, (list, tuple)):
        return value
    if isinstance(value, str) and "," in value:
        return [item.strip() for item in value.split(",")]
    if value:
        return [value]
    return []


def resolve_assets(value: object, kind: str, limit: int = 12) -> list[Path]:
    """Resolve a scalar or list of asset ids, preserving order and uniqueness."""
    resolved: list[Path] = []
    seen: set[Path] = set()
    for item in _as_items(value):
        path = resolve_asset(item, kind)
        if path is None or path in seen:
            continue
        seen.add(path)
        resolved.append(path)
        if len(resolved) >= max(1, limit):
            break
    return resolved


def resolve_sticker_specs(value: object, limit: int = 8) -> list[dict[str, object]]:
    """Resolve stickers and keep optional timing/placement hints."""
    specs: list[dict[str, object]] = []
    for item in _as_items(value):
        path = resolve_asset(item, "stickers")
        if path is None:
            continue
        spec: dict[str, object] = {"path": path}
        if isinstance(item, dict):
            for key in ("start", "duration", "width", "position"):
                if key in item:
                    spec[key] = item[key]
        specs.append(spec)
        if len(specs) >= max(1, limit):
            break
    return specs


__all__ = [
    "asset_catalog",
    "ensure_asset_dirs",
    "list_assets",
    "list_media_assets",
    "resolve_asset",
    "resolve_assets",
    "resolve_media_asset",
    "resolve_sticker_specs",
]
