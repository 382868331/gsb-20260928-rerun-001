import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MediaInfo, Project, Timeline } from "./types";

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
    confirmReplacement: vi.fn(),
    mediaStreamUrl: (id: string) => `/api/media/${id}/stream`,
  },
}));

import { api } from "./api";
import { __resetEditorForTests, useEditor } from "./store";

type MockedApi = {
  [K in keyof typeof api]: ReturnType<typeof vi.fn>;
};
const mockApi = api as unknown as MockedApi;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flush(rounds = 5) {
  for (let index = 0; index < rounds; index += 1) {
    await Promise.resolve();
  }
}

function makeMedia(id: string, filename = `${id}.mp4`): MediaInfo {
  return {
    id,
    filename,
    duration: 3,
    width: 320,
    height: 180,
    fps: 30,
    has_audio: true,
    is_sample: false,
  };
}

let projectCounter = 0;
function makeProject(id: string, clips: Project["clips"] = []): Project {
  return {
    id,
    name: `工程-${id}`,
    clips,
    version: 1,
    created_at: "2026-10-04T00:00:00+00:00",
    updated_at: "2026-10-04T00:00:00+00:00",
  };
}

function makeClip(id: string, mediaId: string): Project["clips"][number] {
  return {
    id,
    media_id: mediaId,
    source_in: 0,
    source_out: 2,
    volume: 1,
    transition: 0,
    speed_segments: [],
  };
}

function emptyTimeline(): Timeline {
  return { duration: 0, clips: [], missing_media_ids: [] };
}

function makeFile(name: string): File {
  return new File([name], name, { type: "video/mp4" });
}

// Simulated server-side media index: uploads push into it, listMedia
// returns a snapshot, mirroring the real backend's dedup index.
let serverMedia: MediaInfo[];
let serverProjects: Map<string, Project>;

beforeEach(() => {
  __resetEditorForTests();
  vi.clearAllMocks();
  serverMedia = [];
  serverProjects = new Map();
  mockApi.listMedia.mockImplementation(() => Promise.resolve([...serverMedia]));
  mockApi.listProjects.mockImplementation(() =>
    Promise.resolve([...serverProjects.values()]),
  );
  mockApi.createProject.mockImplementation((name: string) => {
    const project = { ...makeProject(`p${(projectCounter += 1)}`), name };
    serverProjects.set(project.id, project);
    return Promise.resolve(project);
  });
  mockApi.getProject.mockImplementation((id: string) => {
    const project = serverProjects.get(id);
    return project ? Promise.resolve(project) : Promise.reject(new Error("工程不存在"));
  });
  mockApi.saveProject.mockImplementation((id: string, name: string, clips: Project["clips"]) => {
    const previous = serverProjects.get(id) ?? makeProject(id);
    const saved = {
      ...previous,
      name,
      clips: structuredClone(clips),
      version: previous.version + 1,
    };
    serverProjects.set(id, saved);
    return Promise.resolve(saved);
  });
  mockApi.timeline.mockResolvedValue(emptyTimeline());
});

describe("素材库生命周期独立于工程会话", () => {
  it("素材列表请求期间新建工程，迟到返回仍然入库", async () => {
    const lateList = deferred<MediaInfo[]>();
    mockApi.listMedia.mockReturnValueOnce(lateList.promise);
    const loadPromise = useEditor.getState().loadMedia();

    await useEditor.getState().newProject();
    expect(useEditor.getState().project).not.toBeNull();

    const sample = makeMedia("m1", "已有素材.mp4");
    serverMedia.push(sample);
    lateList.resolve([sample]);
    await loadPromise;

    expect(useEditor.getState().media.map((item) => item.id)).toEqual(["m1"]);
  });

  it("较旧的素材列表响应不得覆盖较新的列表", async () => {
    const first = deferred<MediaInfo[]>();
    const second = deferred<MediaInfo[]>();
    mockApi.listMedia.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    const firstLoad = useEditor.getState().loadMedia();
    const secondLoad = useEditor.getState().loadMedia();

    second.resolve([makeMedia("new")]);
    await secondLoad;
    first.resolve([makeMedia("stale")]);
    await firstLoad;

    expect(useEditor.getState().media.map((item) => item.id)).toEqual(["new"]);
  });

  it("连续切换两个工程，旧工程的迟到响应不写入新工程", async () => {
    const projectA = makeProject("a", [makeClip("clip-a", "m1")]);
    const projectB = makeProject("b", [makeClip("clip-b", "m2")]);
    const timelineA: Timeline = {
      duration: 2,
      clips: [],
      missing_media_ids: [],
    };
    const lateProject = deferred<Project>();
    const lateTimeline = deferred<Timeline>();
    mockApi.getProject.mockImplementation((id: string) =>
      id === "a" ? lateProject.promise : Promise.resolve(projectB),
    );
    mockApi.timeline.mockImplementation((id: string) =>
      id === "a" ? lateTimeline.promise : Promise.resolve(emptyTimeline()),
    );

    const openA = useEditor.getState().openProject("a");
    await useEditor.getState().openProject("b");
    expect(useEditor.getState().project?.id).toBe("b");

    lateProject.resolve(projectA);
    lateTimeline.resolve(timelineA);
    await openA;
    await flush();

    const state = useEditor.getState();
    expect(state.project?.id).toBe("b");
    expect(state.project?.clips.map((clip) => clip.id)).toEqual(["clip-b"]);
    expect(state.timeline).toEqual(emptyTimeline());
  });
});

describe("混合导入：已有 / 读取中 / 失败", () => {
  it("成功素材在等待期间即可入库并加入当前时间线，失败素材可单独重试", async () => {
    const existing = makeMedia("dup", "已有.mp4");
    serverMedia.push(existing);
    await useEditor.getState().loadMedia();

    const slow = deferred<MediaInfo>();
    const retryOfBad = deferred<MediaInfo>();
    let badAttempts = 0;
    mockApi.uploadMedia.mockImplementation((file: File) => {
      if (file.name === "已有.mp4") return Promise.resolve(existing);
      if (file.name === "慢.mp4") return slow.promise;
      if (file.name === "坏.mp4") {
        badAttempts += 1;
        if (badAttempts === 1) return Promise.reject(new Error("无法读取视频"));
        return retryOfBad.promise;
      }
      return Promise.reject(new Error("unexpected file"));
    });

    useEditor.getState().uploadMedia([makeFile("已有.mp4"), makeFile("慢.mp4"), makeFile("坏.mp4")]);
    await flush();

    let state = useEditor.getState();
    const statusOf = (name: string) =>
      state.uploads.find((entry) => entry.name === name)?.status;
    expect(statusOf("已有.mp4")).toBe("done");
    expect(statusOf("慢.mp4")).toBe("reading");
    expect(statusOf("坏.mp4")).toBe("failed");
    expect(state.media.map((item) => item.id)).toContain("dup");

    // 等待期间可以新建工程并把已成功的素材加入当前时间线。
    await useEditor.getState().newProject();
    useEditor.getState().addClip("dup");
    state = useEditor.getState();
    expect(state.project?.clips).toHaveLength(1);
    expect(statusOf("慢.mp4")).toBe("reading");

    // 失败素材单独重试；重试挂在空中时另一个新导入先成功。
    const badKey = state.uploads.find((entry) => entry.name === "坏.mp4")!.key;
    useEditor.getState().retryUpload(badKey);
    const fresh = makeMedia("fresh", "新.mp4");
    mockApi.uploadMedia.mockImplementation((file: File) => {
      if (file.name === "新.mp4") {
        serverMedia.push(fresh);
        return Promise.resolve(fresh);
      }
      if (file.name === "坏.mp4") return retryOfBad.promise;
      if (file.name === "慢.mp4") return slow.promise;
      return Promise.reject(new Error("unexpected"));
    });
    useEditor.getState().uploadMedia([makeFile("新.mp4")]);
    await flush();
    expect(useEditor.getState().media.map((item) => item.id)).toContain("fresh");

    // 迟到的重试返回不得覆盖后来成功的结果。
    const badMedia = makeMedia("bad-ok", "坏.mp4");
    serverMedia.push(badMedia);
    retryOfBad.resolve(badMedia);
    await flush();
    state = useEditor.getState();
    expect(state.media.map((item) => item.id)).toEqual(
      expect.arrayContaining(["dup", "fresh", "bad-ok"]),
    );
    expect(state.uploads.find((entry) => entry.name === "坏.mp4")?.status).toBe("done");

    // 最慢的读取最后完成，同样只追加不覆盖。
    const slowMedia = makeMedia("slow", "慢.mp4");
    serverMedia.push(slowMedia);
    slow.resolve(slowMedia);
    await flush();
    expect(useEditor.getState().media.map((item) => item.id)).toEqual(
      expect.arrayContaining(["dup", "fresh", "bad-ok", "slow"]),
    );
  });

  it("取消载入后可重新导入同一文件", async () => {
    let signalRef: AbortSignal | undefined;
    const pending = deferred<MediaInfo>();
    mockApi.uploadMedia.mockImplementation((_file: File, signal: AbortSignal) => {
      signalRef = signal;
      return pending.promise;
    });

    useEditor.getState().uploadMedia([makeFile("大文件.mp4")]);
    await flush();
    const key = useEditor.getState().uploads[0].key;
    expect(useEditor.getState().uploads[0].status).toBe("reading");

    useEditor.getState().cancelUpload(key);
    expect(signalRef!.aborted).toBe(true);
    expect(useEditor.getState().uploads).toHaveLength(0);

    // 迟到的旧响应（服务端其实成功了）也不得写回列表。
    const stale = makeMedia("stale", "大文件.mp4");
    pending.resolve(stale);
    await flush();
    expect(useEditor.getState().media).toHaveLength(0);

    const retried = makeMedia("ok", "大文件.mp4");
    mockApi.uploadMedia.mockImplementation(() => {
      serverMedia.push(retried);
      return Promise.resolve(retried);
    });
    useEditor.getState().uploadMedia([makeFile("大文件.mp4")]);
    await flush();
    expect(useEditor.getState().media.map((item) => item.id)).toContain("ok");
  });
});

describe("工程编辑状态隔离", () => {
  it("新建工程不带入旧工程的选择、速度与撤销历史", async () => {
    const media = makeMedia("m1");
    serverMedia.push(media);
    await useEditor.getState().loadMedia();
    const projectA = makeProject("a", [makeClip("clip-a", "m1")]);
    serverProjects.set("a", projectA);
    await useEditor.getState().openProject("a");

    useEditor.getState().selectClip("clip-a");
    useEditor.getState().setClipSpeed("clip-a", 0, 1, 2);
    expect(useEditor.getState().canUndo).toBe(true);
    expect(useEditor.getState().project?.clips[0].speed_segments).toHaveLength(1);
    await useEditor.getState().save();

    await useEditor.getState().newProject();
    const state = useEditor.getState();
    expect(state.project?.clips).toEqual([]);
    expect(state.selectedClipId).toBeNull();
    expect(state.canUndo).toBe(false);
    expect(state.canRedo).toBe(false);

    // 保存后重开旧工程：服务端返回带变速的片段，撤销历史仍属该工程。
    await useEditor.getState().openProject("a");
    const reopened = useEditor.getState();
    expect(reopened.project?.clips[0].speed_segments).toHaveLength(1);
    expect(reopened.canUndo).toBe(true);
    useEditor.getState().undo();
    expect(useEditor.getState().project?.clips[0].speed_segments).toHaveLength(0);
  });

  it("空工程与删除最后片段后撤销仍可操作", async () => {
    const media = makeMedia("m1");
    serverMedia.push(media);
    await useEditor.getState().loadMedia();
    await useEditor.getState().newProject();

    // 空工程上的删除/撤销是安全的无操作。
    useEditor.getState().removeClip("missing");
    useEditor.getState().undo();
    expect(useEditor.getState().project?.clips).toEqual([]);
    expect(useEditor.getState().canUndo).toBe(false);

    useEditor.getState().addClip("m1");
    const clipId = useEditor.getState().project!.clips[0].id;
    useEditor.getState().removeClip(clipId);
    let state = useEditor.getState();
    expect(state.project?.clips).toEqual([]);
    expect(state.selectedClipId).toBeNull();
    expect(state.canUndo).toBe(true);

    useEditor.getState().undo();
    state = useEditor.getState();
    expect(state.project?.clips.map((clip) => clip.id)).toEqual([clipId]);
  });
});

describe("缺失素材替换", () => {
  it("确认替换形成可撤销编辑，撤销后恢复片段顺序与变速映射", async () => {
    const clips = [
      {
        ...makeClip("c1", "m1"),
        speed_segments: [{ source_start: 1, source_end: 2, rate: 2 }],
      },
      {
        ...makeClip("c2", "m1"),
        speed_segments: [{ source_start: 0.5, source_end: 1.5, rate: 0.5 }],
      },
    ];
    const project = makeProject("p-rep", clips);
    serverProjects.set(project.id, project);
    serverMedia.push(makeMedia("m1"), makeMedia("m2"));
    await useEditor.getState().loadMedia();
    await useEditor.getState().openProject("p-rep");
    const before = structuredClone(useEditor.getState().project!.clips);

    // 服务器只替换选定的 c1；共享同一素材的 c2 保持不变。
    const replaced = [{ ...clips[0], media_id: "m2" }, clips[1]];
    mockApi.confirmReplacement.mockResolvedValue({
      project: { ...project, clips: replaced, version: 2 },
      applied: ["c1"],
      blocked: [],
    });
    const result = await useEditor.getState().confirmReplacement({
      media_id: "m1",
      replacement_media_id: "m2",
      mode: "keep_source",
      clip_ids: ["c1"],
    });

    expect(result?.applied).toEqual(["c1"]);
    const after = useEditor.getState().project!.clips;
    expect(after[0].media_id).toBe("m2");
    expect(after[1].media_id).toBe("m1");
    expect(useEditor.getState().canUndo).toBe(true);

    useEditor.getState().undo();
    const restored = useEditor.getState().project!.clips;
    expect(restored).toEqual(before);
    // 本地时间线镜像恢复片段顺序与音画共用的源时间映射。
    const timeline = useEditor.getState().timeline!;
    expect(timeline.clips.map((clip) => clip.clip_id)).toEqual(["c1", "c2"]);
    expect(timeline.clips[0].speed_segments.map((seg) => seg.rate)).toEqual([1, 2]);
    expect(timeline.clips[1].speed_segments.map((seg) => seg.rate)).toEqual([1, 0.5, 1]);
  });
});
