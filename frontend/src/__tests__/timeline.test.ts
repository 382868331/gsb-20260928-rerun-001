import { describe, expect, it } from "vitest";
import { clipDuration, layout, totalDuration, type Clip } from "../types";

const mk = (id: string, inp: number, outp: number, speed = 1): Clip =>
  ({ id, media_id: "m", in_point: inp, out_point: outp, speed, volume: 1, color: "none" });

describe("timeline layout", () => {
  it("lays clips out back-to-back from zero", () => {
    const l = layout({ clips: [mk("a", 0, 2), mk("b", 0, 3), mk("c", 1, 4)] });
    expect(l.map((x) => [x.start, x.end])).toEqual([[0, 2], [2, 5], [5, 8]]);
  });

  it("ripples following clips when an earlier clip is trimmed", () => {
    const before = layout({ clips: [mk("a", 0, 2), mk("b", 0, 3)] });
    expect(before[1].start).toBe(2);
    const after = layout({ clips: [mk("a", 0, 1.2), mk("b", 0, 3)] });
    expect(after[0].end).toBeCloseTo(1.2);
    expect(after[1].start).toBeCloseTo(1.2);
    expect(after[1].end).toBeCloseTo(4.2);
  });

  it("accounts for speed in timeline duration", () => {
    expect(clipDuration(mk("a", 0, 4, 2))).toBe(2);
    expect(clipDuration(mk("a", 0, 3, 0.5))).toBe(6);
    expect(totalDuration({ clips: [mk("a", 0, 4, 2), mk("b", 0, 2, 1)] })).toBe(4);
  });
});
