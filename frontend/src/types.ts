export interface MediaInfo {
  id: string;
  filename: string;
  duration: number;
  width: number | null;
  height: number | null;
  fps: number | null;
  has_audio: boolean;
  is_sample: boolean;
}

export type UploadStatus = "reading" | "done" | "failed";

export interface UploadEntry {
  key: string;
  name: string;
  status: UploadStatus;
  error: string | null;
}

export interface Clip {
  id: string;
  media_id: string;
  source_in: number;
  source_out: number;
  volume: number;
  transition: number;
  speed_segments: SpeedSegment[];
}

export interface SpeedSegment {
  source_start: number;
  source_end: number;
  rate: number;
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
  speed_segments: TimelineSpeedSegment[];
}

export interface TimelineSpeedSegment {
  clip_start: number;
  clip_end: number;
  source_start: number;
  source_end: number;
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

export type PackageStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "cancelled";

export interface PackageInfo {
  id: string;
  project_id: string;
  status: PackageStatus;
  progress: number;
  output_url: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface ImportReport {
  reused: string[];
  imported: string[];
  missing: string[];
  corrupt: string[];
}

export interface ImportResult {
  project: Project;
  report: ImportReport;
}

export type ReplacementMode = "keep_source" | "keep_duration";

export interface ReplacementRequest {
  media_id: string;
  replacement_media_id: string;
  mode: ReplacementMode;
  clip_ids: string[] | null;
}

export interface ClipReplacementPlan {
  clip_id: string;
  selected: boolean;
  status: "ok" | "blocked";
  reasons: string[];
  notes: string[];
  source_in_before: number;
  source_out_before: number;
  source_in_after: number;
  source_out_after: number;
  rendered_before: number;
  rendered_after: number;
  speed_segments_after: SpeedSegment[];
}

export interface ReplacementPreview {
  media_id: string;
  replacement: MediaInfo;
  original: MediaInfo | null;
  mode: ReplacementMode;
  clips: ClipReplacementPlan[];
  timeline_duration_before: number;
  timeline_duration_after: number;
}

export interface ReplacementResult {
  project: Project;
  applied: string[];
  blocked: { clip_id: string; reasons: string[] }[];
}
