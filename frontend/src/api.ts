import type {
  Clip,
  ExportInfo,
  ImportResult,
  MediaInfo,
  PackageInfo,
  Project,
  ReplacementPreview,
  ReplacementRequest,
  ReplacementResult,
  Timeline,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data.detail ?? detail;
    } catch {
      // keep status text
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const api = {
  listMedia: () => request<MediaInfo[]>("/api/media"),
  uploadMedia: async (file: File, signal?: AbortSignal): Promise<MediaInfo> => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch("/api/media", {
      method: "POST",
      body: form,
      signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail ?? "上传失败");
    }
    return response.json();
  },
  listProjects: () => request<Project[]>("/api/projects"),
  createProject: (name: string, clips: Clip[]) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name, clips }),
    }),
  getProject: (id: string) => request<Project>(`/api/projects/${id}`),
  saveProject: (id: string, name: string, clips: Clip[]) =>
    request<Project>(`/api/projects/${id}`, {
      method: "PUT",
      body: JSON.stringify({ name, clips }),
    }),
  timeline: (id: string) =>
    request<Timeline>(`/api/projects/${id}/timeline`),
  startExport: (id: string, range?: { start: number; end: number }) =>
    request<ExportInfo>(`/api/projects/${id}/exports`, {
      method: "POST",
      body: JSON.stringify(range ?? {}),
    }),
  getExport: (id: string) => request<ExportInfo>(`/api/exports/${id}`),
  cancelExport: (id: string) =>
    request<{ cancelled: boolean }>(`/api/exports/${id}/cancel`, {
      method: "POST",
    }),
  listExports: (id: string) =>
    request<ExportInfo[]>(`/api/projects/${id}/exports`),
  startPackage: (projectId: string) =>
    request<PackageInfo>(`/api/projects/${projectId}/packages`, { method: "POST" }),
  getPackage: (jobId: string) => request<PackageInfo>(`/api/packages/${jobId}`),
  cancelPackage: (jobId: string) =>
    request<{ cancelled: boolean }>(`/api/packages/${jobId}/cancel`, { method: "POST" }),
  packageDownloadUrl: (jobId: string) => `/api/packages/${jobId}/download`,
  importProject: async (file: File): Promise<ImportResult> => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch("/api/projects/import", {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const detail = data.detail;
      throw new Error(
        typeof detail === "string" ? detail : "工程包导入失败",
      );
    }
    return response.json();
  },
  mediaStreamUrl: (mediaId: string) => `/api/media/${mediaId}/stream`,
  previewReplacement: (projectId: string, body: ReplacementRequest) =>
    request<ReplacementPreview>(`/api/projects/${projectId}/replacement/preview`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  confirmReplacement: (projectId: string, body: ReplacementRequest) =>
    request<ReplacementResult>(`/api/projects/${projectId}/replacement/confirm`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
