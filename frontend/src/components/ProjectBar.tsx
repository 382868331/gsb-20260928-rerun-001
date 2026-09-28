import { useEffect, useState } from "react";
import { api } from "../api";
import type { ProjectSummary, RenderJob } from "../types";

interface Props {
  projectName: string;
  setProjectName: (s: string) => void;
  dirty: boolean;
  onSave: () => void;
  onOpen: (name: string) => void;
  onRender: () => void;
  onPreview: () => void;
  onCancel: () => void;
  job: RenderJob | null;
}

export function ProjectBar(p: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);

  useEffect(() => {
    const tick = () => api.projects()
      .then((r) => setProjects(r.projects)).catch(() => {});
    tick();
    const id = window.setInterval(tick, 4000);
    return () => window.clearInterval(id);
  }, []);

  const running = p.job?.status === "running";

  return (
    <header className="topbar">
      <div className="brand">🎬 ClipFlow</div>
      <input
        className="project-name"
        value={p.projectName}
        onChange={(e) => p.setProjectName(e.target.value)}
        placeholder="工程名称"
      />
      <span className={p.dirty ? "dirty" : "saved"}>
        {p.dirty ? "● 未保存" : "○ 已保存"}
      </span>
      <button onClick={p.onSave}>保存工程</button>
      <select
        value=""
        onChange={(e) => e.target.value && p.onOpen(e.target.value)}
        title="打开历史工程"
      >
        <option value="">打开工程…</option>
        {projects.map((pr) => (
          <option key={pr.name} value={pr.name}>
            {pr.name}（{pr.clip_count} 段 / {pr.duration.toFixed(1)}s）
          </option>
        ))}
      </select>
      <span className="spacer" />
      <button className="secondary" onClick={p.onPreview} disabled={running}>
        生成预览
      </button>
      {running ? (
        <button className="danger" onClick={p.onCancel}>
          取消{p.job?.kind === "preview" ? "预览" : "导出"}（{Math.round(p.job!.progress * 100)}%）
        </button>
      ) : (
        <button className="primary" onClick={p.onRender}>导出成片</button>
      )}
    </header>
  );
}
