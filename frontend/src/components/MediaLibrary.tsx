import { useRef } from "react";
import { Check, Film, Loader2, Plus, RotateCcw, Upload, XCircle } from "lucide-react";
import { useEditor } from "../store";

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${secs}`;
}

export function MediaLibrary() {
  const media = useEditor((state) => state.media);
  const imports = useEditor((state) => state.imports);
  const addClip = useEditor((state) => state.addClip);
  const uploadMedia = useEditor((state) => state.uploadMedia);
  const retryImport = useEditor((state) => state.retryImport);
  const cancelImport = useEditor((state) => state.cancelImport);
  const project = useEditor((state) => state.project);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const reading = imports.some((task) => task.status === "reading");

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
          if (files.length > 0) void uploadMedia(files);
          event.target.value = "";
        }}
      />
      {imports.length > 0 && (
        <ul className="import-list">
          {imports.map((task) => (
            <li key={task.key} className={`import-${task.status}`}>
              <div>
                <strong>{task.name}</strong>
                <span className="meta">
                  {task.status === "reading" && "读取中…"}
                  {task.status !== "reading" && (task.detail ?? "")}
                </span>
              </div>
              {task.status === "reading" && (
                <button
                  type="button"
                  className="danger"
                  title="取消该文件的载入"
                  onClick={() => cancelImport(task.key)}
                >
                  <XCircle size={15} />
                </button>
              )}
              {(task.status === "failed" || task.status === "cancelled") && (
                <button
                  type="button"
                  title="重试该文件"
                  onClick={() => void retryImport(task.key)}
                >
                  <RotateCcw size={15} />
                </button>
              )}
              {task.status === "reading" && <Loader2 size={15} className="spin" />}
              {task.status === "done" && <Check size={15} className="done-icon" />}
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
      {reading && <span className="meta">正在读取素材，可同时新建或切换工程</span>}
    </section>
  );
}
