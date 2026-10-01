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

from .models import (
    MediaInfo,
    Project,
    SpeedSegment,
    Timeline,
    TimelineClip,
    TimelineSegment,
)
from .store import media_exists


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

        segments = resolve_speed_segments(
            clip.source_in,
            clip.source_out,
            clip.speed_segments,
        )
        duration = round(sum(segment.duration for segment in segments), 6)
        # The first clip cannot overlap anything; later clips may only
        # overlap by half of the shorter of the two adjacent clips.
        transition = 0.0 if index == 0 else min(max(0.0, float(clip.transition)), duration * 0.5)
        if clips:
            prev = project.clips[index - 1]
            prev_segments = resolve_speed_segments(
                prev.source_in,
                prev.source_out,
                prev.speed_segments,
            )
            prev_duration = sum(segment.duration for segment in prev_segments)
            transition = min(transition, prev_duration * 0.5)
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
                segments=segments,
            )
        )
        cursor = end if index == 0 else round(start + duration - transition, 6)

    return Timeline(
        duration=round(cursor, 6),
        clips=clips,
        missing_media_ids=sorted(missing),
    )


def resolve_speed_segments(
    source_in: float,
    source_out: float,
    configured: list[SpeedSegment],
) -> list[TimelineSegment]:
    """Resolve source-time speed ranges into output-time intervals.

    The stored speed ranges describe source material only. They are clipped
    to the current trim range; gaps and unspecified material remain at 1x.
    Overlapping ranges are rejected by clip validation, while ranges moved
    outside the current crop are simply ignored.
    """
    if source_out <= source_in:
        return []

    boundaries = [source_in]
    clipped: list[tuple[float, float, float]] = []
    for item in sorted(configured, key=lambda segment: segment.source_in):
        start = max(source_in, item.source_in)
        end = min(source_out, item.source_out)
        if end <= start:
            continue
        clipped.append((start, end, item.rate))
        if start > source_in:
            boundaries.append(start)
        if end < source_out:
            boundaries.append(end)
    boundaries.append(source_out)
    boundaries = sorted(set(round(value, 6) for value in boundaries))

    result: list[TimelineSegment] = []
    output_cursor = 0.0
    for index, boundary_out in enumerate(boundaries[1:]):
        boundary_in = boundaries[index]
        if boundary_out <= boundary_in:
            continue
        rate = 1.0
        for start, end, configured_rate in clipped:
            if start <= boundary_in and boundary_out <= end:
                rate = configured_rate
                break
        source_length = boundary_out - boundary_in
        output_length = source_length / rate
        start = round(output_cursor, 6)
        end = round(output_cursor + output_length, 6)
        result.append(
            TimelineSegment(
                source_in=round(boundary_in, 6),
                source_out=round(boundary_out, 6),
                start=start,
                end=end,
                duration=round(end - start, 6),
                rate=rate,
            )
        )
        output_cursor = end
    return result
