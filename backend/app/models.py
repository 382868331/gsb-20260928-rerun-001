"""Pydantic models for media, projects and export jobs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SpeedSegment(BaseModel):
    # Boundaries are source-media timestamps. They intentionally do not
    # move when a clip's in/out points are trimmed around them.
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    rate: float = Field(gt=0, le=8)

    @field_validator("source_end")
    @classmethod
    def _end_after_start(cls, value, info):
        start = info.data.get("source_start")
        if start is not None and value <= start:
            raise ValueError("speed segment source_end must be greater than source_start")
        return value


class Clip(BaseModel):
    id: str
    media_id: str
    # Source range in seconds, inclusive start / exclusive end.
    source_in: float = Field(ge=0)
    source_out: float = Field(gt=0)
    # Independent playback gain for this clip (0 = muted).
    volume: float = Field(default=1.0, ge=0, le=4)
    # Length of the crossfade overlap *into* this clip from the
    # previous one. 0 means a hard cut; clips overlap on the timeline
    # while video/audio crossfade during the overlap.
    transition: float = Field(default=0.0, ge=0)
    # Non-overlapping source-time intervals with playback rates. Source
    # ranges outside these intervals play at normal speed.
    speed_segments: list[SpeedSegment] = Field(default_factory=list)

    @field_validator("source_out")
    @classmethod
    def _out_after_in(cls, value, info):
        source_in = info.data.get("source_in")
        if source_in is not None and value <= source_in:
            raise ValueError("source_out must be greater than source_in")
        return value


class ProjectPayload(BaseModel):
    name: str = "未命名工程"
    clips: list[Clip] = Field(default_factory=list)


class ExportPayload(BaseModel):
    start: float = Field(default=0.0, ge=0)
    end: float | None = Field(default=None, gt=0)


class Project(BaseModel):
    id: str
    name: str
    clips: list[Clip] = Field(default_factory=list)
    version: int = 0
    created_at: str
    updated_at: str


class MediaInfo(BaseModel):
    id: str
    filename: str
    duration: float
    width: int | None = None
    height: int | None = None
    has_audio: bool
    is_sample: bool = False
    sha256: str | None = None


class TimelineSpeedSegment(BaseModel):
    # Start relative to the clip's rendered beginning (including the
    # part used by its incoming transition).
    clip_start: float
    clip_end: float
    source_start: float
    source_end: float
    rate: float


class TimelineClip(BaseModel):
    clip_id: str
    media_id: str
    start: float  # position on the unified timeline
    end: float
    # This clip's media also plays (fading in) before ``start``; during
    # [start, start + transition] it is crossfaded with the previous clip.
    transition_start: float
    transition: float
    duration: float
    source_in: float
    source_out: float
    volume: float
    media_missing: bool
    source_duration: float | None
    speed_segments: list[TimelineSpeedSegment]


class Timeline(BaseModel):
    duration: float
    clips: list[TimelineClip]
    missing_media_ids: list[str]


class PackageInfo(BaseModel):
    id: str
    project_id: str
    status: Literal["pending", "running", "done", "failed", "cancelled"]
    progress: float = 0.0
    output_url: str | None = None
    error: str | None = None
    created_at: str
    finished_at: str | None = None


class ImportReport(BaseModel):
    reused: list[str] = Field(default_factory=list)
    imported: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    corrupt: list[str] = Field(default_factory=list)


class ImportResult(BaseModel):
    project: Project
    report: ImportReport


class ExportInfo(BaseModel):
    id: str
    project_id: str
    status: Literal["pending", "running", "done", "failed", "cancelled"]
    progress: float = 0.0
    output_url: str | None = None
    error: str | None = None
    created_at: str
    finished_at: str | None = None
