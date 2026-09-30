import { create } from "zustand";
import { api } from "./api";
import type { Clip, ExportInfo, MediaInfo, Project, Timeline } from "./types";

function makeClipId(): string {
  return `clip-${Math.random().toString(36).slice(2, 10)}`;
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
    const duration = Math.max(0, clip.source_out - clip.source_in);
    let transition = index === 0 ? 0 : Math.min(Math.max(0, clip.transition || 0), duration * 0.5);
    if (index > 0) {
      const previous = clips[index - 1];
      const previousDuration = Math.max(0, previous.source_out - previous.source_in);
      transition = Math.min(transition, previousDuration * 0.5);
    }
    transition = Math.round(transition * 1e6) / 1e6;
    const start = Math.round(cursor * 1e6) / 1e6;
    const end = Math.round((start + duration) * 1e6) / 1e6;
    const nextCursor = index === 0 ? end : Math.round(start + duration - transition, 6);
    return {
      clip_id: clip.id,
      media_id: clip.media_id,
      start,
      end,
      transition_start: Math.round(start - transition, 6),
      transition,
      duration: Math.round(duration * 1e6) / 1e6,
      source_in: clip.source_in,
      source_out: clip.source_out,
      volume: Math.max(0, Math.min(4, clip.volume ?? 1)),
      media_missing: !item,
      source_duration: item?.duration ?? null,
    };
    cursor = nextCursor;
  });
  return {
    duration: Math.round(cursor * 1e6) / 1e6,
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
  renameProject: (name: string) => void;
  selectClip: (clipId: string | null) => void;
  startExport: () => Promise<void>;
  pollExport: () => Promise<void>;
  cancelExport: () => Promise<void>;
  setMessage: (message: string | null) => void;
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;

export const useEditor = create<EditorState>((set, get) => ({
  media: [],
  projects: [],
  project: null,
  timeline: null,
  selectedClipId: null,
  exportJob: null,
  message: null,
  busy: false,

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

  renameProject: (name) => {
    const { project } = get();
    if (!project) return;
    set({ project: { ...project, name } });
    scheduleSave(set, get);
  },

  selectClip: (clipId) => set({ selectedClipId: clipId }),

  startExport: async () => {
    const { project } = get();
    if (!project) return;
    await get().save();
    const job = await api.startExport(project.id);
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
