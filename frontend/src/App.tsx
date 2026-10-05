import { useEffect } from "react";
import { ExportPanel } from "./components/ExportPanel";
import { MediaLibrary } from "./components/MediaLibrary";
import { Preview } from "./components/Preview";
import { ProjectBar } from "./components/ProjectBar";
import { ReplacementPanel } from "./components/ReplacementPanel";
import { Timeline } from "./components/Timeline";
import { useEditor } from "./store";

export default function App() {
  const loadMedia = useEditor((state) => state.loadMedia);
  const loadProjects = useEditor((state) => state.loadProjects);
  const newProject = useEditor((state) => state.newProject);
  const openProject = useEditor((state) => state.openProject);
  const project = useEditor((state) => state.project);
  const message = useEditor((state) => state.message);
  const setMessage = useEditor((state) => state.setMessage);

  useEffect(() => {
    void loadMedia();
    void loadProjects().then(() => {
      if (useEditor.getState().project) return;
      const latest = useEditor.getState().projects.find((item) => item.clips.length > 0)
        ?? useEditor.getState().projects[0];
      if (latest) void openProject(latest.id);
      else void newProject();
    });
  }, [loadMedia, loadProjects, newProject, openProject]);

  return (
    <div className="app-shell">
      <ProjectBar />
      {message && (
        <div className="toast" onClick={() => setMessage(null)}>
          {message}
        </div>
      )}
      <main className="workspace">
        <aside className="sidebar">
          <MediaLibrary />
          <ReplacementPanel />
          <ExportPanel />
        </aside>
        <section className="main-area">
          <Preview />
          <Timeline />
          {!project && <div className="loading">正在初始化或打开工程…</div>}
        </section>
      </main>
    </div>
  );
}
