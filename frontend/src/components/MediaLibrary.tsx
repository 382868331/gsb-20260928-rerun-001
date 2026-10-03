import { useRef } from "react";
import { Film, Plus, RotateCcw, Upload, XCircle } from "lucide-react";
import { useEditor } from "../store";

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${secs}`;
}

export function MediaLibrary() {
  const media = useEditor((state) => state.media);
  const uploads = useEditor((state) => state.uploads);
  const addClip = useEditor((state) => state.addClip);
  const uploadMedia = useEditor((state) => state.uploadMedia);
  const retryUpload = useEditor((state) => state.retryUpload);
  const cancelUpload = useEditor((state) => state.cancelUpload);
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
          onClick={() => inputRef.current?.click()}
        >
          <Upload size={16} /> 导入视频（原文件不会被覆盖）
        </button>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        multiple
        hidden
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length > 0) uploadMedia(files);
          event.target.value = "";
        }}
      />
      {uploads.length > 0 && (
        <ul className="upload-list">
          {uploads.map((entry) => (
            <li key={entry.key} className={`upload-${entry.status}`}>
              <div>
                <strong title={entry.name}>{entry.name}</strong>
                <span className="meta">
                  {entry.status === "reading" && "读取中…"}
                  {entry.status === "done" && "已入库"}
                  {entry.status === "failed" && `失败：${entry.error}`}
                </span>
              </div>
              {entry.status === "reading" && (
                <button
                  type="button"
                  className="danger"
                  title="取消载入"
                  onClick={() => cancelUpload(entry.key)}
                >
                  <XCircle size={15} />
                </button>
              )}
              {entry.status === "failed" && (
                <>
                  <button
                    type="button"
                    title="重试该文件"
                    onClick={() => retryUpload(entry.key)}
                  >
                    <RotateCcw size={15} /> 重试
                  </button>
                  <button
                    type="button"
                    className="danger"
                    title="移除失败条目"
                    onClick={() => cancelUpload(entry.key)}
                  >
                    <XCircle size={15} />
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
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
              disabled={!project}
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
