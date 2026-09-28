"""Render a project to a single MP4 with ffmpeg.

The exporter uses the exact same ``compute_timeline`` result the UI
previews: source in/out points and their running sums decide both the
rendered cut points and the timeline positions. Video and audio are cut
in one filter graph per clip, so they stay in lockstep.

Jobs run in background threads. Cancelling terminates the running
ffmpeg process; partial files are discarded and only a fully rendered
video is written to the final path. A failed/cancelled export never
touches the saved project, which can always be reopened and edited.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import EXPORTS_DIR
from .media import load_index
from .store import media_path
from .models import ExportInfo, MediaInfo, Timeline
from .store import atomic_write_json, load_project
from .timeline import compute_timeline

OUTPUT_AUDIO_RATE = 48000
OUTPUT_FPS = 30
OUTPUT_W = 640
OUTPUT_H = 360


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class _Job:
    info: ExportInfo
    project_id: str
    cancel_event: threading.Event
    process: subprocess.Popen | None = None
    process_lock: threading.Lock = field(default_factory=threading.Lock)


class ExportManager:
    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    def create(self, project_id: str) -> ExportInfo:
        job_id = uuid.uuid4().hex
        info = ExportInfo(
            id=job_id,
            project_id=project_id,
            status="pending",
            created_at=_now(),
        )
        job = _Job(
            info=info, project_id=project_id, cancel_event=threading.Event()
        )
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return info

    def get(self, job_id: str) -> ExportInfo | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.info.model_copy(deep=True) if job else None

    def list_for_project(self, project_id: str) -> list[ExportInfo]:
        with self._lock:
            jobs = [
                job.info for job in self._jobs.values() if job.project_id == project_id
            ]
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        return jobs

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False
        job.cancel_event.set()
        with job.process_lock:
            process = job.process
        if process is not None and process.poll() is None:
            process.terminate()
        return True

    def output_path(self, job_id: str) -> Path | None:
        info = self.get(job_id)
        if info is None or info.status != "done":
            return None
        return EXPORTS_DIR / f"{job_id}.mp4"

    def _run(self, job: _Job) -> None:
        info = job.info
        work_dir = EXPORTS_DIR / info.id
        final_path = EXPORTS_DIR / f"{info.id}.mp4"
        try:
            project = load_project(job.project_id)
            if project is None:
                raise RuntimeError("工程不存在")
            media_index = load_index()
            timeline = compute_timeline(project, media_index)
            if not timeline.clips:
                raise RuntimeError("时间线为空，无法导出")
            if timeline.missing_media_ids:
                raise RuntimeError("素材缺失：" + ", ".join(timeline.missing_media_ids))

            work_dir.mkdir(parents=True, exist_ok=True)
            info.status = "running"
            segments = self._render_segments(job, timeline, media_index, work_dir)
            if job.cancel_event.is_set():
                raise RuntimeError("导出已取消")
            self._concat(segments, work_dir, final_path, job)
            if job.cancel_event.is_set():
                raise RuntimeError("导出已取消")
            info.status = "done"
            info.progress = 1.0
            info.output_url = f"/api/exports/{info.id}/download"
            info.finished_at = _now()
        except Exception as exc:  # noqa: BLE001 - report every failure to UI
            if job.cancel_event.is_set():
                info.status = "cancelled"
            else:
                info.status = "failed"
                info.error = str(exc)
            info.finished_at = _now()
            if final_path.exists():
                try:
                    final_path.unlink()
                except OSError:
                    pass
        finally:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_json(EXPORTS_DIR / f"{info.id}.json", info.model_dump())
            shutil.rmtree(work_dir, ignore_errors=True)

    def _render_segments(
        self,
        job: _Job,
        timeline: Timeline,
        media_index: dict[str, MediaInfo],
        work_dir: Path,
    ) -> list[Path]:
        segments: list[Path] = []
        total = timeline.duration or 1.0
        done_before = 0.0
        for index, clip in enumerate(timeline.clips):
            if job.cancel_event.is_set():
                break
            source = media_path(clip.media_id)
            if not source.exists():
                raise RuntimeError(f"素材缺失：{clip.media_id}")
            output = work_dir / f"segment_{index:04d}.mp4"
            self._render_one(
                job,
                source,
                clip.source_in,
                clip.duration,
                output,
                media_index[clip.media_id].has_audio,
            )
            segments.append(output)
            done_before += clip.duration
            job.info.progress = min(0.99, round(0.95 * done_before / total, 4))
        return segments

    def _render_one(
        self,
        job: _Job,
        source: Path,
        source_in: float,
        duration: float,
        output: Path,
        has_audio: bool,
    ) -> None:
        # Precise trim/atrim in one filter graph slices video and audio
        # by the same PTS interval; anullsrc fills clips without audio
        # so every segment has an identical stream layout for concat.
        video_chain = (
            f"[0:v]trim=start={source_in:.6f}:duration={duration:.6f},"
            "setpts=PTS-STARTPTS,"
            f"fps={OUTPUT_FPS},"
            f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease,"
            f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:black,"
            "format=yuv420p[v]"
        )
        if has_audio:
            audio_chain = (
                f"[0:a]atrim=start={source_in:.6f}:duration={duration:.6f},"
                "asetpts=PTS-STARTPTS,"
                f"aresample={OUTPUT_AUDIO_RATE},"
                "apad=whole_dur={:.6f}[a]".format(duration)
            )
        else:
            audio_chain = (
                f"anullsrc=r={OUTPUT_AUDIO_RATE}:cl=stereo:d={duration:.6f}[a]"
            )
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
        ]
        if not has_audio:
            cmd += [
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r={OUTPUT_AUDIO_RATE}:cl=stereo:d={duration:.6f}",
            ]
        cmd += [
            "-filter_complex",
            f"{video_chain};{audio_chain}",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-t",
            f"{duration:.6f}",
            str(output),
        ]
        self._run_process(job, cmd)

    def _concat(
        self, segments: list[Path], work_dir: Path, final_path: Path, job: _Job
    ) -> None:
        list_file = work_dir / "concat.txt"
        lines = []
        for segment in segments:
            escaped = str(segment).replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Identical codec parameters across segments -> stream-copy concat
        # keeps exact boundaries without a second encode.
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(final_path),
        ]
        self._run_process(job, cmd)

    def _run_process(self, job: _Job, cmd: list[str]) -> None:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        with job.process_lock:
            job.process = process
        _, stderr = process.communicate()
        if job.cancel_event.is_set() or process.returncode != 0:
            if job.cancel_event.is_set():
                raise RuntimeError("导出已取消")
            message = (stderr or "").strip()
            raise RuntimeError(
                "ffmpeg 导出失败：" + (message[-500:] if message else "未知错误")
            )


manager = ExportManager()
