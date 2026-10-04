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

export interface ImportIssue {
  kind: "missing" | "corrupt";
  media_id: string | null;
  filename: string | null;
  sha256: string | null;
  detail: string;
}

export interface ImportResult {
  ok: boolean;
  project_id: string | null;
  project_name: string | null;
  imported_media: MediaInfo[];
  reused_media: MediaInfo[];
  issues: ImportIssue[];
  error: string | null;
}
