"""Pydantic models for media, projects and export jobs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Clip(BaseModel):
    id: str
    media_id: str
    # Source range in seconds, inclusive start / exclusive end.
    source_in: float = Field(ge=0)
    source_out: float = Field(gt=0)
    # Overlap crossfade with the *previous* clip, in seconds. 0 means a
    # hard cut. Capped at compute time so it never eats the whole clip.
    transition_in: float = Field(default=0.0, ge=0)
    # Independent per-clip gain multiplier (1 = original volume).
    volume: float = Field(default=1.0, ge=0, le=4.0)

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
    duration: float
    # Effective overlap with the previous clip after clamping.
    transition_in: float = 0.0
    volume: float = 1.0
    source_in: float
    source_out: float
    media_missing: bool
    source_duration: float | None


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
