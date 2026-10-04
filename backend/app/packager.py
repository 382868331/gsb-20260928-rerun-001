"""Project packaging (export to a portable file) and offline restore.

A package is a single zip file containing:

- project.json: the full project document (clips keep their original
  media ids; they are remapped on import);
- manifest.json: format marker + one entry per referenced media with
  its original filename and sha256;
- media/<sha256><suffix>: the actual media bytes, named by content
  hash so identical content is stored exactly once even when several
  clips (or several media entries) reference the same file.

Only media actually referenced by the project's clips is packed.

Import runs the reverse: every manifest entry is verified (member
present, sha256 matches) before anything is written; missing or
corrupt members are reported by name and the import aborts without
touching the library. Media is matched to the existing library by
content hash only -- a same-name file with different content is never
reused. Clip order, trims and speed plans are part of project.json and
survive the round trip unchanged, so a re-export renders the same
timeline as before packing.

Packing runs in a background thread and can be cancelled; a cancelled
job removes its partial archive and never leaves a downloadable file.
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import media as media_service
from .config import PACKAGES_DIR, STAGING_DIR
from .models import PackageInfo, Project
from .store import atomic_write_json, load_project, media_path, save_project

FORMAT_MARKER = "gsb-project-package"
FORMAT_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PackageCancelled(RuntimeError):
    pass


class PackageInvalid(RuntimeError):
    """Raised when an archive cannot be restored.

    "missing" and "corrupt" hold the offending media names so the
    caller can list them explicitly.
    """

    def __init__(
        self,
        message: str,
        missing: list[str] | None = None,
        corrupt: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing = missing or []
        self.corrupt = corrupt or []


@dataclass
class _Job:
    info: PackageInfo
    project_id: str
    cancel_event: threading.Event


class PackageManager:
    # Copied per chunk so cancellation is responsive even for large
    # media; tests shrink this to force multi-chunk copies.
    chunk_size = 1024 * 1024
    # Optional hook invoked after each chunk (used by tests to make
    # cancellation timing deterministic).
    chunk_hook: Callable[[], None] | None = None

    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    def create(self, project_id: str) -> PackageInfo:
        job_id = uuid.uuid4().hex
        info = PackageInfo(
            id=job_id,
            project_id=project_id,
            status="pending",
            created_at=_now(),
        )
        job = _Job(info=info, project_id=project_id, cancel_event=threading.Event())
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return info

    def get(self, job_id: str) -> PackageInfo | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.info.model_copy(deep=True) if job else None

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False
        job.cancel_event.set()
        return True

    def output_path(self, job_id: str) -> Path | None:
        info = self.get(job_id)
        if info is None or info.status != "done":
            return None
        return PACKAGES_DIR / (info.id + ".gsbpack")

    def _run(self, job: _Job) -> None:
        info = job.info
        PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = PACKAGES_DIR / (info.id + ".tmp")
        final_path = PACKAGES_DIR / (info.id + ".gsbpack")
        try:
            project = load_project(job.project_id)
            if project is None:
                raise RuntimeError("工程不存在")
            info.status = "running"
            self._build_archive(job, project, tmp_path)
            if job.cancel_event.is_set():
                raise PackageCancelled("打包已取消")
            tmp_path.replace(final_path)
            info.status = "done"
            info.progress = 1.0
            info.output_url = "/api/packages/" + info.id + "/download"
            info.finished_at = _now()
        except Exception as exc:  # noqa: BLE001 - report every failure to UI
            if job.cancel_event.is_set() or isinstance(exc, PackageCancelled):
                info.status = "cancelled"
            else:
                info.status = "failed"
                info.error = str(exc)
            info.finished_at = _now()
        finally:
            if info.status != "done":
                for path in (tmp_path, final_path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            atomic_write_json(PACKAGES_DIR / (info.id + ".json"), info.model_dump())

    def _build_archive(self, job: _Job, project: Project, tmp_path: Path) -> None:
        index = media_service.load_index()
        # Only media actually referenced by clips, in first-use order.
        referenced: list[str] = []
        for clip in project.clips:
            if clip.media_id not in referenced:
                referenced.append(clip.media_id)
        missing = [mid for mid in referenced if not media_path(mid).exists()]
        if missing:
            names = [index[mid].filename if mid in index else mid for mid in missing]
            raise RuntimeError("素材缺失，无法打包：" + ", ".join(names))

        # Group media ids by content hash so identical bytes are stored
        # once; prefer the recorded sha256, fall back to hashing.
        by_digest: dict[str, list[str]] = {}
        digests: dict[str, str] = {}
        for media_id in referenced:
            info = index.get(media_id)
            digest = info.sha256 if info and info.sha256 else None
            if not digest:
                digest = _sha256(media_path(media_id))
            digests[media_id] = digest
            by_digest.setdefault(digest, []).append(media_id)

        manifest_media = []
        for media_id in referenced:
            info = index.get(media_id)
            digest = digests[media_id]
            suffix = media_path(media_id).suffix or ".mp4"
            manifest_media.append(
                {
                    "media_id": media_id,
                    "filename": info.filename if info else media_id,
                    "sha256": digest,
                    "archived_as": "media/" + digest + suffix,
                }
            )
        manifest = {
            "format": FORMAT_MARKER,
            "version": FORMAT_VERSION,
            "project_name": project.name,
            "media": manifest_media,
        }

        total = len(by_digest) + 2
        done = 0
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
            archive.writestr(
                "project.json",
                json.dumps(project.model_dump(), ensure_ascii=False, indent=2),
            )
            done += 2
            job.info.progress = done / total
            for digest, media_ids in by_digest.items():
                if job.cancel_event.is_set():
                    raise PackageCancelled("打包已取消")
                source = media_path(media_ids[0])
                suffix = source.suffix or ".mp4"
                self._write_member(archive, source, "media/" + digest + suffix, job)
                done += 1
                job.info.progress = done / total

    def _write_member(
        self, archive: zipfile.ZipFile, source: Path, arcname: str, job: _Job
    ) -> None:
        with open(source, "rb") as handle, archive.open(arcname, "w") as dest:
            while True:
                if job.cancel_event.is_set():
                    raise PackageCancelled("打包已取消")
                chunk = handle.read(self.chunk_size)
                if not chunk:
                    break
                dest.write(chunk)
                if self.chunk_hook is not None:
                    self.chunk_hook()


manager = PackageManager()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_package(data: bytes) -> tuple[dict, dict, dict[str, bytes]]:
    """Parse and fully verify a package.

    Returns (project_dict, manifest, media_bytes). Raises
    PackageInvalid listing every missing or corrupt media member.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageInvalid("不是有效的工程包文件：" + str(exc)) from exc
    with archive:
        names = set(archive.namelist())
        for required in ("manifest.json", "project.json"):
            if required not in names:
                raise PackageInvalid("工程包缺少 " + required + "，文件不完整或已损坏")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            project_dict = json.loads(archive.read("project.json").decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise PackageInvalid("工程包元数据损坏：" + str(exc)) from exc
        if manifest.get("format") != FORMAT_MARKER:
            raise PackageInvalid("不是本应用导出的工程包（format 不匹配）")

        missing: list[str] = []
        corrupt: list[str] = []
        media_bytes: dict[str, bytes] = {}
        for entry in manifest.get("media", []):
            label = entry.get("filename") or entry.get("media_id") or "未知素材"
            arcname = entry.get("archived_as", "")
            if arcname not in names:
                missing.append(label)
                continue
            try:
                payload = archive.read(arcname)
            except zipfile.BadZipFile:
                corrupt.append(label)
                continue
            if _sha256_bytes(payload) != entry.get("sha256"):
                corrupt.append(label)
                continue
            media_bytes[entry["media_id"]] = payload
        if missing or corrupt:
            parts = []
            if missing:
                parts.append("缺失素材：" + ", ".join(missing))
            if corrupt:
                parts.append("损坏素材：" + ", ".join(corrupt))
            raise PackageInvalid("；".join(parts), missing=missing, corrupt=corrupt)
        return project_dict, manifest, media_bytes


def import_package(data: bytes) -> tuple[Project, dict]:
    """Restore a package into the current library and save the project.

    Media is matched strictly by content hash: identical content already
    in the library is reused, anything else is imported as a new media
    entry (a same-name file with different content therefore gets its
    own id and is never confused with the existing one).
    """
    project_dict, manifest, media_bytes = read_package(data)

    id_map: dict[str, str] = {}
    reused: list[str] = []
    imported: list[str] = []
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    known_digests = {
        item.sha256 for item in media_service.load_index().values() if item.sha256
    }
    for entry in manifest.get("media", []):
        old_id = entry["media_id"]
        filename = entry.get("filename") or old_id
        suffix = Path(entry.get("archived_as", "")).suffix or ".mp4"
        digest = entry.get("sha256")
        already_in_library = digest in known_digests
        staging = STAGING_DIR / ("import_" + uuid.uuid4().hex + suffix)
        try:
            staging.write_bytes(media_bytes[old_id])
            info = media_service.import_file(staging, filename)
        except Exception as exc:  # noqa: BLE001
            raise PackageInvalid("素材 " + filename + " 无法导入：" + str(exc)) from exc
        finally:
            staging.unlink(missing_ok=True)
        id_map[old_id] = info.id
        if info.sha256:
            known_digests.add(info.sha256)
        if already_in_library:
            reused.append(filename)
        else:
            imported.append(filename)

    clips = project_dict.get("clips", [])
    unknown = [
        clip.get("media_id") for clip in clips if clip.get("media_id") not in id_map
    ]
    if unknown:
        raise PackageInvalid(
            "工程包不完整，片段引用了未打包的素材：" + ", ".join(sorted(set(unknown)))
        )
    for clip in clips:
        clip["media_id"] = id_map[clip["media_id"]]

    project = Project(
        id=uuid.uuid4().hex,
        name=project_dict.get("name") or "导入的工程",
        clips=clips,
        version=1,
        created_at=_now(),
        updated_at=_now(),
    )
    save_project(project)
    report = {
        "reused": reused,
        "imported": imported,
        "missing": [],
        "corrupt": [],
    }
    return project, report
