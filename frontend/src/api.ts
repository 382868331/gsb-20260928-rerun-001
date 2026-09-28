import type {
  Clip, MediaItem, Project, ProjectSummary, RenderJob, Timeline,
} from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(data.error || `请求失败 ${res.status}`);
  return data as T;
}

export const api = {
  media: () => req<{ media: MediaItem[] }>("/api/media"),
  samples: () =>
    req<{ media: MediaItem[] }>("/api/media/samples", { method: "POST" }),
  upload(file: File, onProgress?: (p: number) => void) {
    return new Promise<MediaItem>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/media/import");
      xhr.onload = () => {
        try {
          const data = JSON.parse(xhr.responseText);
          xhr.status < 400 ? resolve(data) : reject(new Error(data.error));
        } catch (e) {
          reject(e);
        }
      };
      xhr.onerror = () => reject(new Error("上传失败"));
      xhr.upload.onprogress = (e) =>
        e.lengthComputable && onProgress?.(e.loaded / e.total);
      const fd = new FormData();
      fd.append("file", file);
      xhr.send(fd);
    });
  },
  projects: () => req<{ projects: ProjectSummary[] }>("/api/projects"),
  loadProject: (name: string) => req<Project>(`/api/projects/${encodeURIComponent(name)}`),
  saveProject: (name: string, timeline: Timeline) =>
    req<Project>(`/api/projects/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timeline }),
    }),
  render: (kind: "preview" | "export", projectName: string, timeline: Timeline) =>
    req<RenderJob>("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, project_name: projectName, timeline }),
    }),
  job: (id: string) => req<RenderJob>(`/api/render/${id}`),
  cancel: (id: string) =>
    req<{ cancelled: boolean }>(`/api/render/${id}/cancel`, { method: "POST" }),
};

export function newClip(media: MediaItem): Clip {
  const d = Math.min(media.duration, 3);
  return {
    id: `clip-${Math.random().toString(36).slice(2, 9)}`,
    media_id: media.id,
    in_point: 0,
    out_point: d,
    speed: 1,
    volume: 1,
    color: "none",
  };
}
