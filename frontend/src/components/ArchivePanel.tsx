import { useEffect, useRef, useState } from "react";
import {
  Download,
  FileArchive,
  FolderInput,
  Loader2,
  XCircle,
} from "lucide-react";
import { useEditor } from "../store";

export function ArchivePanel() {
  const project = useEditor((state) => state.project);
  const job = useEditor((state) => state.packageJob);
  const startPackage = useEditor((state) => state.startPackage);
  const pollPackage = useEditor((state) => state.pollPackage);
  const cancelPackage = useEditor((state) => state.cancelPackage);
  const importPackage = useEditor((state) => state.importPackage);
  const busy = useEditor((state) => state.busy);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    if (job?.status !== "running" && job?.status !== "pending") return;
    const timer = setInterval(() => void pollPackage(), 500);
    return () => clearInterval(timer);
  }, [job?.id, job?.status, pollPackage]);

  const active = job?.status === "running" || job?.status === "pending";

  return (
    <section className="panel archive-panel">
      <h2>
        <FileArchive size={18} /> 工程打包 / 离线恢复
      </h2>
      <p className="meta">
        只打包时间线实际引用的素材，相同内容只存一份；生成的 zip 可搬到空素材库继续编辑。
      </p>
      <div className="export-actions">
        <button
          type="button"
          disabled={!project || active || busy}
          onClick={() => void startPackage()}
        >
          {active ? <Loader2 className="spin" size={16} /> : <Download size={16} />}
          打包当前工程
        </button>
        {active && (
          <button type="button" className="danger" onClick={() => void cancelPackage()}>
            <XCircle size={16} /> 取消打包
          </button>
        )}
        <button
          type="button"
          className="secondary"
          disabled={importing}
          onClick={() => inputRef.current?.click()}
        >
          {importing ? <Loader2 className="spin" size={16} /> : <FolderInput size={16} />}
          导入工程包恢复
        </button>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".zip,application/zip"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            setImporting(true);
            void importPackage(file).finally(() => setImporting(false));
          }
          event.target.value = "";
        }}
      />
      {job && (
        <div className="job">
          <div className="job-row">
            <span>状态：{statusLabel(job.status)}</span>
            {job.progress > 0 && <span>{Math.round(job.progress * 100)}%</span>}
          </div>
          {(job.status === "running" || job.status === "pending") && (
            <progress value={job.progress} max={1} />
          )}
          {job.status === "failed" && <p className="error">{job.error}</p>}
          {job.status === "cancelled" && (
            <p className="warning">已取消。工程与素材未受影响，可重新打包。</p>
          )}
          {job.status === "done" && job.output_url && (
            <a className="download-link" href={job.output_url} target="_blank" rel="noreferrer">
              <Download size={16} /> 下载工程包 zip
            </a>
          )}
        </div>
      )}
    </section>
  );
}

function statusLabel(status: string): string {
  return {
    pending: "排队中",
    running: "打包中",
    done: "完成",
    failed: "失败",
    cancelled: "已取消",
  }[status] ?? status;
}
