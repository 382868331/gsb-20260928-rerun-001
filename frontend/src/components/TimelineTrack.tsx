import type { Clip, MediaItem } from "../types";
import { layout } from "../types";
import { useRef } from "react";

interface Props {
  clips: Clip[];
  mediaById: Map<string, MediaItem>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onReorder: (from: number, to: number) => void;
  onDelete: (id: string) => void;
}

const PX_PER_SEC = 64;

const COLOR_BAR: Record<string, string> = {
  none: "#4f7cff",
  grayscale: "#8a8f98",
  warm: "#e08a3c",
  cool: "#3c9fe0",
  vivid: "#b34de0",
};

export function TimelineTrack(p: Props) {
  const dragIndex = useRef<number | null>(null);
  const items = layout({ clips: p.clips });
  const total = items.at(-1)?.end ?? 0;

  return (
    <div className="timeline">
      <div className="timeline-head">
        <h2>时间线（画面 + 声音共用一条轨道）</h2>
        <span className="total">总时长 {total.toFixed(2)}s</span>
      </div>

      <div className="ruler" style={{ width: Math.max(total * PX_PER_SEC, 200) }}>
        {Array.from({ length: Math.ceil(total) + 1 }, (_, i) => (
          <span key={i} style={{ left: i * PX_PER_SEC }}>{i}s</span>
        ))}
      </div>

      <div className="track-scroll">
        <div className="track" style={{ width: Math.max(total * PX_PER_SEC, 200) }}>
          {items.length === 0 && (
            <div className="empty-track">从左侧素材库把视频“加入时间线”开始剪辑</div>
          )}
          {items.map(({ clip, start, end }, idx) => {
            const m = p.mediaById.get(clip.media_id);
            const width = (end - start) * PX_PER_SEC;
            const selected = clip.id === p.selectedId;
            return (
              <div
                key={clip.id}
                className={"clip-block " + (selected ? "selected " : "") + (m?.missing ? "missing" : "")}
                style={{ left: start * PX_PER_SEC, width,
                         background: COLOR_BAR[clip.color] ?? "#4f7cff" }}
                draggable
                onDragStart={() => (dragIndex.current = idx)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => {
                  if (dragIndex.current !== null && dragIndex.current !== idx)
                    p.onReorder(dragIndex.current, idx);
                  dragIndex.current = null;
                }}
                onClick={() => p.onSelect(clip.id)}
                title={m?.original_name ?? clip.media_id}
              >
                <div className="clip-title">
                  #{idx + 1} {m?.original_name ?? clip.media_id}
                  {m?.missing ? " ⚠缺失" : ""}
                </div>
                <div className="clip-range">
                  {clip.in_point.toFixed(1)}–{clip.out_point.toFixed(1)}s
                  {clip.speed !== 1 && " " + clip.speed.toFixed(1) + "x"}
                </div>
                <div className="clip-audio">
                  {Array.from({ length: Math.max(4, Math.floor(width / 8)) }, (_, k) => (
                    <i key={k} style={{
                      height: 30 + 60 * Math.abs(Math.sin(k * 1.7 + idx)) + "%",
                      opacity: clip.volume === 0 ? 0.15 : 0.9,
                    }} />
                  ))}
                </div>
                <button className="clip-del"
                  onClick={(e) => { e.stopPropagation(); p.onDelete(clip.id); }}>
                  ×
                </button>
              </div>
            );
          })}
        </div>
      </div>
      <p className="hint">拖动片段调整顺序；修改入/出点或速度后，片段宽度与后续位置立即级联更新。预览代理与最终导出共用同一时间线和 ffmpeg 渲染管线。</p>
    </div>
  );
}
