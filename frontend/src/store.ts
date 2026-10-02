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
  uploading: boolean;
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string | null;
  redoLabel: string | null;

  loadMedia: () => Promise<boolean>;
  uploadMedia: (file: File) => Promise<void>;
  cancelUpload: () => void;
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

interface EditSnapshot {
  label: string;
  kind: string;
  clipId: string | null;
  clips: Clip[];
  selectedClipId: string | null;
  at: number;
}

const COALESCE_MS = 700;
type EditDraft = Omit<EditSnapshot, "at">;

function cloneClips(clips: Clip[]): Clip[] {
  return structuredClone(clips);
}

function sanitizeSelection(clips: Clip[], selectedClipId: string | null): string | null {
  return clips.some((clip) => clip.id === selectedClipId) ? selectedClipId : null;
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
  uploading: false,
  canUndo: false,
  canRedo: false,
  undoLabel: null,
  redoLabel: null,

  setMessage: (message) => set({ message }),

  loadMedia: async () => {
    const token = ++loadMediaToken;
    const media = await api.listMedia();
    if (token !== loadMediaToken) return false;
    set({ media });
    return true;
  },

  uploadMedia: async (file) => {
    if (uploadAbort) uploadAbort.abort();
    const controller = new AbortController();
    uploadAbort = controller;
    set({ uploading: true, busy: true, message: `正在导入 ${file.name}…` });
    const session = sessionToken;
    const mediaToken = loadMediaToken;
    try {
      await api.uploadMedia(file, controller.signal);
      const media = await api.listMedia();
      if (controller.signal.aborted) return;
      // Late results must never populate a project the user has left.
      if (session !== sessionToken || mediaToken !== loadMediaToken) return;
      set({
        media,
        message: `已导入：${file.name}（原文件不会被修改）。点击素材右侧 + 即可加入时间线。`,
      });
    } catch (error) {
      if (controller.signal.aborted || (error as Error).name === "AbortError") {
        if (session === sessionToken) set({ message: `已取消导入：${file.name}` });
        return;
      }
      if (session === sessionToken) set({ message: (error as Error).message });
    } finally {
      if (uploadAbort === controller) {
        uploadAbort = null;
        set({ uploading: false, busy: false });
      }
    }
  },

  cancelUpload: () => {
    uploadAbort?.abort();
  },

  loadProjects: async () => {
    set({ projects: await api.listProjects() });
  },

  newProject: async () => {
    const token = beginSession();
    cancelPendingSave();
    const project = await api.createProject(`未命名工程 ${Date.now() % 100000}`, []);
    if (token !== sessionToken) return;
    set({
      project,
      timeline: { duration: 0, clips: [], missing_media_ids: [] },
      selectedClipId: null,
      exportJob: null,
      message: "已建立空工程：从左侧素材库点 + 添加第一段素材即可开始剪辑。",
    });
    resetHistory();
    await get().loadProjects();
  },

  openProject: async (id) => {
    const token = beginSession();
    cancelPendingSave();
    const project = await api.getProject(id);
    if (token !== sessionToken) return;
    const timeline = await api.timeline(id);
    if (token !== sessionToken) return;
    const selectedClipId = sanitizeSelection(project.clips, project.clips[0]?.id ?? null);
    set({
      project,
      timeline,
      selectedClipId,
      exportJob: null,
      message: project.clips.length === 0
        ? "这是空工程：从左侧素材库点 + 添加素材后即可剪辑，也可以直接保存。"
        : timeline.missing_media_ids.length
        ? `工程已打开，但有 ${timeline.missing_media_ids.length} 段素材缺失，恢复后可继续导出`
        : "工程已打开，可以继续剪辑",
    });
    resetHistory();
  },

  save: async () => {
    cancelPendingSave();
    pendingSave = null;
    const { project } = get();
    if (!project) return;
    const session = sessionToken;
    const projectId = project.id;
    const payload = cloneClips(project.clips);
    set({ busy: true });
    try {
      const saved = await api.saveProject(projectId, project.name, payload);
      if (session !== sessionToken || get().project?.id !== projectId) return;
      set({ project: saved });
      const timeline = await api.timeline(projectId);
      if (session !== sessionToken || get().project?.id !== projectId) return;
      set({ timeline });
      await get().loadProjects();
    } catch (error) {
      if (session === sessionToken) set({ message: `保存未生效：${(error as Error).message}` });
    } finally {
      if (session === sessionToken) set({ busy: false });
    }
  },

  refreshTimeline: async () => {
    const { project } = get();
    if (!project) return;
    const projectId = project.id;
    const session = sessionToken;
    const timeline = await api.timeline(projectId);
    if (session !== sessionToken || get().project?.id !== projectId) return;
    set({ timeline });
  },

  addClip: (mediaId) => {
    const { project, media } = get();
    if (!project) return;
    const item = media.find((entry) => entry.id === mediaId);
    if (!item) {
      set({ message: "素材尚未载入完成，添加没有生效；请稍后再试。" });
      return;
    }
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
    applyEdit(get, set, {
      label: `添加「${item.filename}」`,
      kind: "add",
      clipId: clip.id,
      clips,
      selectedClipId: clip.id,
    });
  },

  removeClip: (clipId) => {
    const { project } = get();
    if (!project) return;
    const target = project.clips.find((clip) => clip.id === clipId);
    if (!target) {
      set({ message: "要删除的片段已不存在，本次编辑没有生效；之前的内容保持不变。" });
      return;
    }
    const clips = project.clips.filter((clip) => clip.id !== clipId);
    const media = get().media.find((entry) => entry.id === target.media_id);
    // Clearing the selection makes the empty/no-selection state explicit
    // instead of silently pointing at another clip.
    applyEdit(get, set, {
      label: `删除「${media?.filename ?? "片段"}」`,
      kind: "remove",
      clipId,
      clips,
      selectedClipId: null,
    });
  },

  moveClip: (clipId, direction) => {
    const { project } = get();
    if (!project) return;
    if (!project.clips.some((clip) => clip.id === clipId)) {
      set({ message: "要移动的片段已不存在，本次编辑没有生效；之前的内容保持不变。" });
      return;
    }
    const index = project.clips.findIndex((clip) => clip.id === clipId);
    const target = index + direction;
    if (target < 0 || target >= project.clips.length) return;
    const clips = [...project.clips];
    [clips[index], clips[target]] = [clips[target], clips[index]];
    applyEdit(get, set, {
      label: direction < 0 ? "片段前移" : "片段后移",
      kind: "move",
      clipId,
      clips,
      selectedClipId: clipId,
    });
  },

  trimClip: (clipId, edge, value) => {
    const { project, media } = get();
    if (!project) return;
    if (!project.clips.some((clip) => clip.id === clipId)) {
      set({ message: "要裁剪的片段已不存在，本次编辑没有生效；之前的内容保持不变。" });
      return;
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
    coalesceOrPushEdit(get, set, {
      label: edge === "in" ? "调整入点" : "调整出点",
      kind: edge === "in" ? "trim-in" : "trim-out",
      clipId,
      clips,
      selectedClipId: clipId,
    });
  },

  setClipVolume: (clipId, value) => {
    const { project } = get();
    if (!project) return;
    if (!project.clips.some((clip) => clip.id === clipId)) {
      set({ message: "要调音量的片段已不存在，本次编辑没有生效；之前的内容保持不变。" });
      return;
    }
    const clips = project.clips.map((clip) =>
      clip.id === clipId
        ? { ...clip, volume: Math.max(0, Math.min(4, Math.round(value * 100) / 100)) }
        : clip,
    );
    coalesceOrPushEdit(get, set, {
      label: "调整音量",
      kind: "volume",
      clipId,
      clips,
      selectedClipId: clipId,
    });
  },

  setClipTransition: (clipId, value) => {
    const { project } = get();
    if (!project) return;
    if (!project.clips.some((clip) => clip.id === clipId)) {
      set({ message: "要调转场的片段已不存在，本次编辑没有生效；之前的内容保持不变。" });
      return;
    }
    const clips = project.clips.map((clip) =>
      clip.id === clipId
        ? { ...clip, transition: Math.max(0, Math.round(value * 1000) / 1000) }
        : clip,
    );
    coalesceOrPushEdit(get, set, {
      label: "调整转场",
      kind: "transition",
      clipId,
      clips,
      selectedClipId: clipId,
    });
  },

  setClipSpeed: (clipId, sourceStart, sourceEnd, rate) => {
    const { project, media } = get();
    if (!project) return;
    const target = project.clips.find((clip) => clip.id === clipId);
    if (!target) {
      set({ message: "要变速的片段已不存在，本次编辑没有生效；之前确认的速度安排保持不变。" });
      return;
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
    applyEdit(get, set, {
      label: safeRate === 1 ? "取消一段变速" : `应用 ${safeRate.toFixed(2)}× 变速`,
      kind: "speed",
      clipId,
      clips,
      selectedClipId: clipId,
    });
  },

  undo: () => {
    const snapshot = undoStack.pop();
    const { project } = get();
    if (!snapshot || !project) {
      if (snapshot) undoStack.push(snapshot);
      return;
    }
    redoStack.push({
      label: snapshot.label,
      kind: snapshot.kind,
      clipId: snapshot.clipId,
      clips: cloneClips(project.clips),
      selectedClipId: sanitizeSelection(project.clips, get().selectedClipId),
      at: Date.now(),
    });
    commitSnapshot(set, get, snapshot);
    set({ message: `已撤销：${snapshot.label}` });
  },

  redo: () => {
    const snapshot = redoStack.pop();
    const { project } = get();
    if (!snapshot || !project) {
      if (snapshot) redoStack.push(snapshot);
      return;
    }
    undoStack.push({
      label: snapshot.label,
      kind: snapshot.kind,
      clipId: snapshot.clipId,
      clips: cloneClips(project.clips),
      selectedClipId: sanitizeSelection(project.clips, get().selectedClipId),
      at: Date.now(),
    });
    commitSnapshot(set, get, snapshot);
    set({ message: `已重做：${snapshot.label}` });
  },

  renameProject: (name) => {
    const { project } = get();
    if (!project) return;
    set({ project: { ...project, name } });
    scheduleSave();
  },

  selectClip: (clipId) => {
    const { project } = get();
    if (!project) return;
    // Never let the media area, timeline or inspector reference a
    // clip that no longer exists in this project.
    set({ selectedClipId: sanitizeSelection(project.clips, clipId) });
  },

  startExport: async (range) => {
    const { project } = get();
    if (!project) return;
    await get().save();
    const afterSave = get().project;
    if (!afterSave || afterSave.id !== project.id) return;
    const exportRange = range
      ? {
          start: Math.max(0, Math.min(range.start, range.end)),
          end: Math.min(get().timeline?.duration ?? range.end, Math.max(range.start, range.end)),
        }
      : undefined;
    const projectId = project.id;
    const session = sessionToken;
    const job = await api.startExport(projectId, exportRange);
    if (session !== sessionToken || get().project?.id !== projectId) return;
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

// ---- Session guards, edit history and queued persistence --------------

let sessionToken = 0;
let loadMediaToken = 0;
let uploadAbort: AbortController | null = null;
const undoStack: EditSnapshot[] = [];
const redoStack: EditSnapshot[] = [];
let pendingSave: { projectId: string; name: string; clips: Clip[] } | null = null;

function beginSession(): number {
  sessionToken += 1;
  uploadAbort?.abort();
  uploadAbort = null;
  useEditor.setState({ busy: false, uploading: false });
  // Flush any edits of the project we are leaving against the exact
  // snapshot captured when the edit happened.
  flushPendingSave();
  return sessionToken;
}

function resetHistory() {
  undoStack.length = 0;
  redoStack.length = 0;
  syncHistoryFlags(useEditor.setState);
}
function syncHistoryFlags(setState: (partial: Partial<EditorState>) => void) {
  setState({
    canUndo: undoStack.length > 0,
    canRedo: redoStack.length > 0,
    undoLabel: undoStack.at(-1)?.label ?? null,
    redoLabel: redoStack.at(-1)?.label ?? null,
  });
}

function mediaMap(media: MediaInfo[]): Record<string, MediaInfo> {
  return Object.fromEntries(media.map((item) => [item.id, item]));
}

function commitSnapshot(
  set: (partial: Partial<EditorState>) => void,
  get: () => EditorState,
  snapshot: EditSnapshot,
) {
  const { project, media } = get();
  if (!project) return;
  const clips = cloneClips(snapshot.clips);
  const selectedClipId = sanitizeSelection(clips, snapshot.selectedClipId);
  set({
    project: { ...project, clips },
    selectedClipId,
    timeline: computeLocalTimeline(clips, mediaMap(media)),
  });
  scheduleSave();
}

function pushBeforeState(snapshot: EditDraft, before: { clips: Clip[]; selectedClipId: string | null }) {
  undoStack.push({
    label: snapshot.label,
    kind: snapshot.kind,
    clipId: snapshot.clipId,
    clips: cloneClips(before.clips),
    selectedClipId: before.selectedClipId,
    at: Date.now(),
  });
  redoStack.length = 0;
}

function applyEdit(
  get: () => EditorState,
  set: (partial: Partial<EditorState>) => void,
  next: EditDraft,
) {
  const { project, selectedClipId } = get();
  if (!project) return;
  if (JSON.stringify(next.clips) === JSON.stringify(project.clips)) return;
  pushBeforeState(next, { clips: project.clips, selectedClipId });
  commitSnapshot(set, get, { ...next, at: Date.now() });
  syncHistoryFlags(set);
}

function coalesceOrPushEdit(
  get: () => EditorState,
  set: (partial: Partial<EditorState>) => void,
  next: EditDraft,
) {
  const { project, selectedClipId } = get();
  if (!project) return;
  if (JSON.stringify(next.clips) === JSON.stringify(project.clips)) return;
  const last = undoStack.at(-1);
  if (
    last &&
    last.kind === next.kind &&
    last.clipId === next.clipId &&
    Date.now() - last.at < COALESCE_MS
  ) {
    // Keep the original pre-drag state; one undo reverts the whole drag.
    last.at = Date.now();
  } else {
    pushBeforeState(next, { clips: project.clips, selectedClipId });
  }
  commitSnapshot(set, get, { ...next, at: Date.now() });
  syncHistoryFlags(set);
}

function cancelPendingSave() {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
}

function flushPendingSave() {
  cancelPendingSave();
  const queued = pendingSave;
  pendingSave = null;
  if (!queued) return;
  void api
    .saveProject(queued.projectId, queued.name, queued.clips)
    .catch(() => undefined);
}

function scheduleSave() {
  const { project } = useEditor.getState();
  if (!project) return;
  // Capture the exact content so a project switch or late local edit
  // cannot make the PUT carry another project's clips.
  pendingSave = {
    projectId: project.id,
    name: project.name,
    clips: cloneClips(project.clips),
  };
  cancelPendingSave();
  saveTimer = setTimeout(() => {
    saveTimer = null;
    const queued = pendingSave;
    pendingSave = null;
    if (!queued) return;
    const session = sessionToken;
    void (async () => {
      try {
        const saved = await api.saveProject(queued.projectId, queued.name, queued.clips);
        const state = useEditor.getState();
        if (session !== sessionToken || state.project?.id !== queued.projectId) return;
        useEditor.setState({ project: saved, busy: false });
        await state.refreshTimeline();
        await state.loadProjects();
      } catch (error) {
        if (session === sessionToken) {
          useEditor.setState({ message: `自动保存未生效：${(error as Error).message}` });
        }
      }
    })();
  }, 600);
}
