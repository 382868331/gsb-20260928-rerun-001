import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, newClip } from "./api";
import { MediaBin } from "./components/MediaBin";
import { Inspector } from "./components/Inspector";
import { TimelineTrack } from "./components/TimelineTrack";
import { Preview } from "./components/Preview";
import { ProjectBar } from "./components/ProjectBar";
import type {
  Clip, MediaItem, Project, RenderJob, Timeline as Tl,
} from "./types";
import { totalDuration } from "./types";

export default function App() {
  const [media, setMedia] = useState<MediaItem[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState("my-project");
  const [status, setStatus] = useState<{ kind: "info" | "error"; text: string }
    | null>(null);
  const [dirty, setDirty] = useState(false);
  const [job, setJob] = useState<RenderJob | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const timeline: Tl = useMemo(() => ({ clips }), [clips]);
  const mediaById = useMemo(
    () => new Map(media.map((m) => [m.id, m])), [media]);

  const refreshMedia = useCallback(async () => {
    const { media } = await api.media();
    setMedia(media);
  }, []);

  useEffect(() => {
    api.samples().then(({ media }) => setMedia(media))
      .catch(() => refreshMedia());
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
  }, [refreshMedia]);

  const notify = (kind: "info" | "error", text: string) => {
    setStatus({ kind, text });
    window.setTimeout(() => setStatus(null), 4000);
  };

  const mutate = useCallback((fn: (cs: Clip[]) => Clip[]) => {
    setClips((cs) => fn(cs));
    setDirty(true);
  }, []);

  const addClip = (m: MediaItem) => {
    if (m.missing) return notify("error", "该素材文件缺失，无法加入时间线");
    mutate((cs) => [...cs, newClip(m)]);
    notify("info", `已加入时间线：${m.original_name}`);
  };

  const updateClip = (id: string, patch: Partial<Clip>) =>
    mutate((cs) => cs.map((c) => (c.id === id ? { ...c, ...patch } : c)));

  const selected = clips.find((c) => c.id === selectedId) ?? null;

  // ---- project save / load ----
  const save = useCallback(async () => {
    if (!clips.length) return notify("error", "时间线为空，无法保存");
    try {
      await api.saveProject(projectName.trim() || "untitled", timeline);
      setDirty(false);
      notify("info", "工程已保存");
    } catch (e) {
      notify("error", (e as Error).message);
    }
  }, [clips, projectName, timeline]);

  const openProject = async (name: string) => {
    try {
      const p: Project = await api.loadProject(name);
      setClips(p.timeline.clips);
      setProjectName(p.name);
      setSelectedId(p.timeline.clips[0]?.id ?? null);
      setDirty(false);
      setPreviewUrl(null);
      if (p.missing_media?.length)
        notify("error", `有 ${p.missing_media.length} 个素材缺失，片段保留可编辑`);
      else notify("info", `已打开工程 ${p.name}`);
    } catch (e) {
      notify("error", (e as Error).message);
    }
  };

  // ---- render (preview & export share backend pipeline) ----
  const startRender = async (kind: "preview" | "export") => {
    if (!clips.length) return notify("error", "时间线为空");
    if (pollRef.current) window.clearInterval(pollRef.current);
    try {
      // save before render so an interrupted render never loses the edit
      await api.saveProject(projectName.trim() || "untitled", timeline);
      setDirty(false);
      const j = await api.render(kind, projectName.trim() || "untitled", timeline);
      setJob(j);
      if (kind === "preview") setPreviewUrl(null);
      pollRef.current = window.setInterval(async () => {
        const live = await api.job(j.id);
        setJob(live);
        if (live.status === "done") {
          window.clearInterval(pollRef.current!);
          if (kind === "preview")
            setPreviewUrl(`${live.output}?t=${Date.now()}`);
          notify("info", kind === "preview" ? "预览渲染完成" : "成片导出完成");
        } else if (live.status === "failed") {
          window.clearInterval(pollRef.current!);
          notify("error", `渲染失败（工程已保存，可修改后重试）：${live.error ?? ""}`);
        }
      }, 700);
    } catch (e) {
      notify("error", (e as Error).message);
    }
  };

  const cancelRender = async () => {
    if (!job) return;
    await api.cancel(job.id);
  };

  const missingIds = useMemo(
    () => clips.filter((c) => mediaById.get(c.media_id)?.missing).map((c) => c.id),
    [clips, mediaById]);

  return (
    <div className="app">
      <ProjectBar
        projectName={projectName}
        setProjectName={setProjectName}
        dirty={dirty}
        onSave={save}
        onOpen={openProject}
        onRender={() => startRender("export")}
        onPreview={() => startRender("preview")}
        onCancel={cancelRender}
        job={job}
      />
      {status && (
        <div className={`toast ${status.kind}`}>{status.text}</div>
      )}
      <div className="workspace">
        <aside className="panel bin">
          <MediaBin media={media} onAdd={addClip} onRefresh={refreshMedia} />
        </aside>
        <main className="panel main">
          <Preview
            previewUrl={previewUrl}
            job={job}
            totalDuration={totalDuration(timeline)}
            clips={clips}
            mediaById={mediaById}
          />
          <TimelineTrack
            clips={clips}
            mediaById={mediaById}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onReorder={(from, to) =>
              mutate((cs) => {
                const next = [...cs];
                const [item] = next.splice(from, 1);
                next.splice(to, 0, item);
                return next;
              })}
            onDelete={(id) => {
              mutate((cs) => cs.filter((c) => c.id !== id));
              if (selectedId === id) setSelectedId(null);
            }}
          />
          {missingIds.length > 0 && (
            <div className="missing-banner">
              ⚠ {missingIds.length} 个片段的源素材缺失，已保留位置与裁切参数，
              补回素材后可继续编辑与导出。
            </div>
          )}
        </main>
        <aside className="panel inspector">
          <Inspector
            clip={selected}
            media={selected ? mediaById.get(selected.media_id) : undefined}
            onChange={(patch) => selected && updateClip(selected.id, patch)}
          />
        </aside>
      </div>
    </div>
  );
}
