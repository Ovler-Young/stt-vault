import { describe, expect, it } from "vitest";

import {
  adjacentSpeakerSegment,
  boundedSeekTime,
  nextSegment,
  previousSegment,
} from "./asset-playback.controller";
import { segmentMediaEnd, segmentMediaStart } from "./asset-page.helpers";

const segments = [
  { start: 0, end: 4, speaker: "SPEAKER_00", text: "one" },
  { start: 5, end: 9, speaker: "SPEAKER_01", text: "two" },
  { start: 10, end: 14, speaker: "SPEAKER_00", text: "three" },
];

describe("asset playback controller", () => {
  it("bounds seeks and finds timeline neighbours", () => {
    expect(boundedSeekTime(2, 10, -5)).toBe(0);
    expect(boundedSeekTime(8, 10, 5)).toBe(10);
    expect(nextSegment(segments, 4)).toBe(segments[1]);
    expect(previousSegment(segments, 6)).toBe(segments[0]);
  });

  it("finds speaker-specific neighbours", () => {
    expect(adjacentSpeakerSegment(segments, 2, "next")).toBe(segments[2]);
    expect(adjacentSpeakerSegment(segments, 12, "previous")).toBe(segments[0]);
  });

  it("shares finite transcript media boundaries with the page", () => {
    const segment = { start: 2, end: 8, speaker: "SPEAKER_00" };

    expect(segmentMediaStart(segment)).toBe(2);
    expect(segmentMediaEnd(segment)).toBe(8);
    expect(segmentMediaStart({ ...segment, chunk_start: 3 })).toBe(3);
    expect(segmentMediaEnd({ ...segment, chunk_end: 7 })).toBe(7);
    expect(segmentMediaStart({ ...segment, chunk_start: Number.NaN })).toBe(2);
    expect(
      segmentMediaEnd({ ...segment, chunk_end: Number.POSITIVE_INFINITY }),
    ).toBe(8);
  });
});
