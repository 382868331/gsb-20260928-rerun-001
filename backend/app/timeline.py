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

from .models import MediaInfo, Project, Timeline, TimelineClip
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

        duration = max(0.0, round(clip.source_out - clip.source_in, 6))
        # The first clip cannot overlap anything; later clips may only
        # overlap by half of the shorter of the two adjacent clips.
        transition = 0.0 if index == 0 else min(max(0.0, float(clip.transition)), duration * 0.5)
        if clips:
            prev = project.clips[index - 1]
            prev_duration = max(0.0, prev.source_out - prev.source_in)
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
            )
        )
        cursor = end if index == 0 else round(start + duration - transition, 6)

    return Timeline(
        duration=round(cursor, 6),
        clips=clips,
        missing_media_ids=sorted(missing),
    )
