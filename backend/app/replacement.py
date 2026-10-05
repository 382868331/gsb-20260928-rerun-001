"""Media replacement for projects with missing (or swapped) sources.

The recipient of an offline project package can point clips at a
replacement file already in the library. Replacement is matched by
media id / content hash only: a file that merely shares the original
name is never treated as the original media.

For every affected clip the plan is computed individually, because
trim ranges, speed segments and audio needs differ per clip even when
they share one source media:

- keep_source keeps every source timestamp (trim and speed segment
  boundaries) and every playback rate. The replacement must cover the
  same source range, otherwise the clip is blocked.
- keep_duration remaps the source range proportionally into the
  replacement's duration and scales playback rates by the same factor,
  so the rendered timeline duration stays exactly the same.

A clip is blocked (and left untouched) when the replacement is too
short for its trim, lacks the audio track the clip needs, or the
remapped rate would leave the supported range. Blocking one clip never
stops independent clips from being confirmed. Confirming applies the
ok clips as a real project edit (the frontend records it in its
undoable edit history).
"""

from __future__ import annotations

from . import media as media_service
from .models import (
    BlockedClip,
    Clip,
    ClipReplacementPlan,
    MediaInfo,
    Project,
    ReplacementPreview,
    ReplacementRequest,
    SpeedSegment,
)
from .timeline import _effective_speed_segments, compute_timeline

EPS = 1e-6
MAX_RATE = 8.0


def _round(value: float) -> float:
    return round(value, 6)


def _rendered_duration(clip: Clip) -> float:
    pieces = _effective_speed_segments(clip)
    return _round(sum(piece.clip_end - piece.clip_start for piece in pieces))


def _plan_clip(
    clip: Clip,
    selected: bool,
    original: MediaInfo | None,
    replacement: MediaInfo,
    mode: str,
) -> tuple[ClipReplacementPlan, Clip | None]:
    reasons: list[str] = []
    notes: list[str] = []
    rendered_before = _rendered_duration(clip)

    needs_audio = clip.volume > 0 and (original is None or original.has_audio)
    if needs_audio and not replacement.has_audio:
        reasons.append("替代素材缺少该片段所需的音轨")

    new_in = float(clip.source_in)
    new_out = float(clip.source_out)
    new_segments = [segment.model_copy() for segment in clip.speed_segments]

    if mode == "keep_source":
        if replacement.duration + EPS < float(clip.source_out):
            reasons.append(
                f"替代素材时长不足：片段需要源时长 {clip.source_out:.3f}s，"
                f"替代素材仅 {replacement.duration:.3f}s"
            )
        if original is not None and abs(original.duration - replacement.duration) > 0.05:
            notes.append(
                f"源时长 {original.duration:.3f}s → {replacement.duration:.3f}s，"
                "源时间位置保持不变"
            )
    else:  # keep_duration
        if original is None or original.duration <= 0:
            reasons.append("原素材信息缺失，无法按成片时长等比重映射")
        else:
            scale = replacement.duration / original.duration
            new_in = _round(float(clip.source_in) * scale)
            new_out = _round(float(clip.source_out) * scale)
            new_out = min(new_out, _round(replacement.duration))
            if new_out <= new_in:
                reasons.append("重映射后片段时长为零，无法保持成片时长")
            else:
                # Scale the *effective* full-coverage segments (including
                # the implicit 1x gaps), so every rendered sub-interval
                # keeps its exact length: len*k / (rate*k) == len/rate.
                remapped = []
                for piece in _effective_speed_segments(clip):
                    rate = _round(float(piece.rate) * scale)
                    if rate <= 0 or rate > MAX_RATE:
                        reasons.append(
                            f"重映射后变速 {rate:.3f}x 超出允许范围，该片段无法保持成片时长"
                        )
                        break
                    remapped.append(
                        SpeedSegment(
                            source_start=_round(float(piece.source_start) * scale),
                            source_end=_round(float(piece.source_end) * scale),
                            rate=rate,
                        )
                    )
                else:
                    new_segments = remapped
            if not reasons:
                notes.append(
                    f"源时长 {original.duration:.3f}s → {replacement.duration:.3f}s，"
                    f"源位置与变速按比例 {scale:.4f} 重映射，成片时长不变"
                )

    if original is not None:
        if original.fps and replacement.fps and abs(original.fps - replacement.fps) > 0.01:
            notes.append(
                f"帧率 {original.fps:g}fps → {replacement.fps:g}fps，画面采样密度变化"
            )
        if original.has_audio != replacement.has_audio:
            notes.append(
                "音轨差异：原素材"
                + ("有声" if original.has_audio else "无声")
                + "，替代素材"
                + ("有声" if replacement.has_audio else "无声")
            )

    new_clip: Clip | None = None
    rendered_after = rendered_before
    if not reasons:
        new_clip = clip.model_copy(deep=True)
        new_clip.media_id = replacement.id
        new_clip.source_in = new_in
        new_clip.source_out = new_out
        new_clip.speed_segments = new_segments
        rendered_after = _rendered_duration(new_clip)

    plan = ClipReplacementPlan(
        clip_id=clip.id,
        selected=selected,
        status="blocked" if reasons else "ok",
        reasons=reasons,
        notes=notes,
        source_in_before=float(clip.source_in),
        source_out_before=float(clip.source_out),
        source_in_after=new_in,
        source_out_after=new_out,
        rendered_before=rendered_before,
        rendered_after=rendered_after,
        speed_segments_after=new_segments,
    )
    return plan, new_clip


def _build_plans(
    project: Project, request: ReplacementRequest
) -> tuple[list[ClipReplacementPlan], dict[str, Clip], MediaInfo, MediaInfo | None]:
    index = media_service.load_index()
    replacement = index.get(request.replacement_media_id)
    if replacement is None:
        raise ValueError("替代素材不在素材库中，请先导入")
    original = index.get(request.media_id)
    selected_ids = set(request.clip_ids) if request.clip_ids is not None else None

    plans: list[ClipReplacementPlan] = []
    replacements: dict[str, Clip] = {}
    for clip in project.clips:
        if clip.media_id != request.media_id:
            continue
        selected = selected_ids is None or clip.id in selected_ids
        plan, new_clip = _plan_clip(clip, selected, original, replacement, request.mode)
        if not selected:
            # Unselected clips keep their current reference untouched.
            plan.status = "ok"
            plan.reasons = []
            plan.notes = ["未选择，保持原素材引用不变"]
            new_clip = None
        plans.append(plan)
        if new_clip is not None:
            replacements[clip.id] = new_clip
    return plans, replacements, replacement, original


def preview_replacement(project: Project, request: ReplacementRequest) -> ReplacementPreview:
    plans, replacements, replacement, original = _build_plans(project, request)
    before = compute_timeline(project, media_service.load_index())
    after_clips = [replacements.get(clip.id, clip) for clip in project.clips]
    after = compute_timeline(
        project.model_copy(update={"clips": after_clips}), media_service.load_index()
    )
    return ReplacementPreview(
        media_id=request.media_id,
        replacement=replacement,
        original=original,
        mode=request.mode,
        clips=plans,
        timeline_duration_before=before.duration,
        timeline_duration_after=after.duration,
    )


def apply_replacement(
    project: Project, request: ReplacementRequest
) -> tuple[Project, list[str], list[BlockedClip]]:
    """Apply every ok selected clip; blocked/unselected clips stay as-is."""
    plans, replacements, _replacement, _original = _build_plans(project, request)
    blocked = [
        BlockedClip(clip_id=plan.clip_id, reasons=plan.reasons)
        for plan in plans
        if plan.status == "blocked"
    ]
    applied = [clip_id for clip_id in (plan.clip_id for plan in plans) if clip_id in replacements]
    new_clips = [replacements.get(clip.id, clip) for clip in project.clips]
    updated = project.model_copy(update={"clips": new_clips})
    return updated, applied, blocked
