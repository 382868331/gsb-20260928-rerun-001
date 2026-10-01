"""Timeline computation.

Single source of truth shared by the preview API and the exporter.

Each clip's full media duration is ``source_out - source_in``. A clip
with a positive ``transition`` starts fading in that many seconds before
its nominal ``start``: the previous clip is still playing and the two
overlap. The visible ``start`` of clip ``i`` is therefore the running
sum of ``duration - transition`` of every preceding clip, and the
timeline ends at the last clip's end. Trimming a clip or dragging a
transition length shifts every subsequent clip (video and audio
together).
"""

from __future__ import annotations

from .models import MediaInfo, Project, Timeline, TimelineClip, TimelineSpeedSegment
from .store import media_exists


def _effective_speed_segments(clip) -> list[TimelineSpeedSegment]:
    """Return source intervals covering the trimmed clip range.

    Gaps between stored speed changes inherit rate 1. Segments are
    clipped by the media range and never extend outside it.
    """
    source_in = round(max(0.0, float(clip.source_in)), 6)
    source_out = round(max(source_in, float(clip.source_out)), 6)
    ordered = sorted(clip.speed_segments, key=lambda item: (item.source_start, item.source_end))
    pieces: list[TimelineSpeedSegment] = []
    cursor = source_in
    clip_cursor = 0.0
    for segment in ordered:
        start = round(max(segment.source_start, source_in), 6)
        end = round(min(segment.source_end, source_out), 6)
        if end <= start:
            continue
        if start > cursor:
            source_length = round(start - cursor, 6)
            pieces.append(TimelineSpeedSegment(
                clip_start=round(clip_cursor, 6),
                clip_end=round(clip_cursor + source_length, 6),
                source_start=cursor,
                source_end=start,
                rate=1.0,
            ))
            clip_cursor = round(clip_cursor + source_length, 6)
        # Defensive handling for overlapping saved intervals: the later
        # interval wins over the already-covered source range.
        if start < cursor:
            start = cursor
        if end <= start:
            continue
        source_length = round(end - start, 6)
        rendered_length = source_length / max(0.000001, float(segment.rate))
        pieces.append(TimelineSpeedSegment(
            clip_start=round(clip_cursor, 6),
            clip_end=round(clip_cursor + rendered_length, 6),
            source_start=start,
            source_end=end,
            rate=round(max(0.000001, float(segment.rate)), 6),
        ))
        clip_cursor = round(clip_cursor + rendered_length, 6)
        cursor = end
    if cursor < source_out:
        source_length = round(source_out - cursor, 6)
        pieces.append(TimelineSpeedSegment(
            clip_start=round(clip_cursor, 6),
            clip_end=round(clip_cursor + source_length, 6),
            source_start=cursor,
            source_end=source_out,
            rate=1.0,
        ))
    return pieces


def source_time_in_clip(clip: TimelineClip, local_time: float) -> float:
    """Map rendered time inside one clip to the shared A/V source time."""
    time = min(max(0.0, local_time), clip.duration)
    for segment in clip.speed_segments:
        if time <= segment.clip_end + 1e-7 or segment is clip.speed_segments[-1]:
            inside = min(max(0.0, time - segment.clip_start), segment.clip_end - segment.clip_start)
            return round(segment.source_start + inside * segment.rate, 6)
    return clip.source_in


def compute_timeline(project: Project, media_index: dict[str, MediaInfo]) -> Timeline:
    clips: list[TimelineClip] = []
    cursor = 0.0
    missing: set[str] = set()

    for index, clip in enumerate(project.clips):
        media = media_index.get(clip.media_id)
        source_duration = media.duration if media else None
        exists = media is not None and media_exists(clip.media_id)
        if not exists:
            missing.add(clip.media_id)

        speed_pieces = _effective_speed_segments(clip)
        duration = round(
            sum(piece.clip_end - piece.clip_start for piece in speed_pieces), 6
        )
        # The first clip cannot overlap anything; later clips may only
        # overlap by half of the shorter of the two adjacent clips.
        transition = 0.0 if index == 0 else min(max(0.0, float(clip.transition)), duration * 0.5)
        if clips:
            transition = min(transition, clips[-1].duration * 0.5)
        transition = round(transition, 6)

        start = round(cursor, 6)
        transition_start = round(start - transition, 6)
        end = round(start + duration, 6)
        clips.append(
            TimelineClip(
                clip_id=clip.id,
                media_id=clip.media_id,
                start=start,
                end=end,
                transition_start=transition_start,
                transition=transition,
                duration=duration,
                source_in=clip.source_in,
                source_out=clip.source_out,
                volume=max(0.0, min(4.0, float(clip.volume))),
                media_missing=not exists,
                source_duration=source_duration,
                speed_segments=speed_pieces,
            )
        )
        cursor = end if index == 0 else round(start + duration - transition, 6)

    return Timeline(
        duration=round(cursor, 6),
        clips=clips,
        missing_media_ids=sorted(missing),
    )
