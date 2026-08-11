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

  it("keeps the global Space shortcut outside interactive controls", () => {
    const handlers = actions();
    const event = new KeyboardEvent("keydown", {
      code: "Space",
      cancelable: true,
    });

    handleAssetKeydown(event, null, handlers);

    expect(event.defaultPrevented).toBe(true);
    expect(handlers.togglePlay).toHaveBeenCalledOnce();
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

  it("leaves timed-unit button activation to the browser", () => {
    const handlers = actions();
    const control = document.createElement("button");
    control.dataset.timedUnitControl = "";
    const child = document.createElement("span");
    control.append(child);

    for (const code of ["Enter", "Space"]) {
      const event = new KeyboardEvent("keydown", {
        code,
        bubbles: true,
        cancelable: true,
      });
      Object.defineProperty(event, "target", { value: child });
      const preventDefault = vi.spyOn(event, "preventDefault");

      handleAssetKeydown(event, null, handlers);

      expect(preventDefault).not.toHaveBeenCalled();
    }

    expect(handlers.togglePlay).not.toHaveBeenCalled();
  });
});
