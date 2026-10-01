"""Pydantic models for media, projects and export jobs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    # Source-time speed sections. Missing/partial definitions are filled
    # with normal-speed sections when the timeline is computed.
    speed_segments: list["SpeedSegment"] = Field(default_factory=list)

    @field_validator("source_out")
    @classmethod
    def _out_after_in(cls, value, info):
        source_in = info.data.get("source_in")
        if source_in is not None and value <= source_in:
            raise ValueError("source_out must be greater than source_in")
        return value

    @model_validator(mode="after")
    def _speed_segments_do_not_overlap(self):
        cursor = 0.0
        for segment in sorted(self.speed_segments, key=lambda item: item.source_in):
            if segment.source_in < cursor:
                raise ValueError("speed segments must not overlap")
            cursor = segment.source_out
        return self


class SpeedSegment(BaseModel):
    source_in: float = Field(ge=0)
    source_out: float = Field(gt=0)
    rate: float = Field(default=1.0, gt=0, le=8)

    @field_validator("source_out")
    @classmethod
    def _out_after_in(cls, value, info):
        source_in = info.data.get("source_in")
        if source_in is not None and value <= source_in:
            raise ValueError("speed segment source_out must be greater than source_in")
        return value


class ProjectPayload(BaseModel):
    name: str = "未命名工程"
    clips: list[Clip] = Field(default_factory=list)


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
    segments: list["TimelineSegment"] = Field(default_factory=list)


class TimelineSegment(BaseModel):
    source_in: float
    source_out: float
    start: float
    end: float
    duration: float
    rate: float


class Timeline(BaseModel):
    duration: float
    clips: list[TimelineClip]
    missing_media_ids: list[str]


class ExportInfo(BaseModel):
    id: str
    project_id: str
    status: Literal["pending", "running", "done", "failed", "cancelled"]
    progress: float = 0.0
    output_url: str | None = None
    error: str | None = None
    created_at: str
    finished_at: str | None = None
