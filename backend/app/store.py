"""Atomic project/media/export persistence helpers.

Project saves write to a temp file and then atomically replace the real
file, so an interrupted save or a killed process always leaves either the
previous complete project or the new complete project -- never a half
written JSON the user cannot reopen.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import MEDIA_DIR, PROJECTS_DIR
from .models import Project


def project_path(project_id: str) -> Path:
    if not project_id.replace("-", "").isalnum():
        raise ValueError("invalid project id")
    return PROJECTS_DIR / f"{project_id}.json"


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def save_project(project: Project) -> None:
    atomic_write_json(project_path(project.id), project.model_dump())


def load_project(project_id: str) -> Project | None:
    path = project_path(project_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return Project.model_validate(json.load(handle))


def list_projects() -> list[Project]:
    projects = []
    for path in sorted(PROJECTS_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                projects.append(Project.model_validate(json.load(handle)))
        except (OSError, ValueError):
            continue
    projects.sort(key=lambda item: item.updated_at, reverse=True)
    return projects


def media_path(media_id: str) -> Path:
    return MEDIA_DIR / media_id


def media_exists(media_id: str) -> bool:
    return media_path(media_id).exists()
