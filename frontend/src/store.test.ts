import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Clip, MediaInfo, Project, Timeline } from "./types";

vi.mock("./api", () => ({
  api: {
    listMedia: vi.fn(),
    uploadMedia: vi.fn(),
    listProjects: vi.fn(),
    createProject: vi.fn(),
    getProject: vi.fn(),
    saveProject: vi.fn(),
    timeline: vi.fn(),
    startExport: vi.fn(),
    getExport: vi.fn(),
    cancelExport: vi.fn(),
    listExports: vi.fn(),
    mediaStreamUrl: vi.fn(),
  },
}));

import { api } from "./api";
import { useEditor } from "./store";

const mocked = vi.mocked(api);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function settle() {
  for (let index = 0; index < 30; index += 1) {
    await vi.advanceTimersByTimeAsync(0);
  }
}

function media(id: string, duration = 4): MediaInfo {
  return {
    id,
    filename: id + ".mp4",
    duration,
    width: 640,
    height: 360,
    has_audio: true,
    is_sample: false,
  };
}

function clip(id: string, mediaId: string, out = 2): Clip {
  return {
    id,
    media_id: mediaId,
    source_in: 0,
    source_out: out,
    volume: 1,
    transition: 0,
    speed_segments: [],
  };
}

function project(id: string, clips: Clip[] = []): Project {
  return {
    id,
    name: "工程-" + id,
    clips,
    version: 1,
    created_at: "2026-10-04T00:00:00",
    updated_at: "2026-10-04T00:00:00",
  };
}

function emptyTimeline(): Timeline {
  return { duration: 0, clips: [], missing_media_ids: [] };
}

/** Upload mock whose promise rejects when the caller aborts, like fetch. */
function abortAwareUpload<T>(result: {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}) {
  return (_file: File, signal?: AbortSignal) =>
    new Promise<T>((resolve, reject) => {
      signal?.addEventListener("abort", () => {
        reject(Object.assign(new Error("已取消"), { name: "AbortError" }));
      });
      result.promise.then(resolve, reject);
    });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  mocked.listProjects.mockResolvedValue([]);
  mocked.listMedia.mockResolvedValue([]);
  mocked.timeline.mockImplementation(async () => emptyTimeline());
  useEditor.setState({
    media: [],
    imports: [],
    projects: [],
    project: null,
    timeline: null,
    selectedClipId: null,
    exportJob: null,
    message: null,
    busy: false,
    canUndo: false,
    canRedo: false,
    lastEditLabel: null,
  });
});

describe("素材库生命周期独立于工程生命周期", () => {
  it("素材列表加载期间新建工程，迟到的列表结果仍然生效", async () => {
    const listResult = deferred<MediaInfo[]>();
    mocked.listMedia.mockReturnValue(listResult.promise);
    mocked.createProject.mockImplementation(async (name: string) =>
      project("p-new-" + name),
    );

    const loading = useEditor.getState().loadMedia();
    await useEditor.getState().newProject();
    expect(useEditor.getState().project?.id).toMatch(/^p-new-/);

    listResult.resolve([media("m-a"), media("m-b")]);
    await loading;
    await settle();

    expect(useEditor.getState().media.map((item) => item.id)).toEqual([
      "m-a",
      "m-b",
    ]);
    expect(useEditor.getState().project?.clips).toEqual([]);
  });

  it("并发的两次列表请求，以最后发起的为准", async () => {
    const first = deferred<MediaInfo[]>();
    const second = deferred<MediaInfo[]>();
    mocked.listMedia
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    const firstLoad = useEditor.getState().loadMedia();
    const secondLoad = useEditor.getState().loadMedia();
    second.resolve([media("m-new")]);
    await secondLoad;
    first.resolve([media("m-stale")]);
    await firstLoad;
    await settle();

    expect(useEditor.getState().media.map((item) => item.id)).toEqual(["m-new"]);
  });

  it("导入等待期间新建工程，成功素材仍进入素材库且不写入任何工程", async () => {
    const opened = project("p-old", [clip("c1", "m-x")]);
    mocked.getProject.mockResolvedValue(opened);
    mocked.timeline.mockResolvedValue(emptyTimeline());
    mocked.createProject.mockImplementation(async () => project("p-fresh"));
    const uploadResult = deferred<MediaInfo>();
    mocked.uploadMedia.mockImplementation(abortAwareUpload(uploadResult));
    mocked.listMedia.mockResolvedValue([media("m-x"), media("m-new")]);

    await useEditor.getState().openProject("p-old");
    expect(useEditor.getState().project?.id).toBe("p-old");

    const importing = useEditor.getState().uploadMedia(new File(["x"], "new.mp4"));
    expect(useEditor.getState().imports[0]?.status).toBe("reading");

    await useEditor.getState().newProject();
    expect(useEditor.getState().project?.id).toBe("p-fresh");

    uploadResult.resolve(media("m-new"));
    await importing;
    await settle();

    const state = useEditor.getState();
    expect(state.imports[0]?.status).toBe("done");
    expect(state.media.map((item) => item.id)).toEqual(["m-x", "m-new"]);
    // 旧工程的片段选择、撤销历史都不能带进新工程
    expect(state.project?.clips).toEqual([]);
    expect(state.selectedClipId).toBeNull();
    expect(state.canUndo).toBe(false);
  });

  it("一次导入同时包含已存在、读取中与失败的文件，各自独立收尾", async () => {
    const slow = deferred<MediaInfo>();
    mocked.uploadMedia.mockImplementation((file: File, signal?: AbortSignal) => {
      if (file.name === "dup.mp4") return Promise.resolve(media("m-dup"));
      if (file.name === "bad.mp4")
        return Promise.reject(new Error("无法读取视频：缺少视频轨道"));
      return abortAwareUpload(slow)(file, signal);
    });
    mocked.listMedia.mockResolvedValue([media("m-dup"), media("m-slow")]);
    useEditor.setState({ media: [media("m-dup")] });

    const importing = useEditor
      .getState()
      .uploadMedia([
        new File(["a"], "dup.mp4"),
        new File(["b"], "slow.mp4"),
        new File(["c"], "bad.mp4"),
      ]);
    await settle();
    let imports = useEditor.getState().imports;
    expect(imports.find((task) => task.name === "dup.mp4")?.status).toBe("done");
    expect(imports.find((task) => task.name === "dup.mp4")?.detail).toContain("复用");
    expect(imports.find((task) => task.name === "bad.mp4")?.status).toBe("failed");
    expect(imports.find((task) => task.name === "slow.mp4")?.status).toBe("reading");

    slow.resolve(media("m-slow"));
    await importing;
    await settle();
    imports = useEditor.getState().imports;
    expect(imports.find((task) => task.name === "slow.mp4")?.status).toBe("done");
    expect(useEditor.getState().media.map((item) => item.id)).toContain("m-slow");
  });
});

describe("取消与重试", () => {
  it("取消后可单独重试，重试成功", async () => {
    const first = deferred<MediaInfo>();
    mocked.uploadMedia.mockImplementationOnce(abortAwareUpload(first));
    mocked.listMedia.mockResolvedValue([media("m-r")]);

    const importing = useEditor.getState().uploadMedia(new File(["x"], "r.mp4"));
    const key = useEditor.getState().imports[0].key;
    useEditor.getState().cancelImport(key);
    await importing;
    await settle();
    expect(useEditor.getState().imports[0].status).toBe("cancelled");

    const retryResult = deferred<MediaInfo>();
    mocked.uploadMedia.mockImplementationOnce(abortAwareUpload(retryResult));
    const retrying = useEditor.getState().retryImport(key);
    expect(useEditor.getState().imports[0].status).toBe("reading");
    retryResult.resolve(media("m-r"));
    await retrying;
    await settle();

    const task = useEditor.getState().imports[0];
    expect(task.status).toBe("done");
    expect(task.mediaId).toBe("m-r");
  });

  it("取消后旧请求的迟到成功不得覆盖重试结果", async () => {
    // 第一次尝试忽略 abort（服务端已开始写库），随后用户取消并重试。
    const stale = deferred<MediaInfo>();
    mocked.uploadMedia.mockImplementationOnce(() => stale.promise);
    const importing = useEditor.getState().uploadMedia(new File(["x"], "s.mp4"));
    const key = useEditor.getState().imports[0].key;
    useEditor.getState().cancelImport(key);

    const retryResult = deferred<MediaInfo>();
    mocked.uploadMedia.mockImplementationOnce(abortAwareUpload(retryResult));
    mocked.listMedia.mockResolvedValue([media("m-retry")]);
    const retrying = useEditor.getState().retryImport(key);
    retryResult.resolve(media("m-retry"));
    await retrying;
    await settle();
    expect(useEditor.getState().imports[0].mediaId).toBe("m-retry");

    stale.resolve(media("m-stale"));
    await importing;
    await settle();
    const task = useEditor.getState().imports[0];
    expect(task.status).toBe("done");
    expect(task.mediaId).toBe("m-retry");
  });
});

describe("工程生命周期", () => {
  it("连续切换两个工程，迟到的旧工程响应不得覆盖新工程", async () => {
    const projectA = project("p-a", [clip("c-a", "m-a")]);
    const projectB = project("p-b", [clip("c-b", "m-b")]);
    const getA = deferred<Project>();
    const getB = deferred<Project>();
    const timelineA = deferred<Timeline>();
    const timelineB = deferred<Timeline>();
    mocked.getProject.mockImplementation(async (id: string) =>
      id === "p-a" ? getA.promise : getB.promise,
    );
    mocked.timeline.mockImplementation(async (id: string) =>
      id === "p-a" ? timelineA.promise : timelineB.promise,
    );

    const openingA = useEditor.getState().openProject("p-a");
    const openingB = useEditor.getState().openProject("p-b");
    getB.resolve(projectB);
    timelineB.resolve({ ...emptyTimeline(), duration: 2 });
    await openingB;
    getA.resolve(projectA);
    timelineA.resolve({ ...emptyTimeline(), duration: 9 });
    await openingA;
    await settle();

    const state = useEditor.getState();
    expect(state.project?.id).toBe("p-b");
    expect(state.project?.clips.map((item) => item.id)).toEqual(["c-b"]);
    expect(state.timeline?.duration).toBe(2);
  });

  it("新建工程不携带旧工程的选中、速度设置与撤销历史", async () => {
    const withSpeed = project("p-speed", [
      {
        ...clip("c1", "m-a"),
        speed_segments: [{ source_start: 0, source_end: 1, rate: 2 }],
      },
    ]);
    mocked.getProject.mockResolvedValue(withSpeed);
    mocked.timeline.mockResolvedValue(emptyTimeline());
    mocked.createProject.mockImplementation(async () => project("p-blank"));
    useEditor.setState({ media: [media("m-a")] });

    await useEditor.getState().openProject("p-speed");
    useEditor.getState().selectClip("c1");
    useEditor.getState().setClipSpeed("c1", 0, 1, 0.5);
    expect(useEditor.getState().canUndo).toBe(true);

    await useEditor.getState().newProject();
    const state = useEditor.getState();
    expect(state.project?.clips).toEqual([]);
    expect(state.selectedClipId).toBeNull();
    expect(state.canUndo).toBe(false);
    expect(state.canRedo).toBe(false);

    // 切回旧工程，其自身历史仍在，互不串扰
    await useEditor.getState().openProject("p-speed");
    expect(useEditor.getState().canUndo).toBe(true);
  });

  it("空工程、删除最后片段与撤销均可操作", async () => {
    mocked.createProject.mockImplementation(async () => project("p-empty"));
    useEditor.setState({ media: [media("m-a")] });
    await useEditor.getState().newProject();

    useEditor.getState().addClip("m-missing");
    expect(useEditor.getState().message).toContain("尚未完成载入");
    useEditor.getState().removeClip("nope");
    expect(useEditor.getState().message).toContain("没有生效");
    useEditor.getState().undo();
    expect(useEditor.getState().project?.clips).toEqual([]);

    useEditor.getState().addClip("m-a");
    expect(useEditor.getState().project?.clips).toHaveLength(1);
    const clipId = useEditor.getState().project!.clips[0].id;
    useEditor.getState().removeClip(clipId);
    expect(useEditor.getState().project?.clips).toEqual([]);
    expect(useEditor.getState().timeline?.duration).toBe(0);
    expect(useEditor.getState().selectedClipId).toBeNull();

    useEditor.getState().undo();
    expect(useEditor.getState().project?.clips).toHaveLength(1);
    useEditor.getState().redo();
    expect(useEditor.getState().project?.clips).toEqual([]);
  });

  it("防抖保存与导出都以最终确认的片段和速度为准", async () => {
    const base = project("p-final", [clip("c1", "m-a", 4)]);
    mocked.getProject.mockResolvedValue(base);
    mocked.timeline.mockResolvedValue(emptyTimeline());
    mocked.saveProject.mockImplementation(
      async (id: string, name: string, clips: Clip[]) => ({
        ...project(id, clips),
        name,
        version: 2,
      }),
    );
    mocked.startExport.mockImplementation(async (id: string) => ({
      id: "job-1",
      project_id: id,
      status: "pending",
      progress: 0,
      output_url: null,
      error: null,
      created_at: "t",
      finished_at: null,
    }));
    useEditor.setState({ media: [media("m-a")] });

    await useEditor.getState().openProject("p-final");
    useEditor.getState().setClipSpeed("c1", 0, 2, 0.5);
    useEditor.getState().trimClip("c1", "out", 3);
    await vi.advanceTimersByTimeAsync(700);
    await settle();

    expect(mocked.saveProject).toHaveBeenCalledTimes(1);
    const savedClips = mocked.saveProject.mock.calls[0][2];
    expect(savedClips[0].source_out).toBe(3);
    expect(savedClips[0].speed_segments).toEqual([
      { source_start: 0, source_end: 2, rate: 0.5 },
    ]);

    await useEditor.getState().startExport();
    await settle();
    expect(mocked.startExport).toHaveBeenCalledWith("p-final", undefined);
    // 导出前再次保存的仍是最终片段
    const lastSave = mocked.saveProject.mock.calls.at(-1)![2];
    expect(lastSave[0].source_out).toBe(3);
    expect(lastSave[0].speed_segments[0].rate).toBe(0.5);
  });
});
