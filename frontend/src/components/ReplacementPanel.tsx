import { useEffect, useRef, useState } from "react";
import { FileWarning, Replace, X } from "lucide-react";
import { useEditor } from "../store";
import type { ReplacementMode } from "../types";

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(2).padStart(5, "0");
  return `${minutes}:${secs}`;
}

function formatFps(fps: number | null): string {
  return fps ? `${fps.toFixed(fps % 1 === 0 ? 0 : 2)}fps` : "帧率未知";
}

export function ReplacementPanel() {
  const timeline = useEditor((state) => state.timeline);
  const project = useEditor((state) => state.project);
  const replacement = useEditor((state) => state.replacement);
  const prepareReplacement = useEditor((state) => state.prepareReplacement);
  const setReplacementMode = useEditor((state) => state.setReplacementMode);
  const confirmReplacement = useEditor((state) => state.confirmReplacement);
  const closeReplacement = useEditor((state) => state.closeReplacement);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [pendingMediaId, setPendingMediaId] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());

  const plan = replacement?.plan ?? null;
  useEffect(() => {
    if (!plan) {
      setChecked(new Set());
      return;
    }
    // Default: every clip that can be confirmed is selected; blocked
    // clips stay unchecked and disabled until the file/mode changes.
    setChecked(
      new Set(plan.clips.filter((entry) => !entry.blocked).map((entry) => entry.clip_id)),
    );
  }, [plan]);

  if (!project || !timeline) return null;
  const missing = timeline.missing_media_ids;
  if (missing.length === 0 && !plan) return null;

  const toggle = (clipId: string) => {
    const next = new Set(checked);
    if (next.has(clipId)) next.delete(clipId);
    else next.add(clipId);
    setChecked(next);
  };

  const confirmable = plan?.clips.filter((entry) => !entry.blocked) ?? [];
  const selectedCount = plan
    ? plan.clips.filter((entry) => checked.has(entry.clip_id) && !entry.blocked).length
    : 0;

  return (
    <section className="panel replacement-panel">
      <h2>
        <FileWarning size={18} /> 缺失素材替换
      </h2>
      {missing.length > 0 && (
        <ul className="missing-list">
          {missing.map((mediaId) => (
            <li key={mediaId}>
              <span className="meta" title={mediaId}>
                素材缺失：{mediaId.slice(0, 12)}…（{plan?.media_id === mediaId ? "正在选择替代文件" : "可指定替代文件"}）
              </span>
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setPendingMediaId(mediaId);
                  inputRef.current?.click();
                }}
              >
                <Replace size={15} /> 指定替代文件
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file && pendingMediaId) void prepareReplacement(pendingMediaId, file);
          event.target.value = "";
        }}
      />

      {plan && (
        <div className="replacement-detail">
          <div className="replacement-summary">
            <strong>{plan.replacement.filename}</strong>
            <span className="meta">
              时长 {formatDuration(plan.replacement.duration)} · {formatFps(plan.replacement.fps)} ·{" "}
              {plan.replacement.has_audio ? "有声轨" : "无音轨"}
            </span>
            {plan.original && (
              <span className="meta">
                原素材：{plan.original.filename}（{formatDuration(plan.original.duration)} ·{" "}
                {formatFps(plan.original.fps)} · {plan.original.has_audio ? "有声轨" : "无音轨"}）
              </span>
            )}
            <span className={plan.identical_to_original ? "meta ok" : "meta warn"}>
              {plan.identical_to_original
                ? "内容哈希与原件一致，等同原素材"
                : "与原件内容不同（按内容哈希区分，文件名相同也不算原件）"}
            </span>
            {plan.original &&
              plan.original.fps &&
              plan.replacement.fps &&
              Math.abs(plan.original.fps - plan.replacement.fps) > 0.01 && (
                <span className="meta warn">
                  帧率差异：{formatFps(plan.original.fps)} → {formatFps(plan.replacement.fps)}
                  ，变速映射按源时间计算，导出时会按新帧率重采样
                </span>
              )}
          </div>

          <div className="replacement-mode">
            <label>
              <input
                type="radio"
                name="replacement-mode"
                checked={plan.mode === "keep_source"}
                onChange={() => void setReplacementMode("keep_source" as ReplacementMode)}
              />
              保持源时间位置（裁切点/变速点不变，源时长必须足够）
            </label>
            <label>
              <input
                type="radio"
                name="replacement-mode"
                checked={plan.mode === "keep_duration"}
                onChange={() => void setReplacementMode("keep_duration" as ReplacementMode)}
              />
              保持成片时长（源时间按比例缩放，时间线总长不变）
            </label>
          </div>

          <ul className="replacement-clips">
            {plan.clips.map((entry) => {
              const clipIndex = project.clips.findIndex((clip) => clip.id === entry.clip_id);
              return (
                <li key={entry.clip_id} className={entry.blocked ? "blocked" : ""}>
                  <label>
                    <input
                      type="checkbox"
                      disabled={entry.blocked}
                      checked={!entry.blocked && checked.has(entry.clip_id)}
                      onChange={() => toggle(entry.clip_id)}
                    />
                    片段 {clipIndex + 1}：源 {formatDuration(entry.source_in)} →{" "}
                    {formatDuration(entry.source_out)}，成片{" "}
                    {formatDuration(entry.rendered_before)} → {formatDuration(entry.rendered_after)}
                  </label>
                  {entry.blocked && (
                    <span className="meta warn">{entry.reasons.join("；")}，无法确认</span>
                  )}
                </li>
              );
            })}
          </ul>
          {plan.clips.some((entry) => !entry.blocked && !checked.has(entry.clip_id)) && (
            <span className="meta">未勾选的片段保持原引用不变。</span>
          )}

          <div className="replacement-actions">
            <button
              type="button"
              disabled={selectedCount === 0}
              onClick={() =>
                void confirmReplacement(
                  plan.clips
                    .filter((entry) => !entry.blocked && checked.has(entry.clip_id))
                    .map((entry) => entry.clip_id),
                )
              }
            >
              <Replace size={15} /> 确认替换（{selectedCount}/{confirmable.length}），可撤销
            </button>
            <button type="button" className="secondary" onClick={closeReplacement}>
              <X size={15} /> 取消
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
