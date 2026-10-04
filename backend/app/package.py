"""Portable project packaging and offline recovery.

A package is a single .zip file that contains:

* manifest.json -- the project plus the metadata of every media it
  references (clips keep referencing the original media ids);
* blobs/<sha256> -- the actual media content, addressed by sha256.

Only media actually referenced by the project is included, and identical
content (same sha256, even under different media ids/filenames) is
stored exactly once. Importing validates every blob checksum before
touching the media library or project files, so a package with missing
or corrupt files is rejected with an explicit issue list and changes
nothing.
"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import PACKAGES_DIR, STAGING_DIR
from .media import _sha256, import_file, load_index
from .models import (
    Clip,
    ImportIssue,
    ImportResult,
    PackageInfo,
    PackageMediaEntry,
    Project,
)
from .store import atomic_write_json, load_project, media_path, save_project

PACKAGE_FORMAT = "video-editor-package/1"
MANIFEST_NAME = "manifest.json"
BLOBS_PREFIX = "blobs/"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _referenced_media(
    project: Project, index: dict
) -> tuple[list[PackageMediaEntry], list[ImportIssue]]:
    """Return media entries actually referenced, de-duplicated by content."""
    seen_digests: set[str] = set()
    entries: list[PackageMediaEntry] = []
    issues: list[ImportIssue] = []
    for clip in project.clips:
        info = index.get(clip.media_id)
        if info is None or not media_path(clip.media_id).exists():
            issues.append(
                ImportIssue(
                    kind="missing",
                    media_id=clip.media_id,
                    detail=f"工程引用的素材 {clip.media_id} 在素材库中缺失，无法打包",
                )
            )
            continue
        digest = getattr(info, "sha256", None) or _sha256(media_path(clip.media_id))
        if digest in seen_digests:
            continue
        seen_digests.add(digest)
        entries.append(
            PackageMediaEntry(
                media_id=info.id,
                filename=info.filename,
                duration=info.duration,
                width=info.width,
                height=info.height,
                has_audio=info.has_audio,
                is_sample=info.is_sample,
                sha256=digest,
            )
        )
    return entries, issues


@dataclass
class _PackageJob:
    info: PackageInfo
    project_id: str
    cancel_event: threading.Event


class PackageManager:
    """Background package jobs with cancellation, mirroring ExportManager."""

    def __init__(self) -> None:
        self._jobs: dict[str, _PackageJob] = {}
        self._lock = threading.Lock()

    def create(self, project_id: str) -> PackageInfo:
        job_id = uuid.uuid4().hex
        info = PackageInfo(
            id=job_id,
            project_id=project_id,
            status="pending",
            created_at=_now(),
        )
        job = _PackageJob(
            info=info, project_id=project_id, cancel_event=threading.Event()
        )
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return info

    def get(self, job_id: str) -> PackageInfo | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.info.model_copy(deep=True) if job else None

    def list_for_project(self, project_id: str) -> list[PackageInfo]:
        with self._lock:
            jobs = [
                job.info
                for job in self._jobs.values()
                if job.project_id == project_id
            ]
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        return jobs

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
        return PACKAGES_DIR / f"{job_id}.zip"

    def _run(self, job: _PackageJob) -> None:
        info = job.info
        final_path = PACKAGES_DIR / f"{info.id}.zip"
        tmp_path = PACKAGES_DIR / f"{info.id}.zip.tmp"
        try:
            PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
            project = load_project(job.project_id)
            if project is None:
                raise RuntimeError("工程不存在")
            index = load_index()
            entries, issues = _referenced_media(project, index)
            if issues:
                raise RuntimeError("；".join(item.detail for item in issues))

            info.status = "running"
            manifest = {
                "format": PACKAGE_FORMAT,
                "created_at": _now(),
                "project": project.model_dump(),
                "media": [entry.model_dump() for entry in entries],
            }

            total = max(1, len(entries))
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(
                    MANIFEST_NAME,
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                for position, entry in enumerate(entries, start=1):
                    if job.cancel_event.is_set():
                        raise RuntimeError("打包已取消")
                    source = media_path(entry.media_id)
                    # Stream the blob through the archive so a cancel is
                    # observed while a large file is copied and the whole
                    # media is never buffered in memory.
                    blob_name = f"{BLOBS_PREFIX}{entry.sha256}"
                    with archive.open(blob_name, "w", force_zip64=True) as out:
                        with open(source, "rb") as handle:
                            shutil.copyfileobj(handle, out, length=1024 * 1024)
                    if job.cancel_event.is_set():
                        raise RuntimeError("打包已取消")
                    info.progress = round(position / total, 4)
            tmp_path.replace(final_path)
            info.status = "done"
            info.progress = 1.0
            info.output_url = f"/api/packages/{info.id}/download"
            info.finished_at = _now()
        except Exception as exc:  # noqa: BLE001 - surface every failure
            info.status = "cancelled" if job.cancel_event.is_set() else "failed"
            info.error = str(exc)
            info.finished_at = _now()
            if final_path.exists():
                final_path.unlink(missing_ok=True)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            atomic_write_json(
                PACKAGES_DIR / f"{info.id}.json", info.model_dump()
            )


def _cleanup_staging() -> None:
    shutil.rmtree(STAGING_DIR, ignore_errors=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)


def import_package(upload_path: Path, original_name: str = "package.zip") -> ImportResult:
    """Import a package into this data root.

    The whole package is validated first (zip structure, manifest,
    checksum of every referenced blob). Only when it is sound are blobs
    imported into the media library and the project saved, so a bad
    package never leaves a half-restored state.
    """
    if not zipfile.is_zipfile(upload_path):
        return ImportResult(ok=False, error="文件不是有效的工程包（无法识别的 zip 归档）")

    try:
        archive = zipfile.ZipFile(upload_path)
    except (OSError, zipfile.BadZipFile) as exc:
        return ImportResult(ok=False, error=f"工程包已损坏，无法打开：{exc}")

    with archive:
        bad_name = next(
            (
                name
                for name in archive.namelist()
                if name.startswith("/") or ".." in Path(name).parts
            ),
            None,
        )
        if bad_name is not None:
            return ImportResult(ok=False, error=f"工程包包含非法路径条目：{bad_name}")
        try:
            manifest_raw = archive.read(MANIFEST_NAME)
        except KeyError:
            return ImportResult(ok=False, error="工程包缺少 manifest.json，无法恢复")
        try:
            manifest = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return ImportResult(ok=False, error=f"manifest.json 已损坏：{exc}")
        if manifest.get("format") != PACKAGE_FORMAT:
            return ImportResult(
                ok=False, error=f"不支持的工程包格式：{manifest.get('format')}"
            )
        try:
            project = Project.model_validate(manifest["project"])
            media_entries = [
                PackageMediaEntry.model_validate(item)
                for item in manifest.get("media", [])
            ]
        except (KeyError, ValueError) as exc:
            return ImportResult(
                ok=False, error=f"manifest.json 内容不完整或已损坏：{exc}"
            )

        issues: list[ImportIssue] = []
        referenced = {clip.media_id for clip in project.clips}
        entries_by_id = {entry.media_id: entry for entry in media_entries}
        staged: dict[str, Path] = {}
        for media_id in sorted(referenced):
            entry = entries_by_id.get(media_id)
            if entry is None:
                issues.append(
                    ImportIssue(
                        kind="missing",
                        media_id=media_id,
                        detail=f"素材 {media_id} 未包含在工程包中（清单缺失）",
                    )
                )
                continue
            blob_name = f"{BLOBS_PREFIX}{entry.sha256}"
            try:
                info = archive.getinfo(blob_name)
            except KeyError:
                issues.append(
                    ImportIssue(
                        kind="missing",
                        media_id=media_id,
                        filename=entry.filename,
                        sha256=entry.sha256,
                        detail=f"素材文件缺失：{entry.filename}（{entry.sha256[:12]}…）",
                    )
                )
                continue
            # Extract to staging and verify the checksum before anything
            # is written into the media library.
            extract_dir = STAGING_DIR / f"import_{uuid.uuid4().hex}"
            extract_dir.mkdir(parents=True, exist_ok=True)
            staged_path = extract_dir / f"{uuid.uuid4().hex}.mp4"
            try:
                with archive.open(info) as source:
                    with open(staged_path, "wb") as out:
                        shutil.copyfileobj(source, out, length=1024 * 1024)
                digest = _sha256(staged_path)
            except (OSError, zipfile.BadZipFile) as exc:
                issues.append(
                    ImportIssue(
                        kind="corrupt",
                        media_id=media_id,
                        filename=entry.filename,
                        sha256=entry.sha256,
                        detail=f"素材文件无法读取（归档损坏）：{entry.filename}（{exc}）",
                    )
                )
                continue
            if digest != entry.sha256:
                issues.append(
                    ImportIssue(
                        kind="corrupt",
                        media_id=media_id,
                        filename=entry.filename,
                        sha256=entry.sha256,
                        detail=(
                            f"素材文件已损坏（校验不匹配）：{entry.filename}，"
                            f"期望 {entry.sha256[:12]}… 实际 {digest[:12]}…"
                        ),
                    )
                )
                continue
            staged[media_id] = staged_path

        if issues:
            _cleanup_staging()
            return ImportResult(
                ok=False,
                issues=issues,
                error="工程包校验未通过，已取消导入且未修改素材库/工程："
                + "；".join(issue.detail for issue in issues),
            )

        # Package is sound: import blobs (content-addressed dedup by
        # sha256) and remap media ids used by the clips. Same-name but
        # different-content entries never collide because matching is
        # strictly by content hash.
        index = load_index()
        id_remap: dict[str, str] = {}
        imported = []
        reused = []
        for media_id in sorted(referenced):
            entry = entries_by_id[media_id]
            matching = next(
                (item for item in index.values() if item.sha256 == entry.sha256),
                None,
            )
            if matching is not None:
                id_remap[media_id] = matching.id
                reused.append(matching)
                continue
            info = import_file(staged[media_id], entry.filename, is_sample=False)
            id_remap[media_id] = info.id
            imported.append(info)
            index = load_index()

    # Remap clips onto whatever media ids this library assigns, then
    # give the restored project a fresh id if the original already
    # exists here (importing never overwrites an existing project).
    restored_clips = []
    for clip in project.clips:
        clip_data = clip.model_dump()
        clip_data["media_id"] = id_remap[clip.media_id]
        restored_clips.append(Clip.model_validate(clip_data))
    project.clips = restored_clips
    if load_project(project.id) is not None:
        project.id = uuid.uuid4().hex
    project.version = 1
    project.updated_at = _now()
    save_project(project)

    _cleanup_staging()
    return ImportResult(
        ok=True,
        project_id=project.id,
        project_name=project.name,
        imported_media=imported,
        reused_media=reused,
    )


manager = PackageManager()
