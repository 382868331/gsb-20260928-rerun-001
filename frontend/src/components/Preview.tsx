import { useEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Pause,
  Play,
  Repeat,
  SkipBack,
} from "lucide-react";
import { api } from "../api";
import type { Timeline, TimelineClip } from "../types";
import { sourceTimeInClip, useEditor } from "../store";

const PLAYBACK_FPS = 30;
const AUDITION_PAD = 0.5;

interface LayerState {
  video: HTMLVideoElement | null;
  source: MediaElementAudioSourceNode | null;
  gain: GainNode | null;
  url: string;
  loadToken: number;
  segmentIndex: number;
}

function formatTime(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const secs = (safe % 60).toFixed(2).padStart(5, "0");
  return `${minutes}:${secs}`;
}

function activeClipsAt(timeline: Timeline, time: number): TimelineClip[] {
  return timeline.clips.filter(
    (clip) => time >= clip.transition_start - 1e-7 && time < clip.end,
  );
}

export function Preview() {
  const timeline = useEditor((state) => state.timeline);
  const projectId = useEditor((state) => state.project?.id);
  const selectedClipId = useEditor((state) => state.selectedClipId);
  const timeRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const lastFrameRef = useRef(0);
  const playingRef = useRef(false);
  const loopRangeRef = useRef<{ start: number; end: number } | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const layersRef = useRef<LayerState[]>([
    { video: null, source: null, gain: null, url: "", loadToken: 0, segmentIndex: -1 },
    { video: null, source: null, gain: null, url: "", loadToken: 0, segmentIndex: -1 },
  ]);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [auditionClipId, setAuditionClipId] = useState<string | null>(null);

  const duration = timeline?.duration ?? 0;

  const stopPlayback = () => {
    playingRef.current = false;
    setPlaying(false);
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    for (const layer of layersRef.current) layer.video?.pause();
  };

  const syncPreview = (time: number, shouldPlay: boolean) => {
    if (!timeline) return;
  const active = activeClipsAt(timeline, time);
  const firstIndex = timeline.clips.findIndex((clip) => clip.clip_id === active[0]?.clip_id);
  const outgoingTransition = active[0] && firstIndex >= 0
    ? (timeline.clips[firstIndex + 1]?.transition ?? 0)
    : 0;
  configureLayer(layersRef.current[0], active[0], time, shouldPlay && active.length > 0, outgoingTransition);
  configureLayer(layersRef.current[1], active[1], time, shouldPlay && active.length > 1, active[1]?.transition ?? 0);
  };

  useEffect(() => {
    stopPlayback();
    loopRangeRef.current = null;
    timeRef.current = 0;
    setPosition(0);
    setAuditionClipId(null);
    syncPreview(0, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    const clamped = Math.min(timeRef.current, timeline?.duration ?? 0);
    timeRef.current = clamped;
    setPosition(clamped);
    syncPreview(clamped, playingRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeline]);

  useEffect(() => () => stopPlayback(), []);

  const setLayerVideo = (index: number) => (video: HTMLVideoElement | null) => {
    layersRef.current[index].video = video;
  };

  const ensureAudio = () => {
    if (!audioContextRef.current) {
      audioContextRef.current = new AudioContext();
    }
    if (audioContextRef.current.state === "suspended") {
      void audioContextRef.current.resume();
    }
    for (const layer of layersRef.current) {
      const video = layer.video;
      if (!video || !audioContextRef.current || layer.source) continue;
      video.muted = false;
      layer.source = audioContextRef.current.createMediaElementSource(video);
      layer.gain = audioContextRef.current.createGain();
      layer.gain.gain.value = 0;
      layer.source.connect(layer.gain);
      layer.gain.connect(audioContextRef.current.destination);
    }
  };

  const configureLayer = (
    layer: LayerState,
    clip: TimelineClip | undefined,
    time: number,
    shouldPlay: boolean,
    incomingTransition: number,
  ) => {
    const video = layer.video;
    if (!video) return;
    if (!clip || clip.media_missing) {
      video.pause();
      if (layer.gain) layer.gain.gain.value = 0;
      video.style.opacity = "0";
      layer.segmentIndex = -1;
      return;
    }

    const url = api.mediaStreamUrl(clip.media_id);
    const fadeIn = clip.transition > 0 && time < clip.start
      ? Math.max(0, Math.min(1, (time - clip.transition_start) / clip.transition))
      : 1;
    const fadeOut = incomingTransition > 0 && time >= clip.end - incomingTransition
      ? Math.max(0, 1 - (time - (clip.end - incomingTransition)) / incomingTransition)
      : 1;
    const fade = Math.min(fadeIn, fadeOut);
    if (layer.gain) layer.gain.gain.value = clip.volume * fade;
    video.volume = 1;
    video.style.opacity = String(fade);

    const localTime = Math.max(0, time - clip.transition_start);
    const segmentIndex = Math.max(0, clip.speed_segments.findIndex(
      (segment) =>
        localTime >= segment.clip_start - 1e-7 &&
        (localTime < segment.clip_end - 1e-7 || segment === clip.speed_segments.at(-1)),
    ));
    const speedSegment = clip.speed_segments[segmentIndex];
    const playbackRate = speedSegment?.rate ?? 1;
    const sourceTime = sourceTimeInClip(clip, localTime);
    video.playbackRate = playbackRate;
    video.preservesPitch = false;
    (video as HTMLVideoElement & { mozPreservesPitch?: boolean }).mozPreservesPitch = false;
    const crossedSpeedBoundary = layer.segmentIndex >= 0 && layer.segmentIndex !== segmentIndex;
    layer.segmentIndex = segmentIndex;
    if (layer.url !== url) {
      layer.url = url;
      const token = ++layer.loadToken;
      video.src = url;
      video.load();
      video.onloadedmetadata = () => {
        if (token === layer.loadToken) video.currentTime = sourceTime;
      };
    } else if (crossedSpeedBoundary || Math.abs(video.currentTime - sourceTime) > 0.08) {
      video.currentTime = sourceTime;
    }
    if (shouldPlay) void video.play().catch(() => undefined);
    else video.pause();
  };

  const tick = (now: number) => {
    if (!timeline) return;
    const delta = Math.min(0.1, (now - lastFrameRef.current) / 1000 || 0);
    lastFrameRef.current = now;
    let nextTime = timeRef.current + delta;
    const loopRange = loopRangeRef.current;
    if (loopRange && nextTime >= loopRange.end) {
      nextTime = loopRange.start;
    } else if (nextTime >= timeline.duration - 1 / PLAYBACK_FPS) {
      timeRef.current = timeline.duration;
      setPosition(timeline.duration);
      syncPreview(timeline.duration, false);
      stopPlayback();
      return;
    }
    timeRef.current = nextTime;
    setPosition(nextTime);
    syncPreview(nextTime, true);
    rafRef.current = requestAnimationFrame(tick);
  };

  const startPlayback = (loopRange: { start: number; end: number } | null = null) => {
    if (!timeline || timeline.duration === 0) return;
    ensureAudio();
    if (loopRange) {
      timeRef.current = loopRange.start;
      setPosition(loopRange.start);
    } else if (timeRef.current >= timeline.duration - 0.02) {
      timeRef.current = 0;
      setPosition(0);
    }
    loopRangeRef.current = loopRange;
    playingRef.current = true;
    setPlaying(true);
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    lastFrameRef.current = performance.now();
    syncPreview(timeRef.current, true);
    rafRef.current = requestAnimationFrame(tick);
  };

  const pausePlayback = () => {
    stopPlayback();
    syncPreview(timeRef.current, false);
  };

  const restart = () => {
    setAuditionClipId(null);
    loopRangeRef.current = null;
    stopPlayback();
    timeRef.current = 0;
    setPosition(0);
    syncPreview(0, false);
  };

  const scrub = (value: number) => {
    setAuditionClipId(null);
    loopRangeRef.current = null;
    stopPlayback();
    timeRef.current = value;
    setPosition(value);
    syncPreview(value, false);
  };

  const stepFrame = (direction: -1 | 1) => {
    setAuditionClipId(null);
    loopRangeRef.current = null;
    stopPlayback();
    const value = Math.max(
      0,
      Math.min(duration, timeRef.current + direction / PLAYBACK_FPS),
    );
    timeRef.current = value;
    setPosition(value);
    syncPreview(value, false);
  };

  const auditionTransition = () => {
    if (!timeline) return;
    const selectedIndex = timeline.clips.findIndex((clip) => clip.clip_id === selectedClipId);
    const incomingIndex = Math.max(
      1,
      selectedIndex > 0 ? selectedIndex : timeline.clips.findIndex((clip) => clip.transition > 0),
    );
    const incoming = timeline.clips[incomingIndex];
    if (!incoming) return;
    const start = Math.max(0, incoming.transition_start - AUDITION_PAD);
    const end = Math.min(
      timeline.duration,
      incoming.start + incoming.transition + AUDITION_PAD,
    );
    setAuditionClipId(incoming.clip_id);
    startPlayback({ start, end });
  };

  const currentClips = timeline ? activeClipsAt(timeline, position) : [];
  const missing = timeline?.missing_media_ids.length ?? 0;

  return (
    <section className="panel preview">
      <div className="preview-stage">
        <video ref={setLayerVideo(0)} muted playsInline preload="auto" />
        <video ref={setLayerVideo(1)} muted playsInline preload="auto" />
        {currentClips.some((clip) => clip.media_missing) && (
          <div className="missing-badge">素材缺失，占位预览</div>
        )}
      </div>
      <div className="preview-controls">
        <button type="button" onClick={restart} title="回到开头">
          <SkipBack size={18} />
        </button>
        {playing ? (
          <button type="button" onClick={pausePlayback} title="暂停">
            <Pause size={18} />
          </button>
        ) : (
          <button type="button" onClick={() => startPlayback()} title="播放">
            <Play size={18} />
          </button>
        )}
        <button type="button" onClick={() => stepFrame(-1)} title="上一帧">
          <ChevronLeft size={18} />
        </button>
        <button type="button" onClick={() => stepFrame(1)} title="下一帧">
          <ChevronRight size={18} />
        </button>
        <button type="button" onClick={auditionTransition} title="循环试听转场前后">
          <Repeat size={18} />
        </button>
        <input
          className="scrubber"
          type="range"
          min={0}
          max={Math.max(duration, 0.001)}
          step={1 / PLAYBACK_FPS}
          value={Math.min(position, duration)}
          onChange={(event) => scrub(Number(event.target.value))}
        />
        <span className="timecode">
          {formatTime(position)} / {formatTime(duration)}
        </span>
      </div>
      {auditionClipId && (
        <p className="hint">正在循环试听所选转场前后各 {AUDITION_PAD.toFixed(1)} 秒。</p>
      )}
      {missing > 0 && (
        <p className="warning">
          有 {missing} 段素材缺失；补齐同名素材后即可恢复，工程仍可继续编辑。
        </p>
      )}
    </section>
  );
}
