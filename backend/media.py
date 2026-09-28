"""Media library: import, probe (ffprobe), sample assets."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = ROOT / "media"
PROJECTS_DIR = ROOT / "projects"
EXPORTS_DIR = ROOT / "exports"
for d in (MEDIA_DIR, PROJECTS_DIR, EXPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

LIBRARY_FILE = MEDIA_DIR / "library.json"

MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def ffprobe(path):
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    return json.loads(proc.stdout)


def probe_media(path):
    info = ffprobe(path)
    duration = float(info.get("format", {}).get("duration", 0.0))
    width = height = 0
    has_audio = False
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video" and width == 0:
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))
        if stream.get("codec_type") == "audio":
            has_audio = True
    return {"duration": duration, "width": width, "height": height,
            "has_audio": has_audio}


def load_library():
    if not LIBRARY_FILE.exists():
        return []
    try:
        return json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_library(items):
    tmp = LIBRARY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(LIBRARY_FILE)


def media_path(media_id):
    for item in load_library():
        if item["id"] == media_id:
            return MEDIA_DIR / item["filename"]
    return None


def describe(item):
    path = MEDIA_DIR / item["filename"]
    return {**item, "missing": not path.exists()}


def import_file(src_path, original_name):
    ext = Path(original_name).suffix.lower()
    if ext not in MEDIA_EXTS:
        raise ValueError(f"unsupported file type: {ext}")
    media_id = uuid.uuid4().hex[:12]
    filename = f"{media_id}{ext}"
    dest = MEDIA_DIR / filename
    # Copy the upload; the user's original source is never touched.
    with open(src_path, "rb") as src, open(dest, "wb") as out:
        while True:
            chunk = src.read(4 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    try:
        meta = probe_media(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    item = {
        "id": media_id,
        "filename": filename,
        "original_name": original_name,
        "created_at": time.time(),
        **meta,
    }
    library = load_library()
    library.append(item)
    save_library(library)
    return describe(item)


SAMPLE_SPECS = [
    ("sample_red.mp4",   "Red",   "red",   "0xff5533", 440, 4.0),
    ("sample_green.mp4", "Green", "green", "0x33cc55", 550, 4.0),
    ("sample_blue.mp4",  "Blue",  "blue",  "0x3377ff", 660, 4.0),
]


def ensure_samples():
    """Create the bundled short sample clips if missing from the library."""
    library = load_library()
    known = {item["filename"] for item in library}
    for filename, label, _key, rgb, freq, dur in SAMPLE_SPECS:
        dest = MEDIA_DIR / filename
        if filename in known and dest.exists():
            continue
        if not dest.exists():
            draw = (
                f"drawtext=text='{label} %{{pts\\:hms}}':fontsize=72:"
                f"fontcolor=white:x=80:y=300:box=1:boxcolor=black@0.6"
            )
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i",
                f"color=c={rgb}:s=1280x720:d={dur}:r=30",
                "-f", "lavfi", "-i",
                f"sine=frequency={freq}:duration={dur}",
                "-vf", draw,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                str(dest),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"sample render failed: {proc.stderr[-800:]}")
        meta = probe_media(dest)
        item = {
            "id": f"sample-{filename.split('.')[0].split('_')[1]}",
            "filename": filename,
            "original_name": f"{label}.mp4",
            "created_at": 0.0,
            "builtin": True,
            **meta,
        }
        library = [it for it in library if it["filename"] != filename]
        library.insert(0, item)
    save_library(library)
    return library
