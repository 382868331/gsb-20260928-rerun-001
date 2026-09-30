"""Timeline computation.

This is the single source of truth shared by the preview API and the
exporter: clip order defines placement, each clip's duration is
``source_out - source_in``. Each clip may crossfade into the previous
one by ``transition_in`` seconds: the clip starts that early, overlapping
its predecessor on both picture and sound, shortening the total
timeline. Trimming a clip or dragging a transition therefore shifts all
subsequent clips (video and audio together -- they share one track).
"""

from __future__ import annotations

from .models import MediaInfo, Project, Timeline, TimelineClip
from .store import media_exists

# Below half an output frame an overlap is indistinguishable from a hard
# cut (and ffmpeg's xfade needs >= ~1 frame to produce a full-length
# result), so such values are treated as a plain cut.
FRAME = 1.0 / 30.0
EPS = FRAME / 2.0


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
        requested = round(max(0.0, clip.transition_in), 6)
        if requested < EPS:
            requested = 0.0
        if index == 0:
            transition = 0.0
        else:
            prev = clips[index - 1]
            # A transition cannot consume either overlapping clip and two
            # neighbouring transitions may not overlap each other.
            room = min(
                max(0.0, duration - EPS),
                max(0.0, prev.duration - prev.transition_in - EPS),
            )
            transition = round(min(requested, room), 6)

        start = round(cursor - transition, 6)
        end = round(start + duration, 6)
        clips.append(
            TimelineClip(
                clip_id=clip.id,
                media_id=clip.media_id,
                start=start,
                end=end,
                duration=duration,
                transition_in=transition,
                volume=round(min(max(clip.volume, 0.0), 4.0), 6),
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
