import type { Clip, MediaItem, RenderJob } from "../types";
import { clipDuration, layout } from "../types";

interface Props {
  previewUrl: string | null;
  job: RenderJob | null;
  totalDuration: number;
  clips: Clip[];
  mediaById: Map<string, MediaItem>;
}

export function Preview({ previewUrl, job, clips, mediaById }: Props) {
  const running = job?.status === "running";
  const items = layout({ clips });

  return (
    <div className="preview">
      <div className="player">
        {previewUrl ? (
          <video key={previewUrl} src={previewUrl} controls autoPlay />
        ) : (
          <div className="player-placeholder">
            {running ? (
              <>
                <div className="spinner" />
                <p>{job?.kind === "preview" ? "正在渲染预览代理…" : "正在导出成片…"}</p>
                <div className="bar"><i style={{ width: (job?.progress ?? 0) * 100 + "%" }} /></div>
                <p className="muted">{Math.round((job?.progress ?? 0) * 100)}%</p>
              </>
            ) : (
              <>
                <p className="big">▶</p>
                <p>点击“生成预览”按当前时间线渲染可播放代理（与导出同一渲染管线）</p>
              </>
            )}
          </div>
        )}
        {running && previewUrl && (
          <div className="badge-running">
            {job?.kind === "preview" ? "预览" : "导出"} {Math.round((job?.progress ?? 0) * 100)}%
          </div>
        )}
      </div>
      {job?.status === "done" && job.kind === "export" && (
        <a className="download" href={job.output ?? "#"} download>
          ⬇ 下载成片 MP4（{job.project_name}）
        </a>
      )}
      {job?.status === "failed" && (
        <div className="render-error">渲染失败：{job.error}。工程已自动保存，修正后可重试。</div>
      )}
      <div className="segments">
        {items.map(({ clip, start, end }, i) => {
          const m = mediaById.get(clip.media_id);
          return (
            <div key={clip.id} className={"seg " + (m?.missing ? "miss" : "")}>
              <b>#{i + 1}</b> {m?.original_name ?? clip.media_id}
              <span>
                时间线 {start.toFixed(2)}–{end.toFixed(2)}s
                （源 {clip.in_point.toFixed(2)}–{clip.out_point.toFixed(2)}s
                ×{clip.speed.toFixed(1)} → 长 {clipDuration(clip).toFixed(2)}s）
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
