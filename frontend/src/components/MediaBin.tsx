import { useRef, useState } from "react";
import { api } from "../api";
import type { MediaItem } from "../types";

interface Props {
  media: MediaItem[];
  onAdd: (m: MediaItem) => void;
  onRefresh: () => Promise<void>;
}

export function MediaBin({ media, onAdd, onRefresh }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState<number | null>(null);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    for (const file of Array.from(files)) {
      setUploading(0);
      try {
        await api.upload(file, (p) => setUploading(p));
        await onRefresh();
      } catch (e) {
        alert(`导入失败：${(e as Error).message}`);
      }
    }
    setUploading(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div className="media-bin">
      <h2>素材库</h2>
      <button onClick={() => fileRef.current?.click()}>＋ 导入视频</button>
      <input
        ref={fileRef}
        type="file"
        accept="video/*"
        multiple
        hidden
        onChange={(e) => upload(e.target.files)}
      />
      {uploading !== null && (
        <div className="upload-progress">上传中 {Math.round(uploading * 100)}%</div>
      )}
      <ul className="media-list">
        {media.map((m) => (
          <li key={m.id} className={m.missing ? "missing" : ""}>
            <video
              src={m.missing ? undefined : `/media/${m.filename}`}
              muted
              preload="metadata"
            />
            <div className="media-meta">
              <div className="media-name" title={m.original_name}>
                {m.original_name}
              </div>
              <div className="media-sub">
                {m.missing
                  ? "文件缺失"
                  : `${m.duration.toFixed(1)}s · ${m.width}x${m.height}`}
                {!m.has_audio && !m.missing && " · 无声音"}
              </div>
              <button disabled={m.missing} onClick={() => onAdd(m)}>
                加入时间线
              </button>
            </div>
          </li>
        ))}
      </ul>
      <p className="hint">自带 3 段 4 秒示例素材（红/绿/蓝，含不同频率提示音）。导入会复制到 media 目录，不改动原文件。</p>
    </div>
  );
}
