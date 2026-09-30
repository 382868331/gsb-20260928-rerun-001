"""Render a project to a single MP4 with ffmpeg.

The exporter uses the exact same ``compute_timeline`` result the UI
previews: source in/out points, per-clip volume and transition lengths
decide the rendered frames and the soundtrack. Everything is rendered
in ONE ffmpeg filter graph: each clip is trimmed/normalised, adjacent
clips are joined with ``xfade`` (video) and ``acrossfade`` (audio)
overlaps of exactly ``clip.transition`` seconds starting at
``clip.transition_start``, or with a plain concat when the transition
is zero. Video and audio stay in lockstep and the output matches the
preview frame for frame.

Jobs run in background threads. Cancelling terminates the running
ffmpeg process; partial files are discarded and only a fully rendered
video is kept at the final path. A failed/cancelled export never
touches the saved project, and re-exporting the same project renders
the same timeline again.
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
            self._render(job, timeline, media_index, final_path)
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

    def _render(
        self,
        job: _Job,
        timeline: Timeline,
        media_index: dict[str, MediaInfo],
        final_path: Path,
    ) -> None:
        clips = timeline.clips
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        filters: list[str] = []
        silent_inputs = 0
        for index, clip in enumerate(clips):
            source = media_path(clip.media_id)
            if not source.exists():
                raise RuntimeError(f"素材缺失：{clip.media_id}")
            cmd += ["-i", str(source)]
            # Precise trim in one filter graph slices video and audio by
            # the same PTS interval, so each clip stays in lockstep.
            filters.append(
                f"[{index}:v]trim=start={clip.source_in:.6f}:duration={clip.duration:.6f},"
                "setpts=PTS-STARTPTS,"
                f"fps={OUTPUT_FPS},"
                f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease,"
                f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:black,"
                f"format=yuv420p[v{index}]"
            )
            if media_index[clip.media_id].has_audio:
                filters.append(
                    f"[{index}:a]atrim=start={clip.source_in:.6f}:duration={clip.duration:.6f},"
                    "asetpts=PTS-STARTPTS,"
                    f"aresample={OUTPUT_AUDIO_RATE},"
                    "aformat=channel_layouts=stereo,"
                    f"apad=whole_dur={clip.duration:.6f},"
                    f"volume={clip.volume:.6f}[a{index}]"
                )
            else:
                # Silence keeps the stream layout identical for every clip.
                silent = len(clips) + silent_inputs
                silent_inputs += 1
                cmd += [
                    "-f",
                    "lavfi",
                    "-i",
                    f"anullsrc=r={OUTPUT_AUDIO_RATE}:cl=stereo:d={clip.duration:.6f}",
                ]
                filters.append(
                    f"[{silent}:a]atrim=duration={clip.duration:.6f},"
                    f"asetpts=PTS-STARTPTS[a{index}]"
                )
        # Join clips pairwise: xfade/acrossfade for overlaps, concat for
        # hard cuts. Offsets come straight from compute_timeline so the
        # render matches the preview exactly.
        video_cur, audio_cur = "v0", "a0"
        for index in range(1, len(clips)):
            clip = clips[index]
            video_out = f"x{index}v"
            audio_out = f"x{index}a"
            if clip.transition > 0:
                filters.append(
                    f"[{video_cur}][v{index}]xfade=transition=fade:"
                    f"duration={clip.transition:.6f}:"
                    f"offset={clip.transition_start:.6f}[{video_out}]"
                )
                filters.append(
                    f"[{audio_cur}][a{index}]acrossfade=d={clip.transition:.6f}:"
                    f"c1=tri:c2=tri[{audio_out}]"
                )
            else:
                filters.append(
                    f"[{video_cur}][v{index}]concat=n=2:v=1:a=0[{video_out}]"
                )
                filters.append(
                    f"[{audio_cur}][a{index}]concat=n=2:v=0:a=1[{audio_out}]"
                )
            video_cur, audio_cur = video_out, audio_out
        total = max(timeline.duration, 0.001)
        cmd += [
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{video_cur}]",
            "-map",
            f"[{audio_cur}]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-progress",
            "pipe:1",
            "-nostats",
            str(final_path),
        ]
        self._run_process(job, cmd, total)

    def _run_process(self, job: _Job, cmd: list[str], total: float) -> None:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with job.process_lock:
            job.process = process
        tail: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    done = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                job.info.progress = min(0.99, round(0.02 + 0.96 * done / total, 4))
            elif line:
                tail.append(line)
                del tail[:-20]
        process.wait()
        if job.cancel_event.is_set() or process.returncode != 0:
            if job.cancel_event.is_set():
                raise RuntimeError("导出已取消")
            message = " ".join(tail).strip()
            raise RuntimeError(
                "ffmpeg 导出失败：" + (message[-500:] if message else "未知错误")
            )


manager = ExportManager()
