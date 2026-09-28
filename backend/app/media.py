"""Media ingestion and ffprobe inspection."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path

from .config import MEDIA_DIR, SAMPLES_DIR
from .models import MediaInfo

MEDIA_INDEX_FILE = MEDIA_DIR / "index.json"


def _run_ffprobe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def probe(path: Path) -> MediaInfo:
    data = _run_ffprobe(path)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    if video is None:
        raise ValueError("文件中没有视频轨道")
    duration = float(data["format"]["duration"])
    return MediaInfo(
        id=path.name,
        filename=path.name,
        duration=duration,
        width=int(video["width"]) if video.get("width") else None,
        height=int(video["height"]) if video.get("height") else None,
        has_audio=has_audio,
        is_sample=False,
    )


def load_index() -> dict[str, MediaInfo]:
    if not MEDIA_INDEX_FILE.exists():
        return {}
    try:
        raw = json.loads(MEDIA_INDEX_FILE.read_text(encoding="utf-8"))
        return {key: MediaInfo.model_validate(value) for key, value in raw.items()}
    except (OSError, ValueError):
        return {}


def save_index(index: dict[str, MediaInfo]) -> None:
    MEDIA_DIR.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: value.model_dump() for key, value in index.items()}
    from .store import atomic_write_json

    atomic_write_json(MEDIA_INDEX_FILE, payload)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_file(source: Path, original_name: str, is_sample: bool = False) -> MediaInfo:
    """Copy media into the data root; the original file is never modified."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    digest = _sha256(source)
    index = load_index()
    for existing in index.values():
        if getattr(existing, "sha256", None) == digest:
            return existing
    suffix = source.suffix or Path(original_name).suffix or ".mp4"
    media_id = f"{uuid.uuid4().hex}{suffix}"
    dest = MEDIA_DIR / media_id
    if source.resolve() != dest.resolve():
        shutil.copy2(source, dest)
    info = probe(dest)
    info.is_sample = is_sample
    info.filename = original_name if is_sample else Path(original_name).name
    info.sha256 = digest
    index[media_id] = info
    save_index(index)
    return info


def register_sample(sample_id: str) -> MediaInfo | None:
    source = SAMPLES_DIR / sample_id
    if not source.exists():
        return None
    return import_file(source, sample_id, is_sample=True)
