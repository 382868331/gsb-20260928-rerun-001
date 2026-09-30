"""Render a project to a single MP4 with ffmpeg.

The exporter consumes the exact same ``compute_timeline`` result the UI
previews: source in/out points, timeline positions, overlap transitions
and per-clip volume all come from that one computation.

Each source is trimmed (video+audio on the same PTS interval) and each
clip's audio is scaled by its independent volume. Clips are then joined
with ``xfade``/``acrossfade`` overlaps, so the rendered boundaries and
volume ramps are identical to the preview's. A plain cut is rendered as
a one-frame fade (the smallest xfade accepts), which is visually a hard
cut; the final -t absorbs the sub-frame rounding.

Jobs run in background threads. Cancelling terminates the running
ffmpeg process; partial files are discarded and only a fully rendered
video is written to the final path. A failed/cancelled export never
touches the saved project, which can always be reopened and edited.
"""

from __future__ import annotations

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
HARD_CUT_FADE = 1.0 / OUTPUT_FPS


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
        final_path = EXPORTS_DIR / f"{info.id}.mp4"
        # Render into a temp name in the same directory and rename only on
        # success, so a cancelled/interrupted render can never leave a
        # playable-but-partial file behind and re-export needs no cleanup.
        partial_path = EXPORTS_DIR / f".{info.id}.part.mp4"
        try:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            project = load_project(job.project_id)
            if project is None:
                raise RuntimeError("工程不存在")
            media_index = load_index()
            timeline = compute_timeline(project, media_index)
            if not timeline.clips:
                raise RuntimeError("时间线为空，无法导出")
            if timeline.missing_media_ids:
                raise RuntimeError("素材缺失：" + ", ".join(timeline.missing_media_ids))

            info.status = "running"
            cmd = self._build_command(timeline, media_index, partial_path)
            self._run_process(job, cmd)
            if job.cancel_event.is_set():
                raise RuntimeError("导出已取消")
            partial_path.replace(final_path)
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
            for path in (final_path, partial_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        finally:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_json(EXPORTS_DIR / f"{info.id}.json", info.model_dump())

    def _build_command(
        self,
        timeline: Timeline,
        media_index: dict[str, MediaInfo],
        output: Path,
    ) -> list[str]:
        clips = timeline.clips
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        for clip in clips:
            cmd += ["-i", str(media_path(clip.media_id))]
        # One null-audio source is appended; clips without audio reference it.
        null_index = len(clips)
        max_duration = max(clip.duration for clip in clips)
        cmd += [
            "-f", "lavfi", "-i",
            f"anullsrc=r={OUTPUT_AUDIO_RATE}:cl=stereo:d={max_duration:.6f}",
        ]

        filters: list[str] = []
        video_labels: list[str] = []
        audio_labels: list[str] = []
        # xfade/acrossfade reject a zero-length transition and xfade
        # truncates its output for sub-frame durations; one full frame is
        # the smallest valid fade and renders as a visually hard cut.
        for index, clip in enumerate(clips):
            has_audio = media_index[clip.media_id].has_audio
            filters.append(
                f"[{index}:v]trim=start={clip.source_in:.6f}:duration={clip.duration:.6f},"
                "setpts=PTS-STARTPTS,"
                f"fps={OUTPUT_FPS},"
                f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease,"
                f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:black,"
                "settb=AVTB,format=yuv420p"
                f"[v{index}]"
            )
            if has_audio:
                source_chain = (
                    f"[{index}:a]atrim=start={clip.source_in:.6f}:duration={clip.duration:.6f},"
                    "asetpts=PTS-STARTPTS,"
                    f"aresample={OUTPUT_AUDIO_RATE}"
                )
            else:
                source_chain = (
                    f"[{null_index}:a]atrim=start=0:duration={clip.duration:.6f},"
                    "asetpts=PTS-STARTPTS"
                )
            # Independent per-clip volume is applied before the crossfade
            # ramps so overlaps mix the already-scaled material.
            filters.append(f"{source_chain},volume={clip.volume:.6f}[a{index}]")
            video_labels.append(f"[v{index}]")
            audio_labels.append(f"[a{index}]")

        if len(clips) == 1:
            filters.append(f"{video_labels[0]}null[vmix]")
            filters.append(f"{audio_labels[0]}anull[amix]")
        else:
            prev_v = video_labels[0]
            prev_a = audio_labels[0]
            composite_end = clips[0].duration
            for index in range(1, len(clips)):
                clip = clips[index]
                fade = max(clip.transition_in, HARD_CUT_FADE)
                is_last = index == len(clips) - 1
                out_v = "[vmix]" if is_last else f"[vx{index}]"
                out_a = "[amix]" if is_last else f"[ax{index}]"
                # xfade offset is the moment inside the composited-so-far
                # stream where the fade starts. A real overlap starts at
                # clip.start; the 1ms hard-cut stand-in consumes 1ms of
                # composite length that the timeline does not model, so
                # its offset is composite_end - fade. The final -t trims
                # the sub-frame remainder.
                offset = max(0.0, composite_end - fade)
                filters.append(
                    f"{prev_v}{video_labels[index]}"
                    f"xfade=transition=fade:duration={fade:.6f}:offset={offset:.6f}{out_v}"
                )
                filters.append(
                    f"{prev_a}{audio_labels[index]}"
                    f"acrossfade=d={fade:.6f}:c1=tri:c2=tri{out_a}"
                )
                composite_end = offset + clip.duration
                if not is_last:
                    prev_v = f"[vx{index}]"
                    prev_a = f"[ax{index}]"

        cmd += [
            "-filter_complex", ";".join(filters),
            "-map", "[vmix]", "-map", "[amix]",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-t", f"{timeline.duration + HARD_CUT_FADE * (len(clips) - 1):.6f}",
            str(output),
        ]
        return cmd

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
