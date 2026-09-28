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

function clipAt(timeline: Timeline, time: number): TimelineClip | null {
  return (
    timeline.clips.find((clip) => time >= clip.start && time < clip.end) ??
    timeline.clips[timeline.clips.length - 1] ??
    null
  );
}

export function Preview() {
  const timeline = useEditor((state) => state.timeline);
  const media = useEditor((state) => state.media);
  const project = useEditor((state) => state.project);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const timeRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const activeClipRef = useRef<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const playingRef = useRef(false);

  const mediaIndex = new Map(media.map((item) => [item.id, item]));
  const duration = timeline?.duration ?? 0;

  useEffect(() => {
    // Reset preview when the project structure changes materially.
    timeRef.current = 0;
    activeClipRef.current = null;
    setPosition(0);
    setPlaying(false);
    playingRef.current = false;
  }, [project?.id]);

  useEffect(() => {
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  const seekClipTo = (clip: TimelineClip, localOffset: number) => {
    const video = videoRef.current;
    if (!video) return;
    const sourceTime = clip.source_in + localOffset;
    if (Math.abs(video.currentTime - sourceTime) > 0.08) {
      video.currentTime = sourceTime;
    }
  };

  const tick = () => {
    const video = videoRef.current;
    if (!timeline || !video) {
      rafRef.current = requestAnimationFrame(tick);
      return;
    }
    const clip = clipAt(timeline, timeRef.current);
    if (!clip) {
      stop();
      return;
    }
    if (timeRef.current >= timeline.duration - 0.005) {
      stop();
      setPosition(timeline.duration);
      return;
    }
    const localOffset = Math.max(0, timeRef.current - clip.start);
    if (activeClipRef.current !== clip.clip_id) {
      activeClipRef.current = clip.clip_id;
      const url = api.mediaStreamUrl(clip.media_id);
      const item = mediaIndex.get(clip.media_id);
      if (!item) {
        // Missing media: advance clock silently (kept editable in timeline).
      } else if (!video.src.endsWith(url)) {
        video.src = url;
      }
      video.load();
      video.currentTime = clip.source_in + localOffset;
      if (playingRef.current) void video.play().catch(() => undefined);
    } else if (!clip.media_missing) {
      seekClipTo(clip, localOffset);
    }
    timeRef.current += 1 / 60;
    setPosition(timeRef.current);
    rafRef.current = requestAnimationFrame(tick);
  };

  const start = () => {
    if (!timeline || timeline.duration === 0) return;
    if (timeRef.current >= timeline.duration - 0.02) {
      timeRef.current = 0;
      activeClipRef.current = null;
    }
    playingRef.current = true;
    setPlaying(true);
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);
  };

  const pause = () => {
    playingRef.current = false;
    setPlaying(false);
    videoRef.current?.pause();
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  };

  const stop = () => {
    playingRef.current = false;
    setPlaying(false);
    videoRef.current?.pause();
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  };

  const restart = () => {
    stop();
    timeRef.current = 0;
    activeClipRef.current = null;
    setPosition(0);
  };

  const scrub = (value: number) => {
    stop();
    timeRef.current = value;
    activeClipRef.current = null; // force reload at new source offset
    setPosition(value);
    // Render one frame synchronously.
    rafRef.current = requestAnimationFrame(function once() {
      const video = videoRef.current;
      if (video && timeline) {
        const clip = clipAt(timeline, value);
        if (clip && !clip.media_missing) {
          const url = api.mediaStreamUrl(clip.media_id);
          if (!video.src.endsWith(url)) video.src = url;
          video.currentTime = clip.source_in + (value - clip.start);
        }
      }
    });
  };

  const currentClip = timeline ? clipAt(timeline, position) : null;
  const missing = timeline?.missing_media_ids.length ?? 0;

  return (
    <section className="panel preview">
      <div className="preview-stage">
        <video ref={videoRef} muted playsInline preload="auto" />
        {currentClip?.media_missing && (
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
