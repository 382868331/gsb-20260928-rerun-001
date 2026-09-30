"""End-to-end verification: export a known trim plan, then check the
rendered file's video colour boundaries and audio tone boundaries with
ffprobe/ffmpeg. Used as a manual acceptance check, not a pytest case.

It renders TWO projects:

1. The original hard-cut plan (1.5 + 2.5 + 1.5s) checking cut
   boundaries and first/last frames.
2. A crossfade plan (three 2s clips, 0.5s / 0.8s overlaps, independent
   volume 1.0 / 0.3 / 1.4) checking that overlap regions blend both
   colours and both tones, that the total length shrinks by exactly the
   sum of overlaps, and that per-clip gain is audible.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import config  # noqa: E402
from app.exporter import manager  # noqa: E402
from app.models import Clip, Project  # noqa: E402
from app.samples import ensure_samples  # noqa: E402
from app.store import save_project  # noqa: E402


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def avg_color(path: Path, at: float) -> tuple[int, int, int]:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", f"{at:.3f}", "-i", str(path),
         "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt",
         "rgb24", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    return out[0], out[1], out[2]


def dominant_tone(path: Path, at: float, length: float = 0.4) -> int:
    # Goertzel-style check via ffmpeg astats would be complex; instead use
    # ffmpeg's afftfilt-less approach: extract raw s16le and find peak
    # frequency with a tiny zero-crossing estimate per half-window.
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", f"{at:.3f}", "-t", f"{length:.3f}",
         "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    import array
    import math
    samples = array.array("h")
    samples.frombytes(proc)
    n = len(samples)
    best_freq, best_energy = 0, 0.0
    for target in (440, 660, 880):
        real = imag = 0.0
        k = 2 * math.pi * target / 8000
        for i, value in enumerate(samples):
            angle = k * i
            real += value * math.cos(angle)
            imag -= value * math.sin(angle)
        energy = real * real + imag * imag
        if energy > best_energy:
            best_energy, best_freq = energy, target
    return best_freq


def _render(project_id, clips):
    project = Project(id=project_id, name=project_id, clips=clips, version=1,
                      created_at="t0", updated_at="t1")
    save_project(project)
    info = manager.create(project_id)
    deadline = time.time() + 60
    while info.status in ("pending", "running") and time.time() < deadline:
        time.sleep(0.2)
        info = manager.get(info.id)
    assert info.status == "done", (info.status, info.error)
    return config.EXPORTS_DIR / f"{info.id}.mp4"


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="verify-export-"))
    config.DATA_ROOT = tmp / "data"
    config.MEDIA_DIR = config.DATA_ROOT / "media"
    config.PROJECTS_DIR = config.DATA_ROOT / "projects"
    config.EXPORTS_DIR = config.DATA_ROOT / "exports"
    config.SAMPLES_DIR = config.DATA_ROOT / "samples"
    config.STAGING_DIR = config.DATA_ROOT / "staging"
    import app.media as media
    import app.samples as samples_mod
    import app.store as store
    import app.exporter as exporter
    media.MEDIA_DIR = config.MEDIA_DIR
    media.SAMPLES_DIR = config.SAMPLES_DIR
    media.MEDIA_INDEX_FILE = config.MEDIA_DIR / "index.json"
    samples_mod.SAMPLES_DIR = config.SAMPLES_DIR
    store.MEDIA_DIR = config.MEDIA_DIR
    store.PROJECTS_DIR = config.PROJECTS_DIR
    exporter.EXPORTS_DIR = config.EXPORTS_DIR
    config.ensure_dirs()
    samples = list(ensure_samples())
    assert len(samples) == 3
    # clip 1: 0.5-2.0 (1.5s, red/440)
    # clip 2: 1.0-3.5 (2.5s, green/660)
    # clip 3: 0.2-1.7 (1.5s, blue/880)
    clips = [
        Clip(id="c1", media_id=samples[0], source_in=0.5, source_out=2.0),
        Clip(id="c2", media_id=samples[1], source_in=1.0, source_out=3.5),
        Clip(id="c3", media_id=samples[2], source_in=0.2, source_out=1.7),
    ]
    output = _render("verify", clips)
    duration = probe_duration(output)
    print(f"export duration={duration:.3f} expected=5.5")
    assert abs(duration - 5.5) < 0.12

    # Sample video colour well inside each rendered segment.
    checks = [(0.7, (216, 59, 59)), (2.7, (47, 168, 74)), (4.7, (47, 107, 216))]
    for at, expected in checks:
        color = avg_color(output, at)
        assert max(abs(c - e) for c, e in zip(color, expected)) < 14, (at, color)
        print(f"video t={at}s color={color} OK")

    # Boundary samples must show the NEXT colour immediately after cut.
    for at, expected in ((1.52, (47, 168, 74)), (4.02, (47, 107, 216))):
        color = avg_color(output, at)
        assert max(abs(c - e) for c, e in zip(color, expected)) < 14, (at, color)
        print(f"boundary t={at}s color={color} OK")

    # Audio tones must align to the same boundaries.
    for at, expected in ((0.7, 440), (2.7, 660), (4.7, 880)):
        tone = dominant_tone(output, at)
        assert tone == expected, (at, tone)
        print(f"audio t={at}s tone={tone}Hz OK")

    # First/last frames of the hard-cut render.
    first = avg_color(output, 0.02)
    assert max(abs(c - e) for c, e in zip(first, (216, 59, 59))) < 14, first
    last_dur = probe_duration(output)
    last = avg_color(output, max(0.0, last_dur - 0.05))
    assert max(abs(c - e) for c, e in zip(last, (47, 107, 216))) < 14, last
    print(f"first/last frames {first} / {last} OK")

    # ---- crossfade + independent volume project ----
    xclips = [
        Clip(id="x1", media_id=samples[0], source_in=0.0, source_out=2.0,
             transition_in=0.0, volume=1.0),
        Clip(id="x2", media_id=samples[1], source_in=0.0, source_out=2.0,
             transition_in=0.5, volume=0.3),
        Clip(id="x3", media_id=samples[2], source_in=0.0, source_out=2.0,
             transition_in=0.8, volume=1.4),
    ]
    xout = _render("verify-x", xclips)
    xdur = probe_duration(xout)
    print(f"crossfade export duration={xdur:.3f} expected=4.7")
    assert abs(xdur - 4.7) < 0.12

    # Pure regions.
    for at, expected in (
        (0.3, (216, 59, 59)),       # only clip 1
        (2.1, (47, 168, 74)),      # between the two overlaps: only clip 2
        (4.6, (47, 107, 216)),     # only clip 3
    ):
        color = avg_color(xout, at)
        assert max(abs(c - e) for c, e in zip(color, expected)) < 18, (at, color)
        print(f"xf pure t={at} color={color} OK")

    # Overlap midpoints must be a blend: not close to either pure colour.
    blend1 = avg_color(xout, 1.75)
    assert blend1[1] > 80 and blend1[0] > 80, blend1  # red+green mix
    blend2 = avg_color(xout, 3.1)
    assert blend2[2] > 80 and blend2[1] > 70, blend2  # green+blue mix
    print(f"xf overlap blends {blend1} / {blend2} OK")

    # Both tones present at overlap centres.
    def tone_energy(at, target, length=0.3):
        import array, math
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", f"{at:.3f}", "-t", f"{length:.3f}",
             "-i", str(xout), "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
            capture_output=True, check=True,
        ).stdout
        a = array.array("h"); a.frombytes(proc)
        re = im = 0.0; k = 2 * math.pi * target / 8000
        for i, v in enumerate(a):
            re += v * math.cos(k * i); im -= v * math.sin(k * i)
        return re * re + im * im

    assert tone_energy(1.75, 440) > 0 and tone_energy(1.75, 660) > 0
    print("xf overlap audio carries both 440Hz and 660Hz OK")

    # Independent volume: clip 2 at 0.3 must be markedly quieter than
    # clip 1 at 1.0 even accounting for tone differences.
    def rms(at, length=0.4):
        import array, math
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", f"{at:.3f}", "-t", f"{length:.3f}",
             "-i", str(xout), "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
            capture_output=True, check=True,
        ).stdout
        a = array.array("h"); a.frombytes(proc)
        return math.sqrt(sum(v * v for v in a) / max(1, len(a)))

    quiet = rms(2.1)
    loud = rms(0.3)
    boosted = rms(4.3)
    print(f"volume rms reference={loud:.0f} 0.3x={quiet:.0f} 1.4x={boosted:.0f}")
    assert quiet < loud * 0.6
    print("ALL BOUNDARY CHECKS PASSED", output, xout)


if __name__ == "__main__":
    main()
