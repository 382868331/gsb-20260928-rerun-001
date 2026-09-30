import { useEffect, useRef, useState } from "react";
import {
  Pause,
  Play,
  Repeat,
  SkipBack,
  StepBack,
  StepForward,
} from "lucide-react";
import { api } from "../api";
import type { Timeline, TimelineClip } from "../types";
import { useEditor } from "../store";

const FRAME = 1 / 30; // export fps: frame stepping matches the rendered file
const LOOP_PAD = 1.0; // seconds of context on each side of a transition

function formatTime(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const secs = (safe % 60).toFixed(2).padStart(5, "0");
  return `${minutes}:${secs}`;
}

interface ActiveEntry {
  clip: TimelineClip;
  /** Combined fade gain (0..1) before the clip's own volume. */
  gain: number;
  /** Timeline position where this clip's media starts (start - transition). */
  windowStart: number;
}

/** Which clips are visible/audible at timeline time `t`, bottom first.
 * Mirrors the exporter: linear crossfade over [start - transition, start]. */
function activeClips(timeline: Timeline, t: number): ActiveEntry[] {
  const entries: ActiveEntry[] = [];
  for (let index = 0; index < timeline.clips.length; index += 1) {
    const clip = timeline.clips[index];
    const windowStart = clip.start - clip.transition;
    const windowEnd = clip.end - clip.transition;
    if (t < windowStart || t >= windowEnd) continue;
    let gain = 1;
    if (clip.transition > 0 && t < clip.start) {
      gain *= Math.min(1, Math.max(0, (t - windowStart) / clip.transition));
    }
    const next = timeline.clips[index + 1];
    if (next && next.transition > 0) {
      const fadeOutStart = windowEnd - next.transition;
      if (t >= fadeOutStart) {
        gain *= Math.min(1, Math.max(0, (windowEnd - t) / next.transition));
      }
    }
    entries.push({ clip, gain, windowStart });
  }
  return entries;
}

// Audio graphs are cached per <video> element so React StrictMode's
// double effect invocation cannot create duplicate MediaElementSources.
let sharedAudioContext: AudioContext | null = null;
const gainCache = new WeakMap<HTMLVideoElement, GainNode>();

function gainFor(video: HTMLVideoElement): GainNode {
  let gain = gainCache.get(video);
  if (!gain) {
    sharedAudioContext = sharedAudioContext ?? new AudioContext();
    gain = sharedAudioContext.createGain();
    sharedAudioContext
      .createMediaElementSource(video)
      .connect(gain)
      .connect(sharedAudioContext.destination);
    gainCache.set(video, gain);
  }
  return gain;
}

interface Slot {
  clipId: string | null;
}

export function Preview() {
  const timeline = useEditor((state) => state.timeline);
  const project = useEditor((state) => state.project);
  const loopClipId = useEditor((state) => state.loopClipId);
  const setLoopClip = useEditor((state) => state.setLoopClip);
  const videoRefs = [
    useRef<HTMLVideoElement | null>(null),
    useRef<HTMLVideoElement | null>(null),
  ];
  const slotsRef = useRef<Slot[]>([{ clipId: null }, { clipId: null }]);
  // Wall-clock based playhead: no drift vs. requestAnimationFrame rates.
  const clockRef = useRef({ time: 0, perf: 0, playing: false });
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);

  const duration = timeline?.duration ?? 0;

  const loopClip = timeline?.clips.find(
    (clip) => clip.clip_id === loopClipId && clip.transition > 0,
  );
  const loopRange =
    timeline && loopClip
      ? {
          start: Math.max(0, loopClip.transition_start - LOOP_PAD),
          end: Math.min(timeline.duration, loopClip.start + LOOP_PAD),
        }
      : null;

  const now = () => {
    const clock = clockRef.current;
    return clock.playing
      ? clock.time + (performance.now() - clock.perf) / 1000
      : clock.time;
  };
  const setClock = (time: number) => {
    clockRef.current.time = time;
    clockRef.current.perf = performance.now();
  };

  useEffect(() => {
    // Reset preview when a different project is opened.
    clockRef.current = { time: 0, perf: performance.now(), playing: false };
    slotsRef.current = [{ clipId: null }, { clipId: null }];
    videoRefs.forEach((ref) => {
      ref.current?.pause();
      if (ref.current) ref.current.removeAttribute("src");
    });
    setPlaying(false);
    setPosition(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id]);

  useEffect(() => {
    // Attach WebAudio gain nodes once the video elements exist.
    videoRefs.forEach((ref) => {
      if (ref.current) gainFor(ref.current);
    });
    let raf = 0;
    const loop = () => {
      tick();
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const tick = () => {
    const { timeline: tl, loopClipId: loopId } = useEditor.getState();
    const videos = videoRefs.map((ref) => ref.current);
    if (!tl || videos.some((video) => !video)) return;
    const clock = clockRef.current;
    let t = now();

    const loopClipNow = tl.clips.find(
      (clip) => clip.clip_id === loopId && clip.transition > 0,
    );
    const loopNow =
      loopClipNow != null
        ? {
            start: Math.max(0, loopClipNow.transition_start - LOOP_PAD),
            end: Math.min(tl.duration, loopClipNow.start + LOOP_PAD),
          }
        : null;

    if (clock.playing && loopNow && t >= loopNow.end) {
      setClock(loopNow.start);
      t = loopNow.start;
    } else if (clock.playing && t >= tl.duration - 0.005 && !loopNow) {
      clock.playing = false;
      setPlaying(false);
      setClock(Math.max(0, tl.duration - 0.001));
      t = Math.max(0, tl.duration - 0.001);
    }
    t = Math.min(Math.max(t, 0), Math.max(0, tl.duration - 0.001));

    const entries = activeClips(tl, t).filter(
      (entry) => !entry.clip.media_missing,
    );
    const neededIds = entries.map((entry) => entry.clip.clip_id);
    const slots = slotsRef.current;

    entries.forEach((entry, entryIndex) => {
      let slotIndex = slots.findIndex(
        (slot) => slot.clipId === entry.clip.clip_id,
      );
      if (slotIndex < 0) {
        slotIndex = slots.findIndex(
          (slot) => slot.clipId === null || !neededIds.includes(slot.clipId),
        );
      }
      if (slotIndex < 0) return;
      const slot = slots[slotIndex];
      const video = videos[slotIndex]!;
      const url = api.mediaStreamUrl(entry.clip.media_id);
      if (slot.clipId !== entry.clip.clip_id) {
        slot.clipId = entry.clip.clip_id;
        if (!video.src.endsWith(url)) video.src = url;
      }
      const sourceTime =
        entry.clip.source_in + Math.max(0, t - entry.windowStart);
      const drift = Math.abs(video.currentTime - sourceTime);
      if (clock.playing) {
        if (drift > 0.15) video.currentTime = sourceTime;
        if (video.paused) void video.play().catch(() => undefined);
      } else {
        if (!video.paused) video.pause();
        if (drift > 0.01) video.currentTime = sourceTime;
      }
      // Linear dissolve: outgoing clip stays opaque underneath, the
      // incoming clip fades in on top; gains match acrossfade=tri.
      const isTop = entryIndex === entries.length - 1;
      video.style.zIndex = isTop ? "2" : "1";
      video.style.opacity = isTop ? String(entry.gain) : "1";
      const gain = gainCache.get(video);
      if (gain) gain.gain.value = entry.clip.volume * entry.gain;
    });

    slots.forEach((slot, slotIndex) => {
      if (slot.clipId && neededIds.includes(slot.clipId)) return;
      const video = videos[slotIndex]!;
      if (!video.paused) video.pause();
      video.style.opacity = "0";
      const gain = gainCache.get(video);
      if (gain) gain.gain.value = 0;
    });

    setPosition(t);
  };

  const start = () => {
    if (!timeline || timeline.duration === 0) return;
    void sharedAudioContext?.resume();
    let t = now();
    if (loopRange && (t < loopRange.start || t >= loopRange.end)) {
      t = loopRange.start;
    } else if (!loopRange && t >= timeline.duration - 0.02) {
      t = 0;
    }
    setClock(t);
    clockRef.current.playing = true;
    setPlaying(true);
  };

  const pause = () => {
    setClock(now());
    clockRef.current.playing = false;
    setPlaying(false);
  };

  const restart = () => {
    pause();
    setClock(0);
    setPosition(0);
  };

  const scrub = (value: number) => {
    pause();
    setClock(value);
    setPosition(value);
  };

  const stepFrame = (direction: -1 | 1) => {
    pause();
    const next = Math.min(
      Math.max(now() + direction * FRAME, 0),
      Math.max(0, duration - 0.001),
    );
    setClock(next);
    setPosition(next);
  };

  const anyMissingVisible =
    timeline != null &&
    timeline.clips.some(
      (clip) =>
        clip.media_missing &&
        position >= clip.start - clip.transition &&
        position < clip.end - clip.transition,
    );

  return (
    <section className="panel preview">
      <div className="preview-stage">
        {videoRefs.map((ref, index) => (
          <video
            key={index}
            ref={ref}
            playsInline
            preload="auto"
            style={{ position: "absolute", inset: 0, opacity: 0 }}
          />
        ))}
        {anyMissingVisible && (
          <div className="missing-badge">素材缺失，占位预览</div>
        )}
        {loopRange && (
          <button
            type="button"
            className="loop-badge"
            title="点击停止循环试听"
            onClick={() => setLoopClip(null)}
          >
            <Repeat size={13} /> 循环试听转场 {formatTime(loopRange.start)} –{" "}
            {formatTime(loopRange.end)}
          </button>
        )}
      </div>
      <div className="preview-controls">
        <button type="button" onClick={restart} title="回到开头">
          <SkipBack size={18} />
        </button>
        <button
          type="button"
          onClick={() => stepFrame(-1)}
          title="上一帧（1/30s，与导出一致）"
        >
          <StepBack size={18} />
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
        <button
          type="button"
          onClick={() => stepFrame(1)}
          title="下一帧（1/30s，与导出一致）"
        >
          <StepForward size={18} />
        </button>
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
      {(timeline?.missing_media_ids.length ?? 0) > 0 && (
        <p className="warning">
          有 {timeline!.missing_media_ids.length}{" "}
          段素材缺失；补齐同名素材后即可恢复，工程仍可继续编辑。
        </p>
      )}
    </section>
  );
}
