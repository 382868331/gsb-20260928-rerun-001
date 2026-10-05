import { useEffect, useMemo, useState } from "react";
import { FileWarning, RefreshCcw } from "lucide-react";
import { api } from "../api";
import { useEditor } from "../store";
import type {
  ReplacementMode,
  ReplacementPreview,
} from "../types";

function formatSeconds(value: number): string {
  return `${value.toFixed(2)}s`;
}

/**
 * Offline-package media replacement: point clips that reference a
 * missing source at a replacement file from the library. The preview
 * is computed per clip on the server (source duration / frame rate /
 * audio-track differences against trims and speed plans); confirming
 * applies only the unblocked clips as an undoable edit.
 */
export function ReplacementPanel() {
  const project = useEditor((state) => state.project);
  const timeline = useEditor((state) => state.timeline);
  const media = useEditor((state) => state.media);
  const busy = useEditor((state) => state.busy);
  const confirmReplacement = useEditor((state) => state.confirmReplacement);
  const setMessage = useEditor((state) => state.setMessage);

  const missingIds = timeline?.missing_media_ids ?? [];
  const [mediaId, setMediaId] = useState<string | null>(null);
  const [replacementId, setReplacementId] = useState<string>("");
  const [mode, setMode] = useState<ReplacementMode>("keep_source");
  const [selectedClips, setSelectedClips] = useState<string[]>([]);
  const [preview, setPreview] = useState<ReplacementPreview | null>(null);
  const [loading, setLoading] = useState(false);

  const activeMediaId = mediaId && missingIds.includes(mediaId)
    ? mediaId
    : (missingIds[0] ?? null);

  const affectedClips = useMemo(
    () => (project?.clips ?? []).filter((clip) => clip.media_id === activeMediaId),
    [project, activeMediaId],
  );

  useEffect(() => {
    setSelectedClips(affectedClips.map((clip) => clip.id));
    setPreview(null);
  }, [activeMediaId, affectedClips]);

  if (!project || missingIds.length === 0 || !activeMediaId) return null;

  const candidates = media.filter((item) => item.id !== activeMediaId);
  const replacement = candidates.find((item) => item.id === replacementId) ?? null;

  const buildRequest = () => ({
    media_id: activeMediaId,
    replacement_media_id: replacementId,
    mode,
    // Only the checked references are replaced; unchecked clips keep
    // pointing at the old media even though they share it.
    clip_ids: affectedClips.length === selectedClips.length ? null : selectedClips,
  });

  const runPreview = async () => {
    if (!replacement) return;
    setLoading(true);
    try {
      setPreview(await api.previewReplacement(project.id, buildRequest()));
    } catch (error) {
      setPreview(null);
      setMessage(`替换预览失败：${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const runConfirm = async () => {
    if (!replacement) return;
    setLoading(true);
    try {
      const result = await confirmReplacement(buildRequest());
      if (result) setPreview(null);
    } finally {
      setLoading(false);
    }
  };

  const toggleClip = (clipId: string) => {
    setSelectedClips((current) =>
      current.includes(clipId)
        ? current.filter((id) => id !== clipId)
        : [...current, clipId],
    );
    setPreview(null);
  };

  const confirmable =
    preview?.clips.some((plan) => plan.selected && plan.status === "ok") ?? false;

  return (
    <section className="panel replacement">
      <h2>
        <FileWarning size={18} /> 缺失素材替换
      </h2>
      <p className="hint">
        同名文件不会被当作原素材；替代文件按内容匹配入库后在此指定。
      </p>
      <label className="field">
        缺失素材
        <select
          value={activeMediaId}
          onChange={(event) => {
            setMediaId(event.target.value);
            setPreview(null);
          }}
        >
          {missingIds.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        替代文件
        <select
          value={replacementId}
          onChange={(event) => {
            setReplacementId(event.target.value);
            setPreview(null);
          }}
        >
          <option value="">选择库中的替代素材…</option>
          {candidates.map((item) => (
            <option key={item.id} value={item.id}>
              {item.filename}（{formatSeconds(item.duration)}
              {item.fps ? ` · ${item.fps}fps` : ""}
              {item.has_audio ? " · 有声" : " · 无声"}）
            </option>
          ))}
        </select>
      </label>
      <div className="field">
        替换方式
        <label className="radio">
          <input
            type="radio"
            checked={mode === "keep_source"}
            onChange={() => {
              setMode("keep_source");
              setPreview(null);
            }}
          />
          保持源时间位置（裁切点与变速点不动）
        </label>
        <label className="radio">
          <input
            type="radio"
            checked={mode === "keep_duration"}
            onChange={() => {
              setMode("keep_duration");
              setPreview(null);
            }}
          />
          保持成片时长（源位置与变速等比重映射）
        </label>
      </div>
      {affectedClips.length > 1 && (
        <div className="field">
          替换范围（{affectedClips.length} 个片段共享该素材）
          {affectedClips.map((clip, index) => (
            <label className="radio" key={clip.id}>
              <input
                type="checkbox"
                checked={selectedClips.includes(clip.id)}
                onChange={() => toggleClip(clip.id)}
              />
              片段 {index + 1}（{formatSeconds(clip.source_in)}–
              {formatSeconds(clip.source_out)}
              {clip.speed_segments.length > 0
                ? ` · ${clip.speed_segments.length} 段变速`
                : ""}）
            </label>
          ))}
        </div>
      )}
      <div className="row-actions">
        <button
          type="button"
          className="secondary"
          disabled={!replacement || loading || busy || selectedClips.length === 0}
          onClick={runPreview}
        >
          预览逐片段影响
        </button>
        <button
          type="button"
          disabled={!confirmable || loading || busy}
          onClick={runConfirm}
          title={confirmable ? "应用可确认的片段（可撤销）" : "请先预览影响"}
        >
          <RefreshCcw size={15} /> 确认替换
        </button>
      </div>
      {preview && (
        <div className="replacement-preview">
          <p className="hint">
            成片总时长 {formatSeconds(preview.timeline_duration_before)} →{" "}
            {formatSeconds(preview.timeline_duration_after)}
          </p>
          <ul>
            {preview.clips.map((plan) => (
              <li
                key={plan.clip_id}
                className={plan.status === "blocked" ? "blocked" : "ok"}
              >
                <strong>
                  {plan.selected
                    ? plan.status === "ok"
                      ? "可替换"
                      : "被阻止"
                    : "未选择"}
                </strong>{" "}
                源 {formatSeconds(plan.source_in_before)}–
                {formatSeconds(plan.source_out_before)} →{" "}
                {formatSeconds(plan.source_in_after)}–
                {formatSeconds(plan.source_out_after)}，成片{" "}
                {formatSeconds(plan.rendered_before)} →{" "}
                {formatSeconds(plan.rendered_after)}
                {plan.reasons.map((reason) => (
                  <span className="reason" key={reason}>
                    {reason}
                  </span>
                ))}
                {plan.notes.map((note) => (
                  <span className="note" key={note}>
                    {note}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
