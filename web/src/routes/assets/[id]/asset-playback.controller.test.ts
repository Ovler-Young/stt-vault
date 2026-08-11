import { describe, expect, it } from "vitest";

import {
  activeTimedTranscriptUnitIndex,
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

  it("selects timed units on the absolute media timeline", () => {
    const units = [
      { unit_index: 4, text: "first", start_ms: 1000, end_ms: 3000 },
      { unit_index: 3, text: "later", start_ms: 2000, end_ms: 4000 },
      { unit_index: 2, text: "zero", start_ms: 4000, end_ms: 4000 },
      { unit_index: 1, text: "also zero", start_ms: 4000, end_ms: 4000 },
    ];

    expect(activeTimedTranscriptUnitIndex(units, 1.9995, false)).toBe(0);
    expect(activeTimedTranscriptUnitIndex(units, 2, false)).toBe(1);
    expect(activeTimedTranscriptUnitIndex(units, 4, false)).toBe(3);
    expect(activeTimedTranscriptUnitIndex(units, 4, true)).toBe(-1);
    expect(activeTimedTranscriptUnitIndex(units, 4.001, false)).toBe(-1);
  });

  it("keeps nonzero intervals half-open while resolving equal-start overlaps", () => {
    const units = [
      { unit_index: 3, text: "earlier", start_ms: 1000, end_ms: 3000 },
      { unit_index: 2, text: "later", start_ms: 2000, end_ms: 4000 },
      { unit_index: 1, text: "same start", start_ms: 2000, end_ms: 2500 },
    ];

    expect(activeTimedTranscriptUnitIndex(units, 1.9995, false)).toBe(0);
    expect(activeTimedTranscriptUnitIndex(units, 2, false)).toBe(2);
    expect(activeTimedTranscriptUnitIndex(units, 2.5, false)).toBe(1);
  });

  it("clears a nonzero unit exactly at its end boundary", () => {
    const units = [
      { unit_index: 0, text: "boundary", start_ms: 1000, end_ms: 2000 },
    ];

    expect(activeTimedTranscriptUnitIndex(units, 1.9995, false)).toBe(0);
    expect(activeTimedTranscriptUnitIndex(units, 2, false)).toBe(-1);
  });
});
