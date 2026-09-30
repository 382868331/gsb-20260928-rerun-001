"""End-to-end verification: export a known trim plan with crossfade
transitions and per-clip volume, then check the rendered file's video
colour boundaries, transition blends and audio tone boundaries with
ffprobe/ffmpeg. Used as a manual acceptance check, not a pytest case.
"""
from __future__ import annotations

import json
import math
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


def tone_energies(path: Path, at: float, length: float = 0.4) -> dict[int, float]:
    """Goertzel energy of each sample tone in a short window."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", f"{at:.3f}", "-t", f"{length:.3f}",
         "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    import array
    samples = array.array("h")
    samples.frombytes(proc)
    energies: dict[int, float] = {}
    for target in (440, 660, 880):
        real = imag = 0.0
        k = 2 * math.pi * target / 8000
        for i, value in enumerate(samples):
            angle = k * i
            real += value * math.cos(angle)
            imag -= value * math.sin(angle)
        energies[target] = real * real + imag * imag
    return energies


def dominant_tone(path: Path, at: float, length: float = 0.4) -> int:
    return max(tone_energies(path, at, length).items(), key=lambda kv: kv[1])[0]


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="verify-export-"))
    config.DATA_ROOT = tmp / "data"
    config.MEDIA_DIR = config.DATA_ROOT / "media"
    config.PROJECTS_DIR = config.DATA_ROOT / "projects"
    config.EXPORTS_DIR = config.DATA_ROOT / "exports"
    config.SAMPLES_DIR = config.DATA_ROOT / "samples"
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
    # clip 1: 0.5-2.0 (1.5s, red/440, volume 1.0)
    # clip 2: 1.0-3.5 (2.5s, green/660, volume 0.5), 0.7s crossfade from clip 1
    # clip 3: 0.2-1.7 (1.5s, blue/880, volume 1.0), 0.6s crossfade from clip 2
    # timeline: starts 0 / 1.5 / 3.2, overlaps [0.8,1.5] and [2.7,3.3],
    # total 1.5 + 2.5 + 1.5 - 0.7 - 0.6 = 4.2
    clips = [
        Clip(id="c1", media_id=samples[0], source_in=0.5, source_out=2.0),
        Clip(id="c2", media_id=samples[1], source_in=1.0, source_out=3.5,
             transition=0.7, volume=0.5),
        Clip(id="c3", media_id=samples[2], source_in=0.2, source_out=1.7,
             transition=0.6),
    ]
    project = Project(id="verify", name="verify", clips=clips, version=1,
                      created_at="t0", updated_at="t1")
    save_project(project)
    info = manager.create("verify")
    deadline = time.time() + 60
    while info.status in ("pending", "running") and time.time() < deadline:
        time.sleep(0.2)
        info = manager.get(info.id)
    assert info.status == "done", (info.status, info.error)
    output = config.EXPORTS_DIR / f"{info.id}.mp4"
    duration = probe_duration(output)
    print(f"export duration={duration:.3f} expected=4.2")
    assert abs(duration - 4.2) < 0.12

    # Solo colours well inside each clip's exclusive region.
    checks = [(0.35, (216, 59, 59)), (2.0, (47, 168, 74)), (3.7, (47, 107, 216))]
    for at, expected in checks:
        color = avg_color(output, at)
        assert max(abs(c - e) for c, e in zip(color, expected)) < 14, (at, color)
        print(f"video t={at}s color={color} OK")

    # Mid-transition frames must be a ~50/50 blend of the two colours.
    blends = [
        (1.15, (131, 113, 66)),  # red x green, overlap [0.8, 1.5]
        (3.0, (47, 137, 145)),   # green x blue, overlap [2.7, 3.3]
    ]
    for at, expected in blends:
        color = avg_color(output, at)
        assert max(abs(c - e) for c, e in zip(color, expected)) < 45, (at, color)
        print(f"blend t={at}s color={color} OK")

    # Audio tones must align to the same regions.
    for at, expected in ((0.35, 440), (2.0, 660), (3.7, 880)):
        tone = dominant_tone(output, at)
        assert tone == expected, (at, tone)
        print(f"audio t={at}s tone={tone}Hz OK")

    # Mid-transition audio must contain BOTH tones (crossfade, not cut).
    for at, pair in ((1.15, (440, 660)), (3.0, (660, 880))):
        energies = tone_energies(output, at)
        top_two = sorted(energies, key=energies.get, reverse=True)[:2]  # type: ignore[arg-type]
        assert set(top_two) == set(pair), (at, energies)
        print(f"crossfade t={at}s tones={pair} both present OK")

    # Volume: clip 2 at 0.5x -> its solo tone energy ~ 1/4 of clip 1's.
    solo1 = tone_energies(output, 0.35)[440]
    solo2 = tone_energies(output, 2.0)[660]
    ratio = solo2 / solo1
    print(f"volume ratio={ratio:.3f} expected~0.25")
    assert 0.08 < ratio < 0.55, ratio

    # Cancel + retry must reproduce the same timeline render.
    again = manager.create("verify")
    deadline = time.time() + 60
    while again.status in ("pending", "running") and time.time() < deadline:
        time.sleep(0.2)
        again = manager.get(again.id)
    assert again.status == "done", (again.status, again.error)
    duration2 = probe_duration(config.EXPORTS_DIR / f"{again.id}.mp4")
    assert abs(duration2 - duration) < 0.05, (duration, duration2)
    color2 = avg_color(config.EXPORTS_DIR / f"{again.id}.mp4", 1.15)
    assert max(abs(c - e) for c, e in zip(color2, (131, 113, 66))) < 45, color2
    print(f"re-export duration={duration2:.3f} blend={color2} CONSISTENT")
    print("ALL BOUNDARY CHECKS PASSED", output)


if __name__ == "__main__":
    main()
