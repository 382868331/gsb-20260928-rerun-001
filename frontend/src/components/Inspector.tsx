import type { Clip, MediaItem } from "../types";
import { COLOR_OPTIONS } from "../types";

interface Props {
  clip: Clip | null;
  media?: MediaItem;
  onChange: (patch: Partial<Clip>) => void;
}

const fmt = (n: number) => n.toFixed(2);

export function Inspector({ clip, media, onChange }: Props) {
  if (!clip) return (
    <div className="inspector-empty">
      <h2>片段参数</h2>
      <p>点击时间线上的片段后，可在这里裁切入/出点、调速、调色、调音量。</p>
    </div>
  );

  const max = media?.duration ?? clip.out_point;

  const trim = (key: "in_point" | "out_point", raw: string) => {
    let v = parseFloat(raw);
    if (Number.isNaN(v)) return;
    v = Math.max(0, Math.min(max, v));
    if (key === "in_point") v = Math.min(v, clip.out_point - 0.05);
    if (key === "out_point") v = Math.max(v, clip.in_point + 0.05);
    onChange({ [key]: v });
  };

  return (
    <div className="inspector-body">
      <h2>片段参数</h2>
      <div className="kv"><span>素材</span><b title={media?.original_name}>
        {media?.missing ? "⚠ " + (media?.original_name ?? clip.media_id) + "（缺失）"
                        : media?.original_name ?? clip.media_id}
      </b></div>

      <label>入点（源素材秒）</label>
      <div className="row">
        <input type="range" min={0} max={max} step={0.05}
          value={clip.in_point}
          onChange={(e) => trim("in_point", e.target.value)} />
        <input className="num" type="number" step={0.05} min={0} max={max}
          value={fmt(clip.in_point)}
          onChange={(e) => trim("in_point", e.target.value)} />
      </div>

      <label>出点（源素材秒）</label>
      <div className="row">
        <input type="range" min={0} max={max} step={0.05}
          value={clip.out_point}
          onChange={(e) => trim("out_point", e.target.value)} />
        <input className="num" type="number" step={0.05} min={0} max={max}
          value={fmt(clip.out_point)}
          onChange={(e) => trim("out_point", e.target.value)} />
      </div>

      <div className="kv">
        <span>源片段长</span>
        <b>{fmt(clip.out_point - clip.in_point)}s</b>
      </div>

      <label>速度（时间线时长随之变化，后续片段自动后移/前移）</label>
      <div className="row">
        <input type="range" min={0.5} max={2} step={0.1}
          value={clip.speed}
          onChange={(e) => onChange({ speed: parseFloat(e.target.value) })} />
        <span className="badge">{clip.speed.toFixed(1)}×</span>
      </div>

      <label>音量</label>
      <div className="row">
        <input type="range" min={0} max={1.5} step={0.05}
          value={clip.volume}
          onChange={(e) => onChange({ volume: parseFloat(e.target.value) })} />
        <span className="badge">{Math.round(clip.volume * 100)}%</span>
      </div>

      <label>调色</label>
      <div className="color-grid">
        {COLOR_OPTIONS.map(([val, label]) => (
          <button key={val}
            className={clip.color === val ? "active" : ""}
            onClick={() => onChange({ color: val })}>
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
