import { ArrowLeft, ArrowRight, Trash2 } from "lucide-react";
import { useEditor } from "../store";
import type { Clip, MediaInfo } from "../types";

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
  const setClipVolume = useEditor((state) => state.setClipVolume);
  const setClipTransition = useEditor((state) => state.setClipTransition);

  if (!project || !timeline) return null;
  const index = new Map<string, MediaInfo>(media.map((item) => [item.id, item]));
  const selected = project.clips.find((clip) => clip.id === selectedClipId) ?? null;
  const selectedMedia = selected ? (index.get(selected.media_id) ?? undefined) : undefined;
  const maxDuration = timeline.duration || 1;
  const selectedPosition = selected
    ? timeline.clips.findIndex((entry) => entry.clip_id === selected.id)
    : -1;
  const selectedTimelineClip = selectedPosition >= 0 ? timeline.clips[selectedPosition] : null;
  const previousTimelineClip = selectedPosition > 0 ? timeline.clips[selectedPosition - 1] : null;
  const maxTransition = selectedTimelineClip && previousTimelineClip
    ? Math.min(selectedTimelineClip.duration, previousTimelineClip.duration) * 0.5
    : 0;

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
          const width = Math.max(4, (clip.duration / maxDuration) * 100);
          return (
            <div
              key={clip.clip_id}
              className={
                "track-clip" +
                (clip.clip_id === selectedClipId ? " selected" : "") +
                (clip.media_missing ? " missing" : "") +
                (clip.transition > 0 ? " has-transition" : "")
              }
              style={{
                left: `${(clip.start / maxDuration) * 100}%`,
                width: `${width}%`,
              }}
              onClick={() => selectClip(clip.clip_id)}
              title={`${item?.filename ?? clip.media_id}  位置 ${formatDuration(
                clip.start,
              )} - ${formatDuration(clip.end)}`}
            >
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
        onTrim={trimClip}
        onMove={moveClip}
        onRemove={removeClip}
        onVolume={setClipVolume}
        onTransition={setClipTransition}
        maxTransition={maxTransition}
        position={selected ? timeline.clips.findIndex((c) => c.clip_id === selected.id) : -1}
        total={project.clips.length}
      />
    </section>
  );
}

interface InspectorProps {
  clip: Clip | null;
  media: MediaInfo | undefined;
  position: number;
  total: number;
  onTrim: (clipId: string, edge: "in" | "out", value: number) => void;
  onMove: (clipId: string, direction: -1 | 1) => void;
  onRemove: (clipId: string) => void;
  onVolume: (clipId: string, value: number) => void;
  onTransition: (clipId: string, value: number) => void;
  maxTransition: number;
}

function ClipInspector({
  clip,
  media,
  position,
  total,
  onTrim,
  onMove,
  onRemove,
  onVolume,
  onTransition,
  maxTransition,
}: InspectorProps) {
  if (!clip) {
    return <p className="hint">选择一个片段后可以裁切入点/出点或调整顺序。</p>;
  }
  const sourceDuration = media?.duration ?? Math.max(clip.source_out, 1);
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
      <span className="meta">
        片段时长 {formatDuration(Math.max(0, clip.source_out - clip.source_in))}
        ，修改后后续片段位置自动级联变化。
      </span>
      <label className="trim-row">
        <span>音量（0 静音，1 原音量）</span>
        <input
          type="range"
          min={0}
          max={4}
          step={0.05}
          value={clip.volume ?? 1}
          onChange={(event) => onVolume(clip.id, Number(event.target.value))}
        />
        <strong>{(clip.volume ?? 1).toFixed(2)}×</strong>
      </label>
      <label className="trim-row">
        <span>与前段交叠转场</span>
        <input
          type="range"
          min={0}
          max={Math.max(maxTransition, 0.05)}
          step={0.05}
          value={Math.min(clip.transition ?? 0, maxTransition)}
          disabled={position <= 0}
          onChange={(event) => onTransition(clip.id, Number(event.target.value))}
        />
        <strong>{formatDuration(clip.transition ?? 0)}</strong>
      </label>
    </div>
  );
}
