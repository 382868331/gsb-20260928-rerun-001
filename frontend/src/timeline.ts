import type { Clip, MediaInfo, SpeedSegment, Timeline, TimelineClip, TimelineSegment } from "./types";

const EPS = 1e-7;

export function round6(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

export function resolveSpeedSegments(
  sourceIn: number,
  sourceOut: number,
  configured: SpeedSegment[] = [],
): TimelineSegment[] {
  if (sourceOut <= sourceIn) return [];
  const clipped = configured
    .map((segment) => ({
      start: Math.max(sourceIn, segment.source_in),
      end: Math.min(sourceOut, segment.source_out),
      rate: segment.rate,
    }))
    .filter((segment) => segment.end > segment.start)
    .sort((a, b) => a.start - b.start);
  const boundaries = new Set<number>([sourceIn, sourceOut]);
  for (const segment of clipped) {
    if (segment.start > sourceIn) boundaries.add(segment.start);
    if (segment.end < sourceOut) boundaries.add(segment.end);
  }
  const ordered = [...boundaries].sort((a, b) => a - b);
  let cursor = 0;
  return ordered.slice(1).flatMap<TimelineSegment>((sourceBoundaryOut, index) => {
    const sourceBoundaryIn = ordered[index];
    if (sourceBoundaryOut <= sourceBoundaryIn) return [];
    const match = clipped.find(
      (segment) => segment.start <= sourceBoundaryIn && sourceBoundaryOut <= segment.end,
    );
    const rate = match?.rate ?? 1;
    const duration = (sourceBoundaryOut - sourceBoundaryIn) / rate;
    const start = cursor;
    const end = cursor + duration;
    cursor = end;
    return [{
      source_in: round6(sourceBoundaryIn),
      source_out: round6(sourceBoundaryOut),
      start: round6(start),
      end: round6(end),
      duration: round6(duration),
      rate,
    }];
  });
}

export function clipTimelineDuration(clip: Clip): number {
  return round6(
    resolveSpeedSegments(clip.source_in, clip.source_out, clip.speed_segments)
      .reduce((total, segment) => total + segment.duration, 0),
  );
}

export function sourceTimeAt(clip: TimelineClip, localTime: number): { time: number; rate: number } {
  const target = Math.max(0, Math.min(clip.duration, localTime));
  const segment = clip.segments.find(
    (item) => target + EPS >= item.start && target < item.end,
  ) ?? clip.segments[clip.segments.length - 1];
  if (!segment) return { time: clip.source_in, rate: 1 };
  return {
    time: segment.source_in + Math.max(0, target - segment.start) * segment.rate,
    rate: segment.rate,
  };
}

export function normalizeClip(clip: Clip): Clip {
  return { ...clip, speed_segments: normalizeSpeedSegments(clip.speed_segments ?? []) };
}

export function normalizeSpeedSegments(segments: SpeedSegment[]): SpeedSegment[] {
  const sorted = segments
    .map((segment) => ({
      source_in: round6(Math.max(0, segment.source_in)),
      source_out: round6(Math.max(0.001, segment.source_out)),
      rate: Math.max(0.1, Math.min(8, segment.rate)),
    }))
    .filter((segment) => segment.source_out > segment.source_in)
    .sort((a, b) => a.source_in - b.source_in);
  const result: SpeedSegment[] = [];
  for (const segment of sorted) {
    const previous = result[result.length - 1];
    if (!previous || segment.source_in >= previous.source_out - EPS) {
      result.push(segment);
    } else {
      previous.source_out = Math.max(previous.source_out, segment.source_out);
    }
  }
  return result;
}

export function computeLocalTimeline(
  clips: Clip[],
  media: Record<string, MediaInfo>,
): Timeline {
  let cursor = 0;
  const missing = new Set<string>();
  const result: TimelineClip[] = [];
  clips.forEach((rawClip, index) => {
    const clip = normalizeClip(rawClip);
    const item = media[clip.media_id];
    if (!item) missing.add(clip.media_id);
    const segments = resolveSpeedSegments(clip.source_in, clip.source_out, clip.speed_segments);
    const duration = round6(segments.reduce((total, segment) => total + segment.duration, 0));
    let transition = index === 0 ? 0 : Math.min(Math.max(0, clip.transition || 0), duration * 0.5);
    if (index > 0) {
      transition = Math.min(transition, clipTimelineDuration(clips[index - 1]) * 0.5);
    }
    transition = round6(transition);
    const start = round6(cursor);
    const end = round6(start + duration);
    result.push({
      clip_id: clip.id,
      media_id: clip.media_id,
      start,
      end,
      transition_start: round6(start - transition),
      transition,
      duration,
      source_in: clip.source_in,
      source_out: clip.source_out,
      volume: Math.max(0, Math.min(4, clip.volume ?? 1)),
      media_missing: !item,
      source_duration: item?.duration ?? null,
      segments,
    });
    cursor = index === 0 ? end : round6(start + duration - transition);
  });
  return { duration: round6(cursor), clips: result, missing_media_ids: [...missing].sort() };
}
