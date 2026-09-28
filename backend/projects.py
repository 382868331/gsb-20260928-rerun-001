"""Project persistence (atomic JSON writes).

Projects reference media by id plus trim ranges; media bytes are never
embedded or overwritten. Missing media files are flagged on load so the
rest of the project stays editable.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from media import PROJECTS_DIR, media_path
from timeline import Timeline


def _safe(name: str) -> str:
    stem = Path(name).stem
    cleaned = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", stem)[:80]
    return cleaned or "project"


def project_path(name: str) -> Path:
    return PROJECTS_DIR / f"{_safe(name)}.json"


def list_projects():
    out = []
    for p in sorted(PROJECTS_DIR.glob("*.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "name": p.stem,
            "updated_at": data.get("updated_at", p.stat().st_mtime),
            "duration": data.get("duration", 0),
            "clip_count": len(data.get("timeline", {}).get("clips", [])),
        })
    return out


def load_project(name: str):
    path = project_path(name)
    if not path.exists():
        raise FileNotFoundError(name)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("timeline", {"clips": []})
    missing = []
    for clip in data["timeline"]["clips"]:
        mp = media_path(clip["media_id"])
        if mp is None or not Path(mp).exists():
            missing.append(clip["media_id"])
    data["missing_media"] = missing
    return data


def save_project(name: str, timeline: Timeline, extra=None):
    path = project_path(name)
    data = {
        "name": _safe(name),
        "updated_at": time.time(),
        "duration": round(timeline.duration, 4),
        "timeline": timeline.to_dict(),
    }
    if extra:
        data.update(extra)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)
    return data
