import { useRef } from "react";
import { Film, Plus, Upload, XCircle } from "lucide-react";
import { useEditor } from "../store";

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${secs}`;
}

export function MediaLibrary() {
  const media = useEditor((state) => state.media);
  const addClip = useEditor((state) => state.addClip);
  const uploadMedia = useEditor((state) => state.uploadMedia);
  const cancelUpload = useEditor((state) => state.cancelUpload);
  const busy = useEditor((state) => state.busy);
  const uploadingName = useEditor((state) => state.uploadingName);
  const project = useEditor((state) => state.project);
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <section className="panel library">
      <h2>
        <Film size={18} /> 素材库
      </h2>
      <div className="library-upload">
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          <Upload size={16} /> 导入视频（原文件不会被覆盖）
        </button>
        {busy && uploadingName && (
          <button type="button" className="danger" onClick={cancelUpload}>
            <XCircle size={15} /> 取消载入
          </button>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void uploadMedia(file);
          event.target.value = "";
        }}
      />
      <ul className="media-list">
        {media.map((item) => (
          <li key={item.id} className={item.is_sample ? "sample" : ""}>
            <div>
              <strong title={item.filename}>{item.filename}</strong>
              <span className="meta">
                {formatDuration(item.duration)} · {item.has_audio ? "有声" : "无声"}
                {item.is_sample ? " · 内置样例" : ""}
              </span>
            </div>
            <button
              type="button"
              title="加到时间线末尾"
              disabled={!project || busy}
              onClick={() => addClip(item.id)}
            >
              <Plus size={16} />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
