export interface MediaInfo {
  id: string;
  filename: string;
  duration: number;
  width: number | null;
  height: number | null;
  has_audio: boolean;
  is_sample: boolean;
  sha256?: string | null;
}

export interface SpeedSegment {
  source_in: number;
  source_out: number;
  rate: number;
}

export interface Clip {
  id: string;
  media_id: string;
  source_in: number;
  source_out: number;
  volume: number;
  transition: number;
  speed_segments?: SpeedSegment[];
}

export interface Project {
  id: string;
  name: string;
  clips: Clip[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface TimelineClip {
  clip_id: string;
  media_id: string;
  start: number;
  end: number;
  transition_start: number;
  transition: number;
  duration: number;
  source_in: number;
  source_out: number;
  volume: number;
  media_missing: boolean;
  source_duration: number | null;
  segments: TimelineSegment[];
}

export interface TimelineSegment {
  source_in: number;
  source_out: number;
  start: number;
  end: number;
  duration: number;
  rate: number;
}

export interface Timeline {
  duration: number;
  clips: TimelineClip[];
  missing_media_ids: string[];
}

export type ExportStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "cancelled";

export interface ExportInfo {
  id: string;
  project_id: string;
  status: ExportStatus;
  progress: number;
  output_url: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}
