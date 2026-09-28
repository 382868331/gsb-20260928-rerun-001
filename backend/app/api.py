"""HTTP API for media, projects, timeline preview and exports."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import media as media_service
from .config import MEDIA_DIR, ensure_dirs
from .exporter import manager as export_manager
from .models import Project, ProjectPayload
from .samples import ensure_samples
from .store import list_projects, load_project, media_path, save_project
from .timeline import compute_timeline


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    ensure_samples()
    yield


app = FastAPI(title="视频剪辑网站 API", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/media")
def list_media() -> list[dict]:
    ensure_dirs()
    ensure_samples()
    values = list(media_service.load_index().values())
    # Samples first, then uploads by filename.
    values.sort(key=lambda item: (not item.is_sample, item.filename))
    return [item.model_dump() for item in values]


@app.post("/api/media")
def upload_media(file: UploadFile = File(...)) -> dict:
    ensure_dirs()
    suffix = Path(file.filename or "clip.mp4").suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{2,5}", suffix):
        suffix = ".mp4"
    staging = MEDIA_DIR.parent / f"upload_{uuid.uuid4().hex}{suffix}"
    try:
        content = file.file.read()
        staging.write_bytes(content)
        try:
            info = media_service.import_file(staging, file.filename or staging.name)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"无法读取视频：{exc}")
    finally:
        if staging.exists():
            staging.unlink(missing_ok=True)
    return info.model_dump()


@app.get("/api/media/{media_id}/stream")
def stream_media(media_id: str):
    path = media_path(media_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="素材缺失")
    # FileResponse supports HTTP Range, which the <video> preview needs
    # for seeking to arbitrary source offsets.
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/projects")
def get_projects() -> list[dict]:
    return [item.model_dump() for item in list_projects()]


@app.post("/api/projects")
def create_project(payload: ProjectPayload) -> dict:
    project = Project(
        id=uuid.uuid4().hex,
        name=payload.name,
        clips=payload.clips,
        version=1,
        created_at=_now(),
        updated_at=_now(),
    )
    save_project(project)
    return project.model_dump()


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    project = load_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="工程不存在")
    return project.model_dump()


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, payload: ProjectPayload) -> dict:
    project = load_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="工程不存在")
    project.name = payload.name
    project.clips = payload.clips
    project.version += 1
    project.updated_at = _now()
    save_project(project)
    return project.model_dump()


@app.get("/api/projects/{project_id}/timeline")
def project_timeline(project_id: str) -> dict:
    project = load_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="工程不存在")
    timeline = compute_timeline(project, media_service.load_index())
    return timeline.model_dump()


@app.post("/api/projects/{project_id}/exports")
def start_export(project_id: str) -> dict:
    if load_project(project_id) is None:
        raise HTTPException(status_code=404, detail="工程不存在")
    return export_manager.create(project_id).model_dump()


@app.get("/api/projects/{project_id}/exports")
def list_exports(project_id: str) -> list[dict]:
    return [item.model_dump() for item in export_manager.list_for_project(project_id)]


@app.get("/api/exports/{job_id}")
def get_export(job_id: str) -> dict:
    info = export_manager.get(job_id)
    if info is None:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    return info.model_dump()


@app.post("/api/exports/{job_id}/cancel")
def cancel_export(job_id: str) -> dict:
    if not export_manager.cancel(job_id):
        raise HTTPException(status_code=404, detail="导出任务不存在")
    return {"cancelled": True}


@app.get("/api/exports/{job_id}/download")
def download_export(job_id: str):
    path = export_manager.output_path(job_id)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="成片不存在或尚未完成")
    return FileResponse(
        path, media_type="video/mp4", filename=f"export-{job_id[:8]}.mp4"
    )
