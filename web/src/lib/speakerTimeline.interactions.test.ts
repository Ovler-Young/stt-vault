import { describe, expect, it } from "vitest";

import {
  doubleClickTimelineWindow,
  draggedTimelineWindow,
  keyboardTimelineWindow,
  seekTimeForTimelineEvent,
  wheelTimelineWindow,
} from "./speakerTimeline.interactions";

const segments = [{ start: 10, end: 20, speaker: "SPEAKER_00", text: "one" }];

describe("speaker timeline interactions", () => {
  it("seeks to a segment boundary and focuses its double-click window", () => {
    expect(seekTimeForTimelineEvent(segments, 15)).toBe(10);
    expect(seekTimeForTimelineEvent(segments, 25)).toBe(25);
    expect(
      doubleClickTimelineWindow(segments, 15, 100, { start: 0, end: 1 }, 0.02),
    ).toEqual({
      start: 0,
      end: 0.35,
    });
  });

  it("handles keyboard, wheel, and drag window movement within bounds", () => {
    expect(
      keyboardTimelineWindow(
        "Escape",
        30,
        100,
        { start: 0.2, end: 0.6 },
        0.02,
        0.12,
        0.12,
      ),
    ).toEqual({
      start: 0,
      end: 1,
    });
    expect(
      wheelTimelineWindow(
        { start: 0.2, end: 0.6 },
        0.4,
        10,
        2,
        true,
        0.02,
        0.003,
        0.00125,
      ),
    ).toEqual({
      start: 0.20500000000000002,
      end: 0.605,
    });
    const dragged = draggedTimelineWindow(
      { start: 0.2, end: 0.6 },
      -100,
      200,
      0.02,
    );
    expect(dragged.start).toBeCloseTo(0.4);
    expect(dragged.end).toBeCloseTo(0.8);
  });
});
