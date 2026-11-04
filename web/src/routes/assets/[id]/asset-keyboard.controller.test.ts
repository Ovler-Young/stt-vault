import { describe, expect, it, vi } from "vitest";

import { handleAssetKeydown } from "./asset-keyboard.controller";

function actions() {
  return {
    togglePlay: vi.fn(),
    seekRelative: vi.fn(),
    seekPreviousSegment: vi.fn(),
    seekNextSegment: vi.fn(),
    seekPreviousSpeakerSegment: vi.fn(),
    seekNextSpeakerSegment: vi.fn(),
    seekToStart: vi.fn(),
    centerTimeline: vi.fn(),
    zoomTimeline: vi.fn(),
    panTimeline: vi.fn(),
  };
}

describe("asset keyboard controller", () => {
  it("dispatches playback and timeline commands", () => {
    const handlers = actions();
    const event = new KeyboardEvent("keydown", { code: "ArrowRight" });
    const preventDefault = vi.spyOn(event, "preventDefault");

    handleAssetKeydown(event, null, handlers);

    expect(handlers.seekRelative).toHaveBeenCalledWith(5);
    expect(preventDefault).toHaveBeenCalled();
  });

  it("does not consume keys typed into controls", () => {
    const handlers = actions();
    const input = document.createElement("input");
    const event = new KeyboardEvent("keydown", {
      code: "Space",
      bubbles: true,
    });
    Object.defineProperty(event, "target", { value: input });

    handleAssetKeydown(event, null, handlers);

    expect(handlers.togglePlay).not.toHaveBeenCalled();
  });
});
