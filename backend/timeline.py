"""Shared timeline model.

A project has a single timeline: an ordered list of clips. Each clip
references a media asset and defines a source range [in_point, out_point)
plus optional adjustments (speed, volume, color filter).

Positions on the timeline are *derived* from clip order and durations --
they are never stored. Trimming a clip therefore automatically ripples
every following clip, and the exact same computation is used for the
preview proxy render and the final export, so preview and export agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FPS = 30
WIDTH = 1280
HEIGHT = 720

COLOR_FILTERS = {
    "none": "",
    "grayscale": "hue=s=0",
    "warm": "colorbalance=rs=0.15:gs=0.05:bs=-0.1",
    "cool": "colorbalance=rs=-0.1:gs=0.05:bs=0.15",
    "vivid": "eq=saturation=1.4:contrast=1.1",
}


@dataclass
class Clip:
    id: str
    media_id: str
    in_point: float          # seconds into the source media
    out_point: float         # seconds into the source media (exclusive)
    speed: float = 1.0
    volume: float = 1.0
    color: str = "none"

    @property
    def source_duration(self) -> float:
        return max(0.0, self.out_point - self.in_point)

    @property
    def duration(self) -> float:
        """Duration on the timeline after speed adjustment."""
        if self.speed <= 0:
            return 0.0
        return self.source_duration / self.speed

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Clip":
        return cls(
            id=str(data["id"]),
            media_id=str(data["media_id"]),
            in_point=float(data.get("in_point", 0.0)),
            out_point=float(data.get("out_point", 0.0)),
            speed=float(data.get("speed", 1.0)),
            volume=float(data.get("volume", 1.0)),
            color=str(data.get("color", "none")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "media_id": self.media_id,
            "in_point": round(self.in_point, 4),
            "out_point": round(self.out_point, 4),
            "speed": self.speed,
            "volume": self.volume,
            "color": self.color,
        }


@dataclass
class Timeline:
    clips: list[Clip] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Timeline":
        return cls(clips=[Clip.from_dict(c) for c in data.get("clips", [])])

    def to_dict(self) -> dict[str, Any]:
        return {"clips": [c.to_dict() for c in self.clips]}

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    def layout(self) -> list[dict[str, Any]]:
        """Return per-clip placement on the timeline (start/end seconds)."""
        out = []
        cursor = 0.0
        for clip in self.clips:
            out.append({
                "clip": clip,
                "start": cursor,
                "end": cursor + clip.duration,
            })
            cursor += clip.duration
        return out
