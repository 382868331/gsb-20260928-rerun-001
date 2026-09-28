"""Render a Timeline with ffmpeg.

One single code path produces BOTH the low-res preview proxy and the final
export, so what you preview is what you get. Audio and video for each clip
come from the same trim / setpts / asetpts stage, keeping them in sync.
"""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from pathlib import Path

from media import EXPORTS_DIR, media_path, probe_media
from timeline import COLOR_FILTERS, FPS, HEIGHT, Timeline, WIDTH


class RenderError(Exception):
    pass


def validate(timeline: Timeline) -> None:
    if not timeline.clips:
        raise RenderError("时间线为空，没有可导出的片段")
    for clip in timeline.clips:
        path = media_path(clip.media_id)
        if path is None or not Path(path).exists():
            raise RenderError(f"素材缺失: {clip.media_id}")
        meta = probe_media(path)
        if clip.in_point < 0 or clip.out_point <= clip.in_point:
            raise RenderError("片段裁切范围无效")
        if clip.out_point > meta["duration"] + 0.05:
            raise RenderError(
                f"裁切超出素材长度 ({clip.out_point:.2f} > {meta['duration']:.2f})")
        if clip.speed < 0.25 or clip.speed > 4:
            raise RenderError("播放速度需在 0.25x - 4x 之间")


def build_filter_graph(timeline: Timeline, width: int, height: int,
                       sar: str = "1"):
    parts = []
    labels = []
    for i, clip in enumerate(timeline.clips):
        path = media_path(clip.media_id)
        meta = probe_media(path)
        tl_dur = clip.duration
        speed = clip.speed
        vchains = [
            f"[{i}:v]trim=start={clip.in_point:.4f}:end={clip.out_point:.4f}",
            "setpts=PTS-STARTPTS",
        ]
        if abs(speed - 1.0) > 1e-3:
            vchains.append(f"setpts=PTS/{speed:.5f}")
        vchains += [
            f"fps={FPS}",
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            f"setsar={sar}",
        ]
        cf = COLOR_FILTERS.get(clip.color, "")
        if cf:
            vchains.append(cf)
        vchains.append(
            "drawbox=x=0:y=0:w=iw:h=70:color=black@0.55:t=fill,"
            f"drawtext=text='{i + 1} {Path(path).stem}':fontcolor=white:"
            "fontsize=36:x=20:y=18",
        )
        parts.append(",".join(vchains) + f"[v{i}]")

        if meta["has_audio"]:
            achain = (
                f"[{i}:a]atrim=start={clip.in_point:.4f}:end={clip.out_point:.4f},"
                "asetpts=PTS-STARTPTS")
            if abs(speed - 1.0) > 1e-3:
                achain += f",atempo={speed:.5f}"
            achain += (f",volume={clip.volume:.3f},apad=whole_dur={tl_dur:.4f}"
                       f",atrim=duration={tl_dur:.4f}[a{i}]")
            parts.append(achain)
        else:
            parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={tl_dur:.4f}[a{i}]")
        labels.append(f"[v{i}][a{i}]")

    n = len(timeline.clips)
    parts.append("".join(labels) +
                 f"concat=n={n}:v=1:a=1[vout][aout]")
    return ";".join(parts), ["-map", "[vout]", "-map", "[aout]"]


def ffmpeg_inputs(timeline: Timeline):
    args = []
    for clip in timeline.clips:
        args += ["-i", str(media_path(clip.media_id))]
    return args


def render_command(timeline: Timeline, out_path: str, preview: bool):
    width, height = (854, 480) if preview else (WIDTH, HEIGHT)
    vbr = "2500k" if preview else "8000k"
    fc, maps = build_filter_graph(timeline, width, height)
    return (["ffmpeg", "-y", "-hide_banner", "-nostdin"]
            + ffmpeg_inputs(timeline)
            + ["-filter_complex", fc] + maps
            + ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
               "-b:v", vbr, "-c:a", "aac", "-b:a", "128k",
               "-progress", "pipe:1", "-nostats", "-f", "mp4", str(out_path)])


class Job:
    def __init__(self, job_id, kind, project_name):
        self.id = job_id
        self.kind = kind
        self.project_name = project_name
        self.status = "running"
        self.progress = 0.0
        self.output = None
        self.error = None
        self.created_at = time.time()
        self._proc = None
        self._thread = None

    def to_dict(self):
        return {
            "id": self.id, "kind": self.kind, "status": self.status,
            "progress": round(self.progress, 3), "output": self.output,
            "error": self.error, "project_name": self.project_name,
        }


class RenderManager:
    def __init__(self):
        self.jobs = {}

    def start(self, timeline, kind, project_name="untitled"):
        validate(timeline)
        job_id = uuid.uuid4().hex[:12]
        suffix = "_preview.mp4" if kind == "preview" else ".mp4"
        final_path = EXPORTS_DIR / f"{job_id}{suffix}"
        tmp_path = EXPORTS_DIR / f"{job_id}.part"
        job = Job(job_id, kind, project_name)
        self.jobs[job_id] = job

        def run():
            cmd = render_command(timeline, str(tmp_path), kind == "preview")
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1)
                job._proc = proc
                total = max(timeline.duration, 0.001)
                for line in proc.stdout:
                    if line.startswith("out_time_ms="):
                        try:
                            us = int(line.split("=", 1)[1].strip())
                            job.progress = min(0.99, (us / 1_000_000) / total)
                        except ValueError:
                            pass
                proc.wait()
                err_tail = proc.stderr.read()[-1200:]
                if proc.returncode != 0:
                    job.status = "failed"
                    job.error = err_tail or f"ffmpeg exited {proc.returncode}"
                    tmp_path.unlink(missing_ok=True)
                    return
                tmp_path.replace(final_path)  # atomic publish
                job.status = "done"
                job.progress = 1.0
                job.output = f"/exports/{final_path.name}"
            except Exception as exc:  # pragma: no cover
                job.status = "failed"
                job.error = str(exc)
                tmp_path.unlink(missing_ok=True)

        job._thread = threading.Thread(target=run, daemon=True)
        job._thread.start()
        return job

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if job and job.status == "running" and job._proc:
            job._proc.terminate()
            job._thread.join(timeout=10)  # wait for .part cleanup
            if job.status == "running":
                job.status = "failed"
            job.error = "已取消（工程仍已保存，可重新导出）"
            return True
        return False


MANAGER = RenderManager()
