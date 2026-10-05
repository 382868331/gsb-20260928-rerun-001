"""Offline media replacement for projects with missing media.

When a project is restored without its media (or media was lost), the
receiver can point each missing media id at a replacement file already
in the library. A replacement is matched by content hash only: a file
that merely shares the original's name is imported as a new media entry
and is never silently treated as the original.

Preview computes, per clip referencing the missing media, how the
replacement's duration / frame rate / audio tracks interact with the
clip's trim and speed plan, in one of two modes:

- keep_source: source timestamps (in/out points and speed segment
  boundaries) stay exactly where they were. This requires the
  replacement to cover the clip's source_out.
- keep_duration: when the replacement is shorter than the original
  media, source timestamps are scaled down by the same factor the
  playback rates are scaled down with, so every clip's rendered
  duration -- and therefore the whole timeline -- stays identical.

A clip is blocked from confirming when the replacement is too short
(keep_source) or lacks an audio track the clip needs; other,
independent clips can still be confirmed. Clips that share the media
but are not selected keep their old reference untouched.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from . import media as media_service
from .models import MediaInfo, Project, SpeedSegment
from .store import media_exists
from .timeline import _effective_speed_segments

EPS = 1e-6


class ReplacementRequest(BaseModel):
    media_id: str
    replacement_media_id: str
    mode: Literal["keep_source", "keep_duration"] = "keep_source"
    # None means every clip referencing media_id.
    clip_ids: list[str] | None = None


class MediaSummary(BaseModel):
    id: str
    filename: str
    duration: float
    fps: float | None = None
    has_audio: bool


class ReplacementClipPlan(BaseModel):
    clip_id: str
    selected: bool
    blocked: bool
    reasons: list[str]
    source_in: float
    source_out: float
    speed_segments: list[SpeedSegment]
    rendered_before: float
    rendered_after: float


class ReplacementPlan(BaseModel):
    media_id: str
    replacement_media_id: str
    mode: str
    # True only when the replacement's content hash matches the
    # original media's recorded hash -- never inferred from filenames.
    identical_to_original: bool
    original: MediaSummary | None
    replacement: MediaSummary
    clips: list[ReplacementClipPlan]


class ReplacementInvalid(RuntimeError):
    pass


def _summary(info: MediaInfo) -> MediaSummary:
    return MediaSummary(
        id=info.id,
        filename=info.filename,
        duration=info.duration,
        fps=info.fps,
        has_audio=info.has_audio,
    )


def _rendered_duration(clip) -> float:
    pieces = _effective_speed_segments(clip)
    return round(sum(piece.clip_end - piece.clip_start for piece in pieces), 6)


def _scale_clip(clip, factor: float):
    """Scale source timestamps and rates by the given factor.

    Source positions shrink by the factor; every rendered piece's rate
    shrinks by the same factor, so each piece's rendered length -- and
    the clip's total rendered duration -- stays exactly the same. The
    full coverage (gaps included) is baked into explicit speed segments
    because the gaps would otherwise play at rate 1 and shrink.
    """
    pieces = _effective_speed_segments(clip)
    scaled = [
        SpeedSegment(
            source_start=round(piece.source_start * factor, 6),
            source_end=round(piece.source_end * factor, 6),
            rate=max(0.000001, round(piece.rate * factor, 6)),
        )
        for piece in pieces
    ]
    return (
        round(float(clip.source_in) * factor, 6),
        round(float(clip.source_out) * factor, 6),
        scaled,
    )


def build_plan(project: Project, request: ReplacementRequest) -> ReplacementPlan:
    index = media_service.load_index()
    replacement = index.get(request.replacement_media_id)
    if replacement is None or not media_exists(request.replacement_media_id):
        raise ReplacementInvalid("替代素材不存在或文件缺失")
    original = index.get(request.media_id)

    referencing = [clip for clip in project.clips if clip.media_id == request.media_id]
    if not referencing:
        raise ReplacementInvalid("当前工程没有引用该素材的片段")
    selected_ids = (
        {clip.id for clip in referencing}
        if request.clip_ids is None
        else set(request.clip_ids)
    )

    # Original duration comes from the library record when present;
    # offline restores may have lost it, so fall back to the furthest
    # source timestamp any referencing clip uses.
    if original is not None:
        original_duration = float(original.duration)
    else:
        original_duration = max(float(clip.source_out) for clip in referencing)
    original_has_audio = original.has_audio if original is not None else True

    identical = bool(
        original is not None
        and original.sha256
        and replacement.sha256
        and original.sha256 == replacement.sha256
    )

    factor = 1.0
    if request.mode == "keep_duration" and original_duration > EPS:
        factor = min(1.0, float(replacement.duration) / original_duration)

    plans: list[ReplacementClipPlan] = []
    for clip in project.clips:
        if clip.media_id != request.media_id:
            continue
        selected = clip.id in selected_ids
        reasons: list[str] = []
        source_in = float(clip.source_in)
        source_out = float(clip.source_out)
        speed_segments = list(clip.speed_segments)

        if selected:
            needs_audio = float(clip.volume) > 0 and original_has_audio
            if needs_audio and not replacement.has_audio:
                reasons.append("替代素材缺少该片段所需的音轨")
            if request.mode == "keep_source":
                if float(replacement.duration) + EPS < source_out:
                    reasons.append(
                        "替代素材时长不足（需要 %.2fs，仅 %.2fs）"
                        % (source_out, float(replacement.duration))
                    )
            elif factor < 1.0:
                source_in, source_out, speed_segments = _scale_clip(clip, factor)

        changed = clip.model_copy(
            update={
                "source_in": source_in,
                "source_out": source_out,
                "speed_segments": speed_segments,
            }
        )
        plans.append(
            ReplacementClipPlan(
                clip_id=clip.id,
                selected=selected,
                blocked=bool(reasons),
                reasons=reasons,
                source_in=source_in,
                source_out=source_out,
                speed_segments=speed_segments,
                rendered_before=_rendered_duration(clip),
                rendered_after=_rendered_duration(changed),
            )
        )

    return ReplacementPlan(
        media_id=request.media_id,
        replacement_media_id=request.replacement_media_id,
        mode=request.mode,
        identical_to_original=identical,
        original=_summary(original) if original is not None else None,
        replacement=_summary(replacement),
        clips=plans,
    )


def apply_replacement(project: Project, request: ReplacementRequest) -> Project:
    """Apply a confirmed replacement as one real, saved edit.

    Re-validates the plan: any selected clip that is blocked aborts the
    whole apply with ReplacementInvalid, so a confirm can never smuggle
    in a clip the preview rejected. Unselected clips keep their old
    media reference untouched.
    """
    plan = build_plan(project, request)
    blocked = [entry for entry in plan.clips if entry.selected and entry.blocked]
    if blocked:
        details = "; ".join(
            f"片段 {entry.clip_id}: " + ", ".join(entry.reasons) for entry in blocked
        )
        raise ReplacementInvalid("以下片段无法替换：" + details)
    by_id = {entry.clip_id: entry for entry in plan.clips}
    for clip in project.clips:
        entry = by_id.get(clip.id)
        if entry is None or not entry.selected:
            continue
        clip.media_id = request.replacement_media_id
        clip.source_in = entry.source_in
        clip.source_out = entry.source_out
        clip.speed_segments = entry.speed_segments
    return project
