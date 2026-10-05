from __future__ import annotations

import asyncio
import io
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException, UploadFile  # noqa: E402

from app import config  # noqa: E402
from app import api as api_module  # noqa: E402
from app import media as media_module  # noqa: E402
from app import samples as samples_module  # noqa: E402
from app.models import ExportPayload, ProjectPayload  # noqa: E402
from app import store as store_module  # noqa: E402
from app import exporter as exporter_module  # noqa: E402
from app import packager as packager_module  # noqa: E402
from app.config import ensure_dirs  # noqa: E402
from app.samples import ensure_samples  # noqa: E402


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Response:
    def __init__(self, value, status_code=200):
        if isinstance(value, api_module.FileResponse):
            self.status_code = value.status_code
            self.content = Path(value.path).read_bytes()
            self.headers = {"content-type": value.media_type}
        else:
            self.status_code = status_code
            self._value = value
            self.headers = {}

    def json(self):
        return self._value


class DirectClient:
    """Call API route functions directly.  The sandbox blocks loopback
    sockets and this environment's httpx ASGI transport deadlocks; all
    route handlers here are ordinary sync functions (one async upload
    handler is driven through asyncio), so direct calls exercise the same
    validation/persistence/export logic.
    """

    def _call(self, func, **kwargs):
        try:
            result = func(**kwargs)
        except HTTPException as exc:
            raise HTTPError(exc.status_code, exc.detail)
        if hasattr(result, "model_dump"):
            return Response(result.model_dump())
        if isinstance(result, list):
            return Response(
                [item.model_dump() if hasattr(item, "model_dump") else item for item in result]
            )
        return Response(result)

    def get(self, path):
        if path == "/api/health":
            return self._call(api_module.health)
        if path == "/api/media":
            return self._call(api_module.list_media)
        if path == "/api/projects":
            return self._call(api_module.get_projects)
        m = re.fullmatch(r"/api/media/([^/]+)/stream", path)
        if m:
            return self._call(api_module.stream_media, media_id=m.group(1))
        m = re.fullmatch(r"/api/projects/([^/]+)/timeline", path)
        if m:
            return self._call(api_module.project_timeline, project_id=m.group(1))
        m = re.fullmatch(r"/api/projects/([^/]+)/exports", path)
        if m:
            return self._call(api_module.list_exports, project_id=m.group(1))
        m = re.fullmatch(r"/api/projects/([^/]+)", path)
        if m:
            return self._call(api_module.get_project, project_id=m.group(1))
        m = re.fullmatch(r"/api/packages/([^/]+)/download", path)
        if m:
            return self._call(api_module.download_package, job_id=m.group(1))
        m = re.fullmatch(r"/api/packages/([^/]+)", path)
        if m:
            return self._call(api_module.get_package, job_id=m.group(1))
        m = re.fullmatch(r"/api/exports/([^/]+)", path)
        if m:
            return self._call(api_module.get_export, job_id=m.group(1))
        m = re.fullmatch(r"/api/exports/([^/]+)/download", path)
        if m:
            return self._call(api_module.download_export, job_id=m.group(1))
        raise AssertionError(f"unknown GET {path}")

    def post(self, path, json=None, files=None):
        json_body = json
        if path == "/api/media" and files:
            file_tuple = files["file"]
            filename, data = file_tuple[0], file_tuple[1]
            upload = UploadFile(filename=filename, file=io.BytesIO(data))
            return self._upload(upload)
        if path == "/api/projects":
            payload = ProjectPayload.model_validate(json_body or {})
            return self._call(api_module.create_project, payload=payload)
        m = re.fullmatch(r"/api/projects/([^/]+)/exports", path)
        if m:
            payload = ExportPayload.model_validate(json_body or {})
            return self._call(
                api_module.start_export,
                project_id=m.group(1),
                payload=payload,
            )
        m = re.fullmatch(r"/api/exports/([^/]+)/cancel", path)
        if m:
            return self._call(api_module.cancel_export, job_id=m.group(1))
        m = re.fullmatch(r"/api/projects/([^/]+)/packages", path)
        if m:
            return self._call(api_module.start_package, project_id=m.group(1))
        m = re.fullmatch(r"/api/packages/([^/]+)/cancel", path)
        if m:
            return self._call(api_module.cancel_package, job_id=m.group(1))
        if path == "/api/projects/import" and files:
            file_tuple = files["file"]
            filename, data = file_tuple[0], file_tuple[1]
            upload = UploadFile(filename=filename, file=io.BytesIO(data))
            try:
                result = api_module.import_project(file=upload)
            except HTTPException as exc:
                raise HTTPError(exc.status_code, exc.detail)
            return Response(result)
        m = re.fullmatch(r"/api/projects/([^/]+)/replacements/preview", path)
        if m:
            from app.replacement import ReplacementRequest

            payload = ReplacementRequest.model_validate(json_body or {})
            return self._call(
                api_module.preview_replacement, project_id=m.group(1), payload=payload
            )
        m = re.fullmatch(r"/api/projects/([^/]+)/replacements/apply", path)
        if m:
            from app.replacement import ReplacementRequest

            payload = ReplacementRequest.model_validate(json_body or {})
            return self._call(
                api_module.apply_replacement_route, project_id=m.group(1), payload=payload
            )
        raise AssertionError(f"unknown POST {path}")

    async def _upload_async(self, upload):
        raise RuntimeError("upload is now synchronous")

    def _upload(self, upload):
        try:
            result = api_module.upload_media(file=upload)
        except HTTPException as exc:
            raise HTTPError(exc.status_code, exc.detail)
        return Response(result)

    def put(self, path, json=None):
        json_body = json
        m = re.fullmatch(r"/api/projects/([^/]+)", path)
        if m:
            payload = ProjectPayload.model_validate(json_body or {})
            return self._call(
                api_module.update_project, project_id=m.group(1), payload=payload
            )
        raise AssertionError(f"unknown PUT {path}")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    dirs = {
        "MEDIA_DIR": data_root / "media",
        "PROJECTS_DIR": data_root / "projects",
        "EXPORTS_DIR": data_root / "exports",
        "SAMPLES_DIR": data_root / "samples",
        "PACKAGES_DIR": data_root / "packages",
    }
    monkeypatch.setattr(config, "DATA_ROOT", data_root)
    for name, target in dirs.items():
        monkeypatch.setattr(config, name, target)
    monkeypatch.setattr(media_module, "MEDIA_DIR", dirs["MEDIA_DIR"])
    monkeypatch.setattr(media_module, "SAMPLES_DIR", dirs["SAMPLES_DIR"])
    monkeypatch.setattr(
        media_module, "MEDIA_INDEX_FILE", dirs["MEDIA_DIR"] / "index.json"
    )
    monkeypatch.setattr(samples_module, "SAMPLES_DIR", dirs["SAMPLES_DIR"])
    monkeypatch.setattr(store_module, "MEDIA_DIR", dirs["MEDIA_DIR"])
    monkeypatch.setattr(store_module, "PROJECTS_DIR", dirs["PROJECTS_DIR"])
    monkeypatch.setattr(exporter_module, "EXPORTS_DIR", dirs["EXPORTS_DIR"])
    monkeypatch.setattr(packager_module, "PACKAGES_DIR", dirs["PACKAGES_DIR"])
    monkeypatch.setattr(packager_module, "STAGING_DIR", data_root / "staging")
    config.ensure_dirs()
    samples = ensure_samples()
    yield DirectClient(), samples, config
