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
  uploadingName: string | null;
  canUndo: boolean;
  canRedo: boolean;
  lastEditLabel: string | null;

  loadMedia: (sessionToken?: number) => Promise<void>;
  uploadMedia: (file: File) => Promise<void>;
  cancelUpload: () => void;
  loadProjects: () => Promise<void>;
  newProject: () => Promise<void>;
  openProject: (id: string) => Promise<void>;
  save: () => Promise<void>;
  refreshTimeline: (sessionToken?: number) => Promise<void>;
  addClip: (mediaId: string) => void;
  removeClip: (clipId: string) => void;
  moveClip: (clipId: string, direction: -1 | 1) => void;
  trimClip: (clipId: string, edge: "in" | "out", value: number) => void;
  setClipVolume: (clipId: string, value: number) => void;
  setClipTransition: (clipId: string, value: number) => void;
  setClipSpeed: (clipId: string, sourceStart: number, sourceEnd: number, rate: number) => void;
  undo: () => void;
  redo: () => void;
  renameProject: (name: string) => void;
  selectClip: (clipId: string | null) => void;
  startExport: (range?: { start: number; end: number }) => Promise<void>;
  pollExport: () => Promise<void>;
  cancelExport: () => Promise<void>;
  setMessage: (message: string | null) => void;
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;
let sessionToken = 0;
let uploadController: AbortController | null = null;

interface HistoryEntry {
  label: string;
  clips: Clip[];
  selectedClipId: string | null;
  at: number;
}

/**
 * Per-project edit history. Undo/redo replays snapshots of the clip
 * list only, so switching projects or a late load can never leak
 * clips or selections into another project.
 */
interface ProjectHistory {
  past: HistoryEntry[];
  present: HistoryEntry;
  future: HistoryEntry[];
}

const histories = new Map<string, ProjectHistory>();
const HISTORY_COALESCE_MS = 600;

function cloneClips(clips: Clip[]): Clip[] {
  return structuredClone(clips);
}

function sanitizeSelection(clips: Clip[], selectedClipId: string | null): string | null {
  return clips.some((clip) => clip.id === selectedClipId) ? selectedClipId : null;
}

function ensureHistory(project: Project): ProjectHistory {
  let history = histories.get(project.id);
  if (!history) {
    const present: HistoryEntry = {
      label: "打开工程",
      clips: cloneClips(project.clips),
      selectedClipId: project.clips[0]?.id ?? null,
      at: Date.now(),
    };
    history = { past: [], present, future: [] };
    histories.set(project.id, history);
  }
  return history;
}

export const useEditor = create<EditorState>((set, get) => ({
  media: [],
  projects: [],
  project: null,
  timeline: null,
  selectedClipId: null,
  exportJob: null,
  message: null,
  busy: false,
  uploadingName: null,
  canUndo: false,
  canRedo: false,
  lastEditLabel: null,

  setMessage: (message) => set({ message }),

  loadMedia: async (token = sessionToken) => {
    const media = await api.listMedia();
    if (token !== sessionToken) return;
    set({ media });
  },

  uploadMedia: async (file) => {
    uploadController?.abort();
    const controller = new AbortController();
    uploadController = controller;
    const token = sessionToken;
    set({ busy: true, uploadingName: file.name, message: `正在导入 ${file.name}…` });
    try {
      await api.uploadMedia(file, controller.signal);
      if (token !== sessionToken || uploadController !== controller) return;
      await get().loadMedia(token);
      if (token !== sessionToken || uploadController !== controller) return;
      set({ message: `已导入：${file.name}（原文件不会被修改），可加入时间线` });
    } catch (error) {
      if ((error as Error).name === "AbortError" || controller.signal.aborted) {
        if (uploadController === controller) {
          set({ message: `已取消导入：${file.name}，工程未改动` });
        }
        return;
      }
      set({ message: (error as Error).message });
    } finally {
      if (uploadController === controller) {
        uploadController = null;
        set({ busy: false, uploadingName: null });
      }
    }
  },

  cancelUpload: () => {
    uploadController?.abort();
  },

  loadProjects: async () => {
    const token = sessionToken;
    const projects = await api.listProjects();
    if (token === sessionToken) set({ projects });
  },

  newProject: async () => {
    const token = ++sessionToken;
    uploadController?.abort();
    try {
      const project = await api.createProject(`未命名工程 ${Date.now() % 100000}`, []);
      if (token !== sessionToken) return;
      ensureHistory(project);
      set({
        project,
        timeline: { duration: 0, clips: [], missing_media_ids: [] },
        selectedClipId: null,
        exportJob: null,
        canUndo: false,
        canRedo: false,
        lastEditLabel: null,
        message: "已建立空白工程，从素材库添加视频后即可开始剪辑",
      });
      await get().loadProjects();
    } catch (error) {
      if (token === sessionToken) set({ message: `新建工程失败：${(error as Error).message}` });
    }
  },

  openProject: async (id) => {
    const token = ++sessionToken;
    uploadController?.abort();
    set({ busy: true, message: "正在打开工程…" });
    try {
      const project = await api.getProject(id);
      if (token !== sessionToken) return;
      const timeline = await api.timeline(id);
      if (token !== sessionToken) return;
      const history = ensureHistory(project);
      set({
        project,
        timeline,
        selectedClipId: sanitizeSelection(project.clips, history.present.selectedClipId),
        exportJob: null,
        canUndo: history.past.length > 0,
        canRedo: history.future.length > 0,
        lastEditLabel: null,
        busy: false,
        message: timeline.missing_media_ids.length
          ? `工程已打开，但有 ${timeline.missing_media_ids.length} 段素材缺失，工程仍可编辑，恢复素材后可导出`
          : project.clips.length === 0
            ? "已打开空白工程，从素材库添加视频后即可开始剪辑"
            : "工程已打开，可以继续剪辑",
      });
    } catch (error) {
      if (token === sessionToken) {
        set({ busy: false, message: `打开工程失败：${(error as Error).message}` });
      }
    }
  },

  save: async () => {
    const token = sessionToken;
    const { project } = get();
    if (!project) return;
    set({ busy: true });
    try {
      const saved = await api.saveProject(project.id, project.name, project.clips);
      if (token !== sessionToken || get().project?.id !== project.id) return;
      const history = histories.get(project.id);
      const currentClips = get().project!.clips;
      set({
        project: {
          ...saved,
          // The server roundtrip can lag local edits; keep the newest
          // local clip list (history present) instead of the stale
          // response body, but adopt the new version/timestamps.
          clips: currentClips,
        },
      });
      if (history && history.present.clips !== currentClips) {
        history.present.clips = cloneClips(currentClips);
      }
      await get().refreshTimeline(token);
      if (token !== sessionToken) return;
      await get().loadProjects();
    } catch (error) {
      if (token === sessionToken) set({ message: (error as Error).message });
    } finally {
      if (token === sessionToken) set({ busy: false });
    }
  },

  refreshTimeline: async (token = sessionToken) => {
    const { project } = get();
    if (!project || token !== sessionToken) return;
    const timeline = await api.timeline(project.id);
    if (token !== sessionToken || get().project?.id !== project.id) return;
    set({ timeline });
  },

  addClip: (mediaId) => {
    const { project, media } = get();
    if (!project) return editRejected(set, "请先新建或打开一个工程");
    const item = media.find((entry) => entry.id === mediaId);
    if (!item) return editRejected(set, "素材不存在或尚未完成载入，未添加片段");
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
    commitEdit(get, set, clips, clip.id, "添加片段");
  },

  removeClip: (clipId) => {
    const { project } = get();
    if (!project) return editRejected(set, "请先新建或打开一个工程");
    if (!project.clips.some((clip) => clip.id === clipId)) {
      return editRejected(set, "要删除的片段已不存在，本次删除没有生效");
    }
    const clips = project.clips.filter((clip) => clip.id !== clipId);
    const currentSelection = get().selectedClipId;
    const nextSelection =
      currentSelection === clipId
        ? // Never resurrect a deleted clip; fall back to the neighbour
          // only when the deleted clip was selected.
          (clips[Math.min(project.clips.findIndex((clip) => clip.id === clipId), clips.length - 1)]?.id ?? null)
        : sanitizeSelection(clips, currentSelection);
    commitEdit(get, set, clips, nextSelection, "删除片段");
  },

  moveClip: (clipId, direction) => {
    const { project } = get();
    if (!project) return editRejected(set, "请先新建或打开一个工程");
    const index = project.clips.findIndex((clip) => clip.id === clipId);
    const target = index + direction;
    if (index < 0) return editRejected(set, "要移动的片段已不存在，本次调整没有生效");
    if (target < 0 || target >= project.clips.length) return;
    const clips = [...project.clips];
    [clips[index], clips[target]] = [clips[target], clips[index]];
    commitEdit(get, set, clips, clipId, "调整顺序", true);
  },

  trimClip: (clipId, edge, value) => {
    const { project, media } = get();
    if (!project) return editRejected(set, "请先新建或打开一个工程");
    if (!project.clips.some((clip) => clip.id === clipId)) {
      return editRejected(set, `片段已删除，${edge === "in" ? "入点" : "出点"}裁剪没有生效，之前的内容保持不变`);
    }
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
    commitEdit(get, set, clips, clipId, "裁剪片段", true);
  },

  setClipVolume: (clipId, value) => {
    const { project } = get();
    if (!project) return editRejected(set, "请先新建或打开一个工程");
    if (!project.clips.some((clip) => clip.id === clipId)) {
      return editRejected(set, "片段已删除，音量调整没有生效，之前的内容保持不变");
    }
    const clips = project.clips.map((clip) =>
      clip.id === clipId
        ? { ...clip, volume: Math.max(0, Math.min(4, Math.round(value * 100) / 100)) }
        : clip,
    );
    commitEdit(get, set, clips, clipId, "调整音量", true);
  },

  setClipTransition: (clipId, value) => {
    const { project } = get();
    if (!project) return editRejected(set, "请先新建或打开一个工程");
    if (!project.clips.some((clip) => clip.id === clipId)) {
      return editRejected(set, "片段已删除，转场调整没有生效，之前的内容保持不变");
    }
    const clips = project.clips.map((clip) =>
      clip.id === clipId
        ? { ...clip, transition: Math.max(0, Math.round(value * 1000) / 1000) }
        : clip,
    );
    commitEdit(get, set, clips, clipId, "调整转场", true);
  },

  setClipSpeed: (clipId, sourceStart, sourceEnd, rate) => {
    const { project, media } = get();
    if (!project) return editRejected(set, "请先新建或打开一个工程");
    const target = project.clips.find((clip) => clip.id === clipId);
    if (!target) {
      return editRejected(set, "片段已删除，变速没有生效，之前的速度安排保持不变");
    }
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
    commitEdit(get, set, clips, clipId, "设置变速");
  },

  undo: () => {
    const { project } = get();
    if (!project) return editRejected(set, "请先新建或打开一个工程");
    const history = histories.get(project.id);
    if (!history || history.past.length === 0) return;
    const previous = history.past.pop()!;
    history.future.unshift(history.present);
    history.present = previous;
    applyHistoryEntry(get, set, project.id, previous, `已撤销：${previous.label}`);
  },

  redo: () => {
    const { project } = get();
    if (!project) return editRejected(set, "请先新建或打开一个工程");
    const history = histories.get(project.id);
    if (!history || history.future.length === 0) return;
    const next = history.future.shift()!;
    history.past.push(history.present);
    history.present = next;
    applyHistoryEntry(get, set, project.id, next, `已重做：${next.label}`);
  },

  renameProject: (name) => {
    const { project } = get();
    if (!project) return;
    set({ project: { ...project, name } });
    scheduleSave(set, get);
  },

  selectClip: (clipId) => {
    const { project } = get();
    if (clipId && (!project || !project.clips.some((clip) => clip.id === clipId))) {
      set({ selectedClipId: null, message: "该片段已不存在，已保持无选中状态" });
      return;
    }
    set({ selectedClipId: clipId });
  },

  startExport: async (range) => {
    const token = sessionToken;
    const { project } = get();
    if (!project) return;
    await get().save();
    if (token !== sessionToken || get().project?.id !== project.id) return;
    const exportRange = range
      ? {
          start: Math.max(0, Math.min(range.start, range.end)),
          end: Math.min(get().timeline?.duration ?? range.end, Math.max(range.start, range.end)),
        }
      : undefined;
    const job = await api.startExport(project.id, exportRange);
    if (token !== sessionToken || get().project?.id !== project.id) return;
    set({ exportJob: job, message: "导出已开始…" });
  },

  pollExport: async () => {
    const token = sessionToken;
    const { exportJob, project } = get();
    if (!exportJob) return;
    const job = await api.getExport(exportJob.id);
    if (token !== sessionToken || !project || project.id !== job.project_id) return;
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

function mediaMap(media: MediaInfo[]): Record<string, MediaInfo> {
  return Object.fromEntries(media.map((item) => [item.id, item]));
}

function editRejected(set: (partial: Partial<EditorState>) => void, message: string) {
  set({ message });
}

function commitEdit(
  get: () => EditorState,
  set: (partial: Partial<EditorState>) => void,
  clips: Clip[],
  selectedClipId: string | null,
  label: string,
  coalesce = false,
) {
  const { project, media } = get();
  if (!project) return;
  const history = ensureHistory(project);
  const now = Date.now();
  const canCoalesce =
    coalesce &&
    history.past.length > 0 &&
    history.present.label === label &&
    now - history.present.at < HISTORY_COALESCE_MS;
  if (!canCoalesce) {
    history.past.push(history.present);
  }
  const safeSelection = sanitizeSelection(clips, selectedClipId);
  history.present = {
    label,
    clips: cloneClips(clips),
    selectedClipId: safeSelection,
    at: canCoalesce ? history.present.at : now,
  };
  history.future = [];
  set({
    project: { ...project, clips },
    selectedClipId: safeSelection,
    timeline: computeLocalTimeline(clips, mediaMap(media)),
    canUndo: history.past.length > 0,
    canRedo: false,
    lastEditLabel: label,
  });
  scheduleSave(set, get);
}

function applyHistoryEntry(
  get: () => EditorState,
  set: (partial: Partial<EditorState>) => void,
  projectId: string,
  entry: HistoryEntry,
  message: string,
) {
  const { project, media } = get();
  if (!project || project.id !== projectId) return;
  const history = histories.get(projectId);
  const clips = cloneClips(entry.clips);
  const safeSelection = sanitizeSelection(clips, entry.selectedClipId);
  if (history) {
    history.present = { ...entry, selectedClipId: safeSelection, clips };
  }
  set({
    project: { ...project, clips },
    selectedClipId: safeSelection,
    timeline: computeLocalTimeline(clips, mediaMap(media)),
    canUndo: (history?.past.length ?? 0) > 0,
    canRedo: (history?.future.length ?? 0) > 0,
    lastEditLabel: entry.label,
    message,
  });
  scheduleSave(set, get);
}

function scheduleSave(set: (partial: Partial<EditorState>) => void, get: () => EditorState) {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    void get().save();
  }, 600);
}
