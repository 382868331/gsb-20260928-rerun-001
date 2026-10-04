import { useEffect, useRef } from "react";
import { Download, Loader2, Package, PackageOpen, Save, XCircle } from "lucide-react";
import { api } from "../api";
import { useEditor } from "../store";

export function ProjectBar() {
  const project = useEditor((state) => state.project);
  const projects = useEditor((state) => state.projects);
  const save = useEditor((state) => state.save);
  const openProject = useEditor((state) => state.openProject);
  const newProject = useEditor((state) => state.newProject);
  const renameProject = useEditor((state) => state.renameProject);
  const busy = useEditor((state) => state.busy);
  const packageJob = useEditor((state) => state.packageJob);
  const startPackage = useEditor((state) => state.startPackage);
  const pollPackage = useEditor((state) => state.pollPackage);
  const cancelPackage = useEditor((state) => state.cancelPackage);
  const importPackage = useEditor((state) => state.importPackage);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const packing =
    packageJob?.status === "running" || packageJob?.status === "pending";

  useEffect(() => {
    if (!packing) return;
    const timer = setInterval(() => void pollPackage(), 500);
    return () => clearInterval(timer);
  }, [packing, pollPackage]);

  return (
    <header className="topbar">
      <div className="brand">剪片台 · 素材导入 / 裁切 / 排序 / 预览 / 导出</div>
      <div className="project-controls">
        <button type="button" className="secondary" onClick={() => void newProject()}>
          新建工程
        </button>
        <select
          value={project?.id ?? ""}
          onChange={(event) => event.target.value && void openProject(event.target.value)}
        >
          <option value="" disabled>
            打开已保存的工程…
          </option>
          {projects.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}（{item.clips.length} 段，v{item.version}）
            </option>
          ))}
        </select>
        {project && (
          <>
            <input
              className="project-name"
              value={project.name}
              onChange={(event) => renameProject(event.target.value)}
            />
            <button type="button" disabled={busy} onClick={() => void save()}>
              <Save size={15} /> 保存
            </button>
            <span className="meta">v{project.version}</span>
            <button
              type="button"
              className="secondary"
              disabled={busy || packing}
              title="把工程和实际引用的素材打成一个可搬运文件"
              onClick={() => void startPackage()}
            >
              {packing ? <Loader2 className="spin" size={15} /> : <Package size={15} />}
              导出工程包
            </button>
            {packing && (
              <button type="button" className="danger" onClick={() => void cancelPackage()}>
                <XCircle size={15} /> 取消打包
              </button>
            )}
            {packageJob?.status === "done" && packageJob.output_url && (
              <a
                className="download-link"
                href={api.packageDownloadUrl(packageJob.id)}
                download
              >
                <Download size={15} /> 下载工程包
              </a>
            )}
            {packageJob?.status === "failed" && (
              <span className="meta">打包失败：{packageJob.error}</span>
            )}
          </>
        )}
        <button
          type="button"
          className="secondary"
          disabled={busy}
          title="从工程包恢复工程及其素材到当前素材库"
          onClick={() => fileInput.current?.click()}
        >
          <PackageOpen size={15} /> 导入工程包
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".gsbpack,.zip,application/zip"
          style={{ display: "none" }}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void importPackage(file);
          }}
        />
      </div>
    </header>
  );
}
