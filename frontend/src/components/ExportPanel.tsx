import { useEffect, useState } from "react";
import { Download, Loader2, XCircle } from "lucide-react";
import { useEditor } from "../store";

export function ExportPanel() {
  const project = useEditor((state) => state.project);
  const timeline = useEditor((state) => state.timeline);
  const job = useEditor((state) => state.exportJob);
  const startExport = useEditor((state) => state.startExport);
  const pollExport = useEditor((state) => state.pollExport);
  const cancelExport = useEditor((state) => state.cancelExport);
  const busy = useEditor((state) => state.busy);
  const selectedClipId = useEditor((state) => state.selectedClipId);
  const [rangeStart, setRangeStart] = useState(0);
  const [rangeEnd, setRangeEnd] = useState<number | "">("");

  useEffect(() => {
    if (job?.status !== "running" && job?.status !== "pending") return;
    const timer = setInterval(() => void pollExport(), 600);
    return () => clearInterval(timer);
  }, [job?.id, job?.status, pollExport]);

  const blocked = !project || project.clips.length === 0;
  const missingCount = timeline?.missing_media_ids.length ?? 0;
  const active = job?.status === "running" || job?.status === "pending";
  const selectedClip = timeline?.clips.find((clip) => clip.clip_id === selectedClipId);
  const exportEnd = rangeEnd === "" ? undefined : Math.min(Math.max(0, rangeEnd), timeline?.duration ?? 0);
  const exportRangeValid = !timeline || (rangeStart >= 0 && (exportEnd === undefined || exportEnd > rangeStart));

  return (
    <section className="panel export-panel">
      <h2>导出成片</h2>
      <p className="meta">
        导出与预览使用同一条时间线计算结果（顺序 + 入出点 + 分段速度 + 转场），画面和声音逐段一起裁切后再拼接。
      </p>
      <div className="export-range">
        <label>开始 <input type="number" min={0} step={0.05} value={rangeStart} onChange={(e) => setRangeStart(Number(e.target.value))} /></label>
        <label>结束 <input type="number" min={0} step={0.05} placeholder="结尾" value={rangeEnd} onChange={(e) => setRangeEnd(e.target.value === "" ? "" : Number(e.target.value))} /></label>
        <button type="button" className="secondary" disabled={!selectedClip}
          onClick={() => selectedClip && (setRangeStart(selectedClip.transition_start), setRangeEnd(selectedClip.end))}>
          使用所选片段
        </button>
      </div>
      {missingCount > 0 && (
        <p className="warning">
          存在 {missingCount} 段缺失素材，恢复素材文件后即可导出；工程已自动保存。
        </p>
      )}
      <div className="export-actions">
        <button
          type="button"
          disabled={blocked || active || busy || missingCount > 0 || !exportRangeValid}
          onClick={() => void startExport(rangeStart, exportEnd)}
        >
          {active ? <Loader2 className="spin" size={16} /> : <Download size={16} />}
          导出 MP4
        </button>
        {active && (
          <button type="button" className="danger" onClick={() => void cancelExport()}>
            <XCircle size={16} /> 中断导出
          </button>
        )}
      </div>
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
            <p className="warning">已中断。工程保留，可直接再次导出。</p>
          )}
          {job.status === "done" && job.output_url && (
            <a className="download-link" href={job.output_url} target="_blank" rel="noreferrer">
              <Download size={16} /> 下载成片
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
    running: "渲染中",
    done: "完成",
    failed: "失败",
    cancelled: "已取消",
  }[status] ?? status;
}
