"""Timeline computation.

This is the single source of truth shared by the preview API and the
exporter: clip order defines placement, each clip's duration is
``source_out - source_in`` and every later clip is positioned at the
exact sum of preceding durations. Trimming a clip therefore shifts all
subsequent clips (video and audio together -- they share one track).
"""

from __future__ import annotations

from .models import MediaInfo, Project, Timeline, TimelineClip
from .store import media_exists


def compute_timeline(project: Project, media_index: dict[str, MediaInfo]) -> Timeline:
    clips: list[TimelineClip] = []
    cursor = 0.0
    missing: set[str] = set()

    for clip in project.clips:
        media = media_index.get(clip.media_id)
        source_duration = media.duration if media else None
        exists = media is not None and media_exists(clip.media_id)
        if not exists:
            missing.add(clip.media_id)

        start = round(cursor, 6)
        duration = max(0.0, round(clip.source_out - clip.source_in, 6))
        end = round(cursor + duration, 6)
        clips.append(
            TimelineClip(
                clip_id=clip.id,
                media_id=clip.media_id,
                start=start,
                end=end,
                duration=duration,
                source_in=clip.source_in,
                source_out=clip.source_out,
                media_missing=not exists,
                source_duration=source_duration,
            )
        )
        cursor = end

    return Timeline(
        duration=round(cursor, 6),
        clips=clips,
        missing_media_ids=sorted(missing),
    )
