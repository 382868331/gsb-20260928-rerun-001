"""Generate the bundled short clips with ffmpeg.

Each sample has a distinct solid colour, a wall-clock text burn-in and a
distinct sine tone, so after an export the boundaries can be verified
both visually (colour changes) and audibly (tone frequencies).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import SAMPLES_DIR
from .media import load_index, register_sample, save_index
from .models import MediaInfo

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
if not Path(FONT).exists():
    FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

SAMPLES = [
    ("sample1.mp4", "样例1-红 440Hz", "0xD83B3B", 440),
    ("sample2.mp4", "样例2-绿 660Hz", "0x2FA84A", 660),
    ("sample3.mp4", "样例3-蓝 880Hz", "0x2F6BD8", 880),
]


def _generate_one(path: Path, label: str, color: str, frequency: int, seconds: int = 4) -> None:
    # A moving vertical bar plus per-sample label and running source
    # timestamp make trimming boundaries unambiguous in any player.
    vf = (
        f"drawbox=x='mod(t,1)*w':y=0:w=6:h=ih:color=white@0.9:t=fill,"
        f"drawtext=fontfile={FONT}:text='{label}  %{{pts\\:hms}}':"
        f"fontcolor=white:fontsize=28:x=(w-text_w)/2:y=(h-text_h)/2:"
        f"box=1:boxcolor=black@0.55"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=640x360:r=30:d={seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration={seconds}:sample_rate=48000",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def ensure_samples() -> dict[str, MediaInfo]:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    result: dict[str, MediaInfo] = {}
    changed = False
    for sample_id, label, color, frequency in SAMPLES:
        path = SAMPLES_DIR / sample_id
        if not path.exists():
            _generate_one(path, label, color, frequency)
            changed = True
        info = register_sample(sample_id)
        if info is not None:
            # Keep a friendly label as the visible filename.
            index = load_index()
            stored = index.get(info.id)
            if stored is not None and stored.filename != label:
                stored.filename = label
                save_index(index)
                info = stored
                changed = True
            result[info.id] = info
    if changed:
        # Re-read so callers get the labelled entries.
        result = {key: value for key, value in load_index().items() if value.is_sample}
    return result
