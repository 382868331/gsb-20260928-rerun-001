import { FolderOpen, Save } from "lucide-react";
import { useEditor } from "../store";

export function ProjectBar() {
  const project = useEditor((state) => state.project);
  const projects = useEditor((state) => state.projects);
  const save = useEditor((state) => state.save);
  const openProject = useEditor((state) => state.openProject);
  const newProject = useEditor((state) => state.newProject);
  const renameProject = useEditor((state) => state.renameProject);
  const busy = useEditor((state) => state.busy);

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
          </>
        )}
      </div>
    </header>
  );
}
