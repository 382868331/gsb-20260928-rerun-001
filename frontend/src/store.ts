import { create } from "zustand";
import { api } from "./api";
import type {
  Clip,
  ExportInfo,
  MediaInfo,
  Project,
  Timeline,
  TimelineClip,
  TimelineSpeedSegment,
} from "./types";

function makeClipId(): string {
  return `clip-${Math.random().toString(36).slice(2, 10)}`;
}

function round6(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

function effectiveSpeedSegments(clip: Clip): TimelineSpeedSegment[] {
  const sourceIn = Math.max(0, clip.source_in);
  const sourceOut = Math.max(sourceIn, clip.source_out);
  const ordered = [...clip.speed_segments].sort((a, b) =>
    a.source_start === b.source_start
      ? a.source_end - b.source_end
      : a.source_start - b.source_start,
  );
  const pieces: TimelineSpeedSegment[] = [];
  let sourceCursor = sourceIn;
  let clipCursor = 0;
  for (const segment of ordered) {
    let start = Math.max(segment.source_start, sourceIn);
    const end = Math.min(segment.source_end, sourceOut);
    if (end <= start) continue;
    if (start > sourceCursor) {
      const sourceLength = start - sourceCursor;
      pieces.push({
        clip_start: round6(clipCursor),
        clip_end: round6(clipCursor + sourceLength),
        source_start: round6(sourceCursor),
        source_end: round6(start),
        rate: 1,
      });
      clipCursor += sourceLength;
    }
    if (start < sourceCursor) start = sourceCursor;
    if (end <= start) continue;
    const sourceLength = end - start;
    const renderedLength = sourceLength / segment.rate;
    pieces.push({
      clip_start: round6(clipCursor),
      clip_end: round6(clipCursor + renderedLength),
      source_start: round6(start),
      source_end: round6(end),
      rate: segment.rate,
    });
    clipCursor += renderedLength;
    sourceCursor = end;
  }
  if (sourceCursor < sourceOut) {
    const sourceLength = sourceOut - sourceCursor;
    pieces.push({
      clip_start: round6(clipCursor),
      clip_end: round6(clipCursor + sourceLength),
      source_start: round6(sourceCursor),
      source_end: round6(sourceOut),
      rate: 1,
    });
  }
  return pieces;
}

export function sourceTimeInClip(clip: TimelineClip, localTime: number): number {
  const time = Math.min(Math.max(0, localTime), clip.duration);
  for (const segment of clip.speed_segments) {
    if (time <= segment.clip_end + 1e-7 || segment === clip.speed_segments.at(-1)) {
      const inside = Math.min(
        Math.max(0, time - segment.clip_start),
        segment.clip_end - segment.clip_start,
      );
      return round6(segment.source_start + inside * segment.rate);
    }
  }
  return clip.source_in;
}

/** Same math as backend compute_timeline: running sum of clip durations. */
export function computeLocalTimeline(
  clips: Clip[],
  media: Record<string, MediaInfo>,
): Timeline {
  let cursor = 0;
  const missing = new Set<string>();
  const result = clips.map((clip, index) => {
    const item = media[clip.media_id];
    if (!item) missing.add(clip.media_id);
    const speedPieces = effectiveSpeedSegments(clip);
    const duration = round6(
      speedPieces.reduce((total, piece) => total + piece.clip_end - piece.clip_start, 0),
    );
    let transition = index === 0 ? 0 : Math.min(Math.max(0, clip.transition || 0), duration * 0.5);
    if (index > 0) {
      const previous = clips[index - 1];
      const previousDuration = effectiveSpeedSegments(previous).reduce(
        (total, piece) => total + piece.clip_end - piece.clip_start,
        0,
      );
      transition = Math.min(transition, previousDuration * 0.5);
    }
    transition = round6(transition);
    const start = round6(cursor);
    const end = round6(start + duration);
    const nextCursor = index === 0 ? end : round6(start + duration - transition);
    const rendered = {
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
      speed_segments: speedPieces,
    };
    cursor = nextCursor;
    return rendered;
  });
  return {
    duration: round6(cursor),
    clips: result,
    missing_media_ids: [...missing].sort(),
  };
}

interface EditorState {
  media: MediaInfo[];
  projects: Project[];
  project: Project | null;
  timeline: Timeline | null;
  selectedClipId: string | null;
  exportJob: ExportInfo | null;
  message: string | null;
  busy: boolean;
  canUndoSpeed: boolean;

  loadMedia: () => Promise<void>;
  uploadMedia: (file: File) => Promise<void>;
  loadProjects: () => Promise<void>;
  newProject: () => Promise<void>;
  openProject: (id: string) => Promise<void>;
  save: () => Promise<void>;
  refreshTimeline: () => Promise<void>;
  addClip: (mediaId: string) => void;
  removeClip: (clipId: string) => void;
  moveClip: (clipId: string, direction: -1 | 1) => void;
  trimClip: (clipId: string, edge: "in" | "out", value: number) => void;
  setClipVolume: (clipId: string, value: number) => void;
  setClipTransition: (clipId: string, value: number) => void;
  setClipSpeed: (clipId: string, sourceStart: number, sourceEnd: number, rate: number) => void;
  undoLastSpeed: () => void;
  renameProject: (name: string) => void;
  selectClip: (clipId: string | null) => void;
  startExport: (range?: { start: number; end: number }) => Promise<void>;
  pollExport: () => Promise<void>;
  cancelExport: () => Promise<void>;
  setMessage: (message: string | null) => void;
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;
const speedHistory: Array<{ clipId: string; speedSegments: Clip["speed_segments"] }> = [];

export const useEditor = create<EditorState>((set, get) => ({
  media: [],
  projects: [],
  project: null,
  timeline: null,
  selectedClipId: null,
  exportJob: null,
  message: null,
  busy: false,
  canUndoSpeed: false,

  setMessage: (message) => set({ message }),

  loadMedia: async () => {
    const media = await api.listMedia();
    set({ media });
  },

  uploadMedia: async (file) => {
    set({ busy: true, message: `正在导入 ${file.name}…` });
    try {
      await api.uploadMedia(file);
      await get().loadMedia();
      set({ message: `已导入：${file.name}（原文件不会被修改）` });
    } catch (error) {
      set({ message: (error as Error).message });
    } finally {
      set({ busy: false });
    }
  },

  loadProjects: async () => {
    set({ projects: await api.listProjects() });
  },

  newProject: async () => {
    const project = await api.createProject(`未命名工程 ${Date.now() % 100000}`, []);
    set({
      project,
      timeline: { duration: 0, clips: [], missing_media_ids: [] },
      selectedClipId: null,
      exportJob: null,
      canUndoSpeed: false,
    });
    await get().loadProjects();
  },

  openProject: async (id) => {
    const project = await api.getProject(id);
    const timeline = await api.timeline(id);
    set({
      project,
      timeline,
      selectedClipId: project.clips[0]?.id ?? null,
      exportJob: null,
      canUndoSpeed: false,
      message: timeline.missing_media_ids.length
        ? `工程已打开，但有 ${timeline.missing_media_ids.length} 段素材缺失，恢复后可继续导出`
        : "工程已打开，可以继续剪辑",
    });
  },

  save: async () => {
    const { project } = get();
    if (!project) return;
    set({ busy: true });
    try {
      const saved = await api.saveProject(
        project.id,
        project.name,
        project.clips,
      );
      set({ project: saved });
      await get().refreshTimeline();
      await get().loadProjects();
    } catch (error) {
      set({ message: (error as Error).message });
    } finally {
      set({ busy: false });
    }
  },

  refreshTimeline: async () => {
    const { project } = get();
    if (!project) return;
    set({ timeline: await api.timeline(project.id) });
  },

  addClip: (mediaId) => {
    const { project, media } = get();
    if (!project) return;
    const item = media.find((entry) => entry.id === mediaId);
    if (!item) return;
    const clip: Clip = {
      id: makeClipId(),
      media_id: mediaId,
      source_in: 0,
      source_out: Math.min(item.duration, Math.max(1, item.duration)),
      volume: 1,
      transition: 0,
      speed_segments: [],
    };
    const clips = [...project.clips, clip];
    patchClips(set, get, clips, clip.id, true);
  },

  removeClip: (clipId) => {
    const { project } = get();
    if (!project) return;
    const clips = project.clips.filter((clip) => clip.id !== clipId);
    patchClips(set, get, clips, null, true);
  },

  moveClip: (clipId, direction) => {
    const { project } = get();
    if (!project) return;
    const index = project.clips.findIndex((clip) => clip.id === clipId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= project.clips.length) return;
    const clips = [...project.clips];
    [clips[index], clips[target]] = [clips[target], clips[index]];
    patchClips(set, get, clips, clipId, true);
  },

  trimClip: (clipId, edge, value) => {
    const { project, media } = get();
    if (!project) return;
    const clips = project.clips.map((clip) => {
      if (clip.id !== clipId) return clip;
      const item = media.find((entry) => entry.id === clip.media_id);
      const maxDuration = item?.duration ?? clip.source_out;
      const next = { ...clip };
      if (edge === "in") {
        next.source_in = Math.min(
          Math.max(0, Math.round(value * 1000) / 1000),
          clip.source_out - 0.05,
        );
      } else {
        next.source_out = Math.min(
          Math.max(clip.source_in + 0.05, Math.round(value * 1000) / 1000),
          maxDuration,
        );
      }
      return next;
    });
    patchClips(set, get, clips, clipId, false);
  },

  setClipVolume: (clipId, value) => {
    const { project } = get();
    if (!project) return;
    const clips = project.clips.map((clip) =>
      clip.id === clipId
        ? { ...clip, volume: Math.max(0, Math.min(4, Math.round(value * 100) / 100)) }
        : clip,
    );
    patchClips(set, get, clips, clipId, false);
  },

  setClipTransition: (clipId, value) => {
    const { project } = get();
    if (!project) return;
    const clips = project.clips.map((clip) =>
      clip.id === clipId
        ? { ...clip, transition: Math.max(0, Math.round(value * 1000) / 1000) }
        : clip,
    );
    patchClips(set, get, clips, clipId, false);
  },

  setClipSpeed: (clipId, sourceStart, sourceEnd, rate) => {
    const { project, media } = get();
    if (!project) return;
    const target = project.clips.find((clip) => clip.id === clipId);
    if (!target) return;
    const sourceDuration = media.find((item) => item.id === target.media_id)?.duration ?? target.source_out;
    const safeStart = Math.min(
      Math.max(0, Math.round(sourceStart * 1000) / 1000),
      Math.max(0, sourceDuration - 0.1),
    );
    const safeEnd = Math.min(
      Math.max(safeStart + 0.1, Math.round(sourceEnd * 1000) / 1000),
      sourceDuration,
    );
    const safeRate = Math.max(0.25, Math.min(4, Math.round(rate * 100) / 100));
    const clips = project.clips.map((clip) => {
      if (clip.id !== clipId) return clip;
      const otherSegments = clip.speed_segments.filter(
        (segment) =>
          segment.source_start !== safeStart || segment.source_end !== safeEnd,
      );
      return {
        ...clip,
        speed_segments: safeRate === 1
          ? otherSegments
          : [
              ...otherSegments,
              { source_start: safeStart, source_end: safeEnd, rate: safeRate },
            ],
      };
    });
    patchClips(set, get, clips, clipId, false);
    speedHistory.push({
      clipId,
      speedSegments: structuredClone(project.clips.find((clip) => clip.id === clipId)?.speed_segments ?? []),
    });
    set({ canUndoSpeed: true });
    scheduleSave(set, get);
  },

  undoLastSpeed: () => {
    const previous = speedHistory.pop();
    if (!previous) return;
    const currentProject = get().project;
    if (!currentProject) return;
    const clips = currentProject.clips.map((clip) =>
      clip.id === previous.clipId
        ? { ...clip, speed_segments: structuredClone(previous.speedSegments) }
        : clip,
    );
    patchClips(set, get, clips, previous.clipId, false);
    set({ canUndoSpeed: speedHistory.length > 0 });
    scheduleSave(set, get);
  },

  renameProject: (name) => {
    const { project } = get();
    if (!project) return;
    set({ project: { ...project, name } });
    scheduleSave(set, get);
  },

  selectClip: (clipId) => set({ selectedClipId: clipId }),

  startExport: async (range) => {
    const { project } = get();
    if (!project) return;
    await get().save();
    const exportRange = range
      ? {
          start: Math.max(0, Math.min(range.start, range.end)),
          end: Math.min(get().timeline?.duration ?? range.end, Math.max(range.start, range.end)),
        }
      : undefined;
    const job = await api.startExport(project.id, exportRange);
    set({ exportJob: job, message: "导出已开始…" });
  },

  pollExport: async () => {
    const { exportJob } = get();
    if (!exportJob) return;
    const job = await api.getExport(exportJob.id);
    set({ exportJob: job });
    if (job.status === "done") set({ message: "成片已生成，可下载或继续修改" });
    if (job.status === "failed") set({ message: `导出失败：${job.error}` });
    if (job.status === "cancelled")
      set({ message: "导出已取消，工程未受影响，可重新导出" });
  },

  cancelExport: async () => {
    const { exportJob } = get();
    if (!exportJob) return;
    await api.cancelExport(exportJob.id);
    await get().pollExport();
  },
}));

function patchClips(
  set: (partial: Partial<EditorState>) => void,
  get: () => EditorState,
  clips: Clip[],
  selectedClipId: string | null,
  persist: boolean,
) {
  const { project, media } = get();
  if (!project) return;
  set({
    project: { ...project, clips },
    selectedClipId: selectedClipId ?? get().selectedClipId,
    timeline: computeLocalTimeline(clips, Object.fromEntries(media.map((m) => [m.id, m]))),
  });
  if (persist) scheduleSave(set, get);
}

function scheduleSave(set: (partial: Partial<EditorState>) => void, get: () => EditorState) {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    void get().save();
  }, 600);
}
