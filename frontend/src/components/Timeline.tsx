import { ArrowLeft, ArrowRight, Repeat, Trash2 } from "lucide-react";
import { useEditor } from "../store";
import type { Clip, MediaInfo, TimelineClip } from "../types";

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(2).padStart(5, "0");
  return `${minutes}:${secs}`;
}

export function Timeline() {
  const project = useEditor((state) => state.project);
  const timeline = useEditor((state) => state.timeline);
  const media = useEditor((state) => state.media);
  const selectedClipId = useEditor((state) => state.selectedClipId);
  const selectClip = useEditor((state) => state.selectClip);
  const removeClip = useEditor((state) => state.removeClip);
  const moveClip = useEditor((state) => state.moveClip);
  const trimClip = useEditor((state) => state.trimClip);
  const setVolume = useEditor((state) => state.setVolume);
  const setTransition = useEditor((state) => state.setTransition);
  const loopClipId = useEditor((state) => state.loopClipId);
  const setLoopClip = useEditor((state) => state.setLoopClip);

  if (!project || !timeline) return null;
  const index = new Map<string, MediaInfo>(media.map((item) => [item.id, item]));
  const selected = project.clips.find((clip) => clip.id === selectedClipId) ?? null;
  const selectedMedia = selected ? (index.get(selected.media_id) ?? undefined) : undefined;
  const selectedIndex = selected
    ? timeline.clips.findIndex((c) => c.clip_id === selected.id)
    : -1;
  const selectedTimeline: TimelineClip | undefined =
    selectedIndex >= 0 ? timeline.clips[selectedIndex] : undefined;
  const prevTimeline = selectedIndex > 0 ? timeline.clips[selectedIndex - 1] : undefined;
  const maxTransition = selectedTimeline
    ? Math.max(
        0,
        Math.min(selectedTimeline.duration, prevTimeline?.duration ?? 0) * 0.5,
      )
    : 0;
  const total = timeline.duration || 1;

  return (
    <section className="panel timeline-panel">
      <div className="timeline-head">
        <h2>时间线（画面与声音共用）</h2>
        <span className="meta">总时长 {formatDuration(timeline.duration)}</span>
      </div>

      <div className="track" aria-label="统一音视频时间线">
        {timeline.clips.length === 0 && (
          <div className="empty-track">从左侧素材库把视频加到这里</div>
        )}
        {timeline.clips.map((clip, clipIndex) => {
          const item = index.get(clip.media_id) ?? undefined;
          const left = ((clip.start - clip.transition) / total) * 100;
          const width = Math.max(1.5, (clip.duration / total) * 100);
          const fadeWidth =
            clip.duration > 0
              ? Math.min(100, (clip.transition / clip.duration) * 100)
              : 0;
          return (
            <div
              key={clip.clip_id}
              className={
                "track-clip" +
                (clip.clip_id === selectedClipId ? " selected" : "") +
                (clip.media_missing ? " missing" : "")
              }
              style={{
                left: `${left}%`,
                width: `${width}%`,
                zIndex: clipIndex + 1,
              }}
              onClick={() => selectClip(clip.clip_id)}
              title={`${item?.filename ?? clip.media_id}  位置 ${formatDuration(
                clip.start - clip.transition,
              )} - ${formatDuration(clip.end - clip.transition)}`}
            >
              {clip.transition > 0 && (
                <div
                  className="transition-wedge"
                  style={{ width: `${fadeWidth}%` }}
                  title={`交叠转场 ${clip.transition.toFixed(2)}s`}
                />
              )}
              <span className="clip-name">
                {clipIndex + 1}. {item?.filename ?? "素材缺失"}
              </span>
              <span className="clip-range">
                {formatDuration(clip.source_in)} → {formatDuration(clip.source_out)}
              </span>
              <div className="clip-waves" aria-hidden="true">
                <i /><i /><i /><i /><i />
              </div>
            </div>
          );
        })}
      </div>

      <ClipInspector
        clip={selected}
        media={selectedMedia}
        timelineClip={selectedTimeline}
        maxTransition={maxTransition}
        loopActive={selected != null && loopClipId === selected.id}
        onTrim={trimClip}
        onMove={moveClip}
        onRemove={removeClip}
        onVolume={setVolume}
        onTransition={setTransition}
        onToggleLoop={(clipId) => setLoopClip(loopClipId === clipId ? null : clipId)}
        position={selectedIndex}
        total={project.clips.length}
      />
    </section>
  );
}

interface InspectorProps {
  clip: Clip | null;
  media: MediaInfo | undefined;
  timelineClip: TimelineClip | undefined;
  maxTransition: number;
  loopActive: boolean;
  position: number;
  total: number;
  onTrim: (clipId: string, edge: "in" | "out", value: number) => void;
  onMove: (clipId: string, direction: -1 | 1) => void;
  onRemove: (clipId: string) => void;
  onVolume: (clipId: string, value: number) => void;
  onTransition: (clipId: string, value: number) => void;
  onToggleLoop: (clipId: string) => void;
}

function ClipInspector({
  clip,
  media,
  timelineClip,
  maxTransition,
  loopActive,
  position,
  total,
  onTrim,
  onMove,
  onRemove,
  onVolume,
  onTransition,
  onToggleLoop,
}: InspectorProps) {
  if (!clip) {
    return <p className="hint">选择一个片段后可以裁切入点/出点或调整顺序。</p>;
  }
  const sourceDuration = media?.duration ?? Math.max(clip.source_out, 1);
  const transition = timelineClip?.transition ?? 0;
  return (
    <div className="inspector">
      <div className="inspector-actions">
        <button
          type="button"
          className="secondary"
          disabled={position <= 0}
          onClick={() => onMove(clip.id, -1)}
        >
          <ArrowLeft size={15} /> 前移
        </button>
        <button
          type="button"
          className="secondary"
          disabled={position < 0 || position >= total - 1}
          onClick={() => onMove(clip.id, 1)}
        >
          后移 <ArrowRight size={15} />
        </button>
        <button type="button" className="danger" onClick={() => onRemove(clip.id)}>
          <Trash2 size={15} /> 删除
        </button>
        <button
          type="button"
          className={loopActive ? "" : "secondary"}
          disabled={transition <= 0}
          title="循环播放转场前后各 1 秒，便于核对画面混合与声音淡入淡出"
          onClick={() => onToggleLoop(clip.id)}
        >
          <Repeat size={15} /> {loopActive ? "停止循环" : "循环试听转场"}
        </button>
      </div>
      <label className="trim-row">
        <span>入点（源 {formatDuration(0)}）</span>
        <input
          type="range"
          min={0}
          max={Math.max(0, sourceDuration - 0.05)}
          step={0.05}
          value={clip.source_in}
          onChange={(event) => onTrim(clip.id, "in", Number(event.target.value))}
        />
        <strong>{formatDuration(clip.source_in)}</strong>
      </label>
      <label className="trim-row">
        <span>出点（源 {formatDuration(sourceDuration)}）</span>
        <input
          type="range"
          min={0.05}
          max={sourceDuration}
          step={0.05}
          value={clip.source_out}
          onChange={(event) => onTrim(clip.id, "out", Number(event.target.value))}
        />
        <strong>{formatDuration(clip.source_out)}</strong>
      </label>
      <label className="trim-row">
        <span>音量（0 = 静音）</span>
        <input
          type="range"
          min={0}
          max={2}
          step={0.05}
          value={clip.volume}
          onChange={(event) => onVolume(clip.id, Number(event.target.value))}
        />
        <strong>{clip.volume.toFixed(2)}×</strong>
      </label>
      <label className="trim-row">
        <span>交叠转场（与前一段）</span>
        <input
          type="range"
          min={0}
          max={Math.max(maxTransition, 0.05)}
          step={0.05}
          value={Math.min(clip.transition, maxTransition)}
          disabled={position <= 0 || maxTransition <= 0}
          onChange={(event) => onTransition(clip.id, Number(event.target.value))}
        />
        <strong>{transition.toFixed(2)}s</strong>
      </label>
      <span className="meta">
        片段时长 {formatDuration(Math.max(0, clip.source_out - clip.source_in))}
        ，修改后后续片段位置自动级联变化。
      </span>
    </div>
  );
}
