export interface MediaItem {
  id: string;
  filename: string;
  original_name: string;
  duration: number;
  width: number;
  height: number;
  has_audio: boolean;
  builtin?: boolean;
  missing?: boolean;
}

export interface Clip {
  id: string;
  media_id: string;
  in_point: number;
  out_point: number;
  speed: number;
  volume: number;
  color: string;
}

export interface Timeline {
  clips: Clip[];
}

export interface ProjectSummary {
  name: string;
  updated_at: number;
  duration: number;
  clip_count: number;
}

export interface Project {
  name: string;
  duration: number;
  timeline: Timeline;
  missing_media?: string[];
}

export interface RenderJob {
  id: string;
  kind: string;
  status: "running" | "done" | "failed";
  progress: number;
  output: string | null;
  error: string | null;
  project_name: string;
}

export function clipDuration(c: Clip): number {
  return c.speed > 0 ? (c.out_point - c.in_point) / c.speed : 0;
}

export interface LayoutItem {
  clip: Clip;
  start: number;
  end: number;
}

export function layout(tl: Timeline): LayoutItem[] {
  let cursor = 0;
  return tl.clips.map((clip) => {
    const start = cursor;
    const end = cursor + clipDuration(clip);
    cursor = end;
    return { clip, start, end };
  });
}

export function totalDuration(tl: Timeline): number {
  return tl.clips.reduce((s, c) => s + clipDuration(c), 0);
}

export const COLOR_OPTIONS = [
  ["none", "原色"],
  ["grayscale", "黑白"],
  ["warm", "暖调"],
  ["cool", "冷调"],
  ["vivid", "鲜艳"],
] as const;
