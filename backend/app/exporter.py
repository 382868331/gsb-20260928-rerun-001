"""Render a project to MP4 using the resolved timeline."""

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
from .models import ExportInfo, MediaInfo, Timeline
from .store import atomic_write_json, load_project, media_path
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
    range_start: float = 0.0
    range_end: float | None = None


class ExportManager:
    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    def create(
        self,
        project_id: str,
        range_start: float = 0.0,
        range_end: float | None = None,
    ) -> ExportInfo:
        job_id = uuid.uuid4().hex
        info = ExportInfo(
            id=job_id,
            project_id=project_id,
            status="pending",
            created_at=_now(),
        )
        job = _Job(
            info=info,
            project_id=project_id,
            cancel_event=threading.Event(),
            range_start=max(0.0, range_start),
            range_end=range_end,
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
            jobs = [job.info for job in self._jobs.values() if job.project_id == project_id]
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
        return EXPORTS_DIR / f"{job_id}.mp4" if info and info.status == "done" else None

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
            range_start = min(max(0.0, job.range_start), timeline.duration)
            range_end = (
                min(max(0.0, job.range_end), timeline.duration)
                if job.range_end is not None
                else timeline.duration
            )
            if range_end <= range_start:
                raise RuntimeError("导出区间无效")

            work_dir.mkdir(parents=True, exist_ok=True)
            info.status = "running"
            self._render_timeline(job, timeline, media_index, final_path, range_start, range_end)
            if job.cancel_event.is_set():
                raise RuntimeError("导出已取消")
            info.status = "done"
            info.progress = 1.0
            info.output_url = f"/api/exports/{info.id}/download"
            info.finished_at = _now()
        except Exception as exc:  # noqa: BLE001
            info.status = "cancelled" if job.cancel_event.is_set() else "failed"
            if info.status == "failed":
                info.error = str(exc)
            info.finished_at = _now()
            if final_path.exists():
                final_path.unlink(missing_ok=True)
        finally:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_json(EXPORTS_DIR / f"{info.id}.json", info.model_dump())
            shutil.rmtree(work_dir, ignore_errors=True)

    def _render_timeline(
        self,
        job: _Job,
        timeline: Timeline,
        media_index: dict[str, MediaInfo],
        final_path: Path,
        range_start: float,
        range_end: float,
    ) -> None:
        cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        filters: list[str] = []

        for index, clip in enumerate(timeline.clips):
            source = media_path(clip.media_id)
            if not source.exists():
                raise RuntimeError(f"素材缺失：{clip.media_id}")
            cmd += ["-i", str(source)]

        silent_clips = {
            index for index, clip in enumerate(timeline.clips)
            if not media_index[clip.media_id].has_audio
        }
        next_input = len(timeline.clips)
        silent_input: dict[int, int] = {}
        for clip_index in silent_clips:
            cmd += [
                "-f", "lavfi", "-i",
                f"anullsrc=r={OUTPUT_AUDIO_RATE}:cl=stereo:d={timeline.duration:.6f}",
            ]
            silent_input[clip_index] = next_input
            next_input += 1

        for index, clip in enumerate(timeline.clips):
            video_segments: list[str] = []
            audio_segments: list[str] = []
            for segment_index, segment in enumerate(clip.segments):
                suffix = f"{index}_{segment_index}"
                source_duration = segment.source_out - segment.source_in
                filters.append(
                    f"[{index}:v]trim=start={segment.source_in:.6f}:duration={source_duration:.6f},"
                    f"setpts=(PTS-STARTPTS)/{segment.rate:.6f},fps={OUTPUT_FPS},"
                    f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease,"
                    f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:black,"
                    f"format=yuv420p[vs{suffix}]"
                )
                video_segments.append(f"vs{suffix}")
                audio_label = f"as{suffix}"
                if index in silent_clips:
                    silent_index = silent_input[index]
                    filters.append(
                        f"[{silent_index}:a]atrim=start={clip.transition + segment.start:.6f}:duration={segment.duration:.6f},"
                        f"asetpts=PTS-STARTPTS[{audio_label}]"
                    )
                else:
                    filters.append(
                        f"[{index}:a]atrim=start={segment.source_in:.6f}:duration={source_duration:.6f},"
                        "asetpts=PTS-STARTPTS,"
                        f"aresample={OUTPUT_AUDIO_RATE},asetrate={int(OUTPUT_AUDIO_RATE * segment.rate)},"
                        f"aresample={OUTPUT_AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={OUTPUT_AUDIO_RATE}[{audio_label}]"
                    )
                audio_segments.append(audio_label)

            if len(video_segments) == 1:
                filters.append(f"[{video_segments[0]}]null[vraw{index}]")
                filters.append(f"[{audio_segments[0]}]anull[araw{index}]")
            else:
                filters.append(
                    "".join(f"[{label}]" for label in video_segments)
                    + f"concat=n={len(video_segments)}:v=1:a=0[vraw{index}]"
                )
                filters.append(
                    "".join(f"[{label}]" for label in audio_segments)
                    + f"concat=n={len(audio_segments)}:v=0:a=1[araw{index}]"
                )

            video_filter = f"[vraw{index}]null"
            audio_filter = f"[araw{index}]"
            volume = max(0.0, min(4.0, clip.volume))
            if volume != 1.0:
                video_filter += f"volume={volume:.6f}"
                audio_filter += f"volume={volume:.6f},"
            if index > 0 and clip.transition > 0:
                video_filter += f",fade=t=in:st=0:d={clip.transition:.6f}"
                audio_filter += f"afade=t=in:st=0:d={clip.transition:.6f},"
            if clip.transition > 0:
                fade_start = max(0.0, clip.duration - clip.transition)
                video_filter += f",fade=t=out:st={fade_start:.6f}:d={clip.transition:.6f}"
                audio_filter += f"afade=t=out:st={fade_start:.6f}:d={clip.transition:.6f},"
            audio_filter += f"atrim=duration={clip.duration:.6f},asetpts=PTS-STARTPTS"
            filters.append(f"{video_filter},setpts=PTS-STARTPTS[v{index}]")
            filters.append(f"{audio_filter}[a{index}]")

        video_label = "v0"
        audio_label = "a0"
        for index in range(1, len(timeline.clips)):
            clip = timeline.clips[index]
            next_video = f"vx{index}"
            next_audio = f"ax{index}"
            if clip.transition > 0:
                filters.append(
                    f"[{video_label}][v{index}]xfade=transition=fade:offset={clip.start:.6f}:duration={clip.transition:.6f}[{next_video}]"
                )
                filters.append(
                    f"[{audio_label}][a{index}]acrossfade=d={clip.transition:.6f}:c1=tri:c2=tri[{next_audio}]"
                )
            else:
                filters.append(f"[{video_label}][v{index}]concat=n=2:v=1:a=0[{next_video}]")
                filters.append(f"[{audio_label}][a{index}]concat=n=2:v=0:a=1[{next_audio}]")
            video_label, audio_label = next_video, next_audio

        export_duration = range_end - range_start
        if range_start > 0 or range_end < timeline.duration:
            filters.append(
                f"[{video_label}]trim=start={range_start:.6f}:duration={export_duration:.6f},setpts=PTS-STARTPTS[vfinal]"
            )
            filters.append(
                f"[{audio_label}]atrim=start={range_start:.6f}:duration={export_duration:.6f},asetpts=PTS-STARTPTS[afinal]"
            )
            video_label, audio_label = "vfinal", "afinal"

        cmd += [
            "-filter_complex", ";".join(filters),
            "-map", f"[{video_label}]", "-map", f"[{audio_label}]",
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "128k",
            "-t", f"{export_duration:.6f}", str(final_path),
        ]
        self._run_process(job, cmd)
        job.info.progress = 0.99

    def _run_process(self, job: _Job, cmd: list[str]) -> None:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        with job.process_lock:
            job.process = process
        _, stderr = process.communicate()
        if job.cancel_event.is_set() or process.returncode != 0:
            if job.cancel_event.is_set():
                raise RuntimeError("导出已取消")
            message = (stderr or "").strip()
            raise RuntimeError("ffmpeg 导出失败：" + (message[-500:] if message else "未知错误"))


manager = ExportManager()
