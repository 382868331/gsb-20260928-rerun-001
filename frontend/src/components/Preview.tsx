import { useEffect, useRef, useState } from "react";
import { Pause, Play, SkipBack } from "lucide-react";
import { api } from "../api";
import type { Timeline, TimelineClip } from "../types";
import { useEditor } from "../store";

function formatTime(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const secs = (safe % 60).toFixed(2).padStart(5, "0");
  return `${minutes}:${secs}`;
}

/** Clips active at a timeline time, ordered so the newest is on top. */
function activeClipsAt(timeline: Timeline, time: number): TimelineClip[] {
  return timeline.clips.filter((clip) => time >= clip.start - 0.001 && time < clip.end - 0.001);
}

/** Visual/audible weight of a clip at a timeline time (matches the
 * exporter's linear xfade / triangular acrossfade ramps). */
function clipWeight(clip: TimelineClip, time: number): number {
  if (clip.transition_in <= 0) return 1;
  const fadeStart = clip.start;
  if (time < fadeStart) return 0;
  if (time < fadeStart + clip.transition_in) {
    return Math.min(1, (time - fadeStart) / clip.transition_in);
  }
  return 1;
}

interface Layer {
  clip: TimelineClip;
  el: HTMLVideoElement;
  gain: GainNode;
}

export function Preview() {
  const timeline = useEditor((state) => state.timeline);
  const media = useEditor((state) => state.media);
  const project = useEditor((state) => state.project);
  const layerHostRef = useRef<HTMLDivElement | null>(null);
  const timeRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const layersRef = useRef<Map<string, Layer>>(new Map());
  const audioRef = useRef<AudioContext | null>(null);
  const masterRef = useRef<GainNode | null>(null);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const playingRef = useRef(false);

  const mediaIndex = new Map(media.map((item) => [item.id, item]));
  const duration = timeline?.duration ?? 0;

  useEffect(() => {
    timeRef.current = 0;
    removeAllLayers();
    setPosition(0);
    setPlaying(false);
    playingRef.current = false;
  }, [project?.id]);

  useEffect(() => {
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      void audioRef.current?.close().catch(() => undefined);
    };
  }, []);

  const ensureAudio = () => {
    if (!audioRef.current) {
      const ctx = new AudioContext();
      const master = ctx.createGain();
      master.gain.value = 1;
      master.connect(ctx.destination);
      audioRef.current = ctx;
      masterRef.current = master;
    }
    if (audioRef.current.state === "suspended") void audioRef.current.resume();
    return audioRef.current;
  };

  const removeLayer = (clipId: string) => {
    const layer = layersRef.current.get(clipId);
    if (!layer) return;
    layersRef.current.delete(clipId);
    try {
      layer.gain.disconnect();
    } catch {
      // already disconnected
    }
    layer.el.remove();
  };

  const removeAllLayers = () => {
    for (const id of [...layersRef.current.keys()]) removeLayer(id);
  };

  const ensureLayer = (clip: TimelineClip): Layer | null => {
    const existing = layersRef.current.get(clip.clip_id);
    if (existing) return existing;
    const item = mediaIndex.get(clip.media_id);
    if (!item || clip.media_missing) return null;
    const ctx = ensureAudio();
    const el = document.createElement("video");
    el.src = api.mediaStreamUrl(clip.media_id);
    el.crossOrigin = "anonymous";
    el.playsInline = true;
    el.preload = "auto";
    el.muted = false;
    el.className = "layer-video";
    const source = ctx.createMediaElementSource(el);
    const gain = ctx.createGain();
    source.connect(gain);
    gain.connect(masterRef.current!);
    layerHostRef.current?.appendChild(el);
    const layer: Layer = { clip, el, gain };
    layersRef.current.set(clip.clip_id, layer);
    return layer;
  };

  const renderFrame = (time: number) => {
    if (!timeline) return;
    const active = activeClipsAt(timeline, time);
    const activeIds = new Set(active.map((clip) => clip.clip_id));
    for (const id of [...layersRef.current.keys()]) {
      if (!activeIds.has(id)) removeLayer(id);
    }
    active.forEach((clip) => {
      const layer = ensureLayer(clip);
      if (!layer) return;
      const sourceTime = clip.source_in + Math.max(0, time - clip.start);
      if (Math.abs(layer.el.currentTime - sourceTime) > 0.08) {
        try {
          layer.el.currentTime = sourceTime;
        } catch {
          // seek may reject before metadata loads
        }
      }
      const weight = clipWeight(clip, time);
      // During an overlap the outgoing clip's weight is 1 - incoming
      // weight, exactly like a linear xfade.
      const next = timeline.clips.find(
        (candidate) =>
          candidate.transition_in > 0 &&
          time >= candidate.start &&
          time < candidate.start + candidate.transition_in,
      );
      const ownWeight = next && clip.clip_id !== next.clip_id ? 1 - clipWeight(next, time) : weight;
      layer.el.style.opacity = String(ownWeight);
      layer.gain.gain.value = clip.volume * ownWeight;
      if (playingRef.current && layer.el.paused) {
        void layer.el.play().catch(() => undefined);
      } else if (!playingRef.current && !layer.el.paused) {
        layer.el.pause();
      }
    });
  };

  const tick = () => {
    if (!timeline) return;
    if (timeRef.current >= timeline.duration - 0.005) {
      stop();
      setPosition(timeline.duration);
      renderFrame(timeline.duration - 0.01);
      return;
    }
    renderFrame(timeRef.current);
    timeRef.current += 1 / 60;
    setPosition(timeRef.current);
    rafRef.current = requestAnimationFrame(tick);
  };

  const start = () => {
    if (!timeline || timeline.duration === 0) return;
    if (timeRef.current >= timeline.duration - 0.02) timeRef.current = 0;
    ensureAudio();
    playingRef.current = true;
    setPlaying(true);
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);
  };

  const pause = () => {
    playingRef.current = false;
    setPlaying(false);
    layersRef.current.forEach((layer) => layer.el.pause());
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  };

  const stop = () => {
    playingRef.current = false;
    setPlaying(false);
    layersRef.current.forEach((layer) => layer.el.pause());
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  };

  const restart = () => {
    stop();
    timeRef.current = 0;
    removeAllLayers();
    setPosition(0);
    requestAnimationFrame(() => renderFrame(0));
  };

  const scrub = (value: number) => {
    stop();
    timeRef.current = value;
    setPosition(value);
    requestAnimationFrame(() => renderFrame(value));
  };

  const currentClips = timeline ? activeClipsAt(timeline, position) : [];
  const missing = timeline?.missing_media_ids.length ?? 0;

  return (
    <section className="panel preview">
      <div className="preview-stage" ref={layerHostRef}>
        {currentClips.some((clip) => clip.media_missing) && (
          <div className="missing-badge">素材缺失，占位预览</div>
        )}
      </div>
      <div className="preview-controls">
        <button type="button" onClick={restart} title="回到开头">
          <SkipBack size={18} />
        </button>
        {playing ? (
          <button type="button" onClick={pause} title="暂停">
            <Pause size={18} />
          </button>
        ) : (
          <button type="button" onClick={start} title="播放">
            <Play size={18} />
          </button>
        )}
        <input
          className="scrubber"
          type="range"
          min={0}
          max={Math.max(duration, 0.001)}
          step={0.01}
          value={Math.min(position, duration)}
          onChange={(event) => scrub(Number(event.target.value))}
        />
        <span className="timecode">
          {formatTime(position)} / {formatTime(duration)}
        </span>
      </div>
      {missing > 0 && (
        <p className="warning">
          有 {missing} 段素材缺失；补齐同名素材后即可恢复，工程仍可继续编辑。
        </p>
      )}
    </section>
  );
}
