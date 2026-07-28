import { flushSync, mount, tick, unmount } from "svelte";
import { describe, expect, it, vi } from "vitest";

import pageSource from "./+page.svelte?raw";
import AssetPageShell from "./components/AssetPageShell.svelte";
import shellSource from "./components/AssetPageShell.svelte?raw";
import type { AssetDetail } from "$lib/api/types";

const asset: AssetDetail = {
  id: "asset-1",
  filename: "meeting.mp4",
  media_type: "video",
  duration: 30,
  status: "processing",
  created_at: 0,
  updated_at: 0,
  original_path: "/recordings/meeting.mp4",
  transcript_segments: [
    {
      start: 4,
      end: 8,
      speaker: "SPEAKER_00",
      text: "Hello from the transcript.",
    },
  ],
  visual_events: [],
};

describe("asset page shell boundary", () => {
  it("keeps page lifecycle in the route and layout composition in the scoped shell", () => {
    expect(pageSource).toContain("<AssetPageShell");
    expect(pageSource).not.toContain("<ResizableAssetWorkspace>");
    expect(pageSource).not.toContain("<style>");

    expect(shellSource).toContain("<ResizableAssetWorkspace>");
    expect(shellSource).toContain("<AssetMediaPane");
    expect(shellSource).toContain("<TranscriptPane");
    expect(shellSource).toContain('<p class="error" aria-live="polite">');
    expect(shellSource).toContain("<style>");
  });

  it("wires mounted child interactions to route callbacks", async () => {
    const target = document.createElement("div");
    const onTranscriptSeek = vi.fn();
    const onTimeUpdate = vi.fn();
    const onDetectVisualEvents = vi.fn();
    document.body.append(target);

    const component = mount(AssetPageShell, {
      target,
      props: {
        asset,
        error: "",
        audioTracks: [],
        selectedAudioTrack: "default",
        playbackRate: 1,
        currentTime: 0,
        visualMessage: "",
        speakerMatchMessage: "",
        onRetry: async () => {},
        onRemove: async () => {},
        onTimeUpdate,
        onStartClock: () => {},
        onStopClock: () => {},
        onRestoreMediaSeek: () => {},
        onAudioTrackChange: async () => {},
        onPlaybackRateChange: () => {},
        onTimelineSeek: () => {},
        onDetectVisualEvents,
        onLoad: async () => {},
        onError: () => {},
        onTranscriptSeek,
        onEditSpeaker: () => {},
      },
    });
    await tick();

    try {
      const transcriptButton = target.querySelector<HTMLButtonElement>(
        '[data-segment-index="0"]',
      );
      const media = target.querySelector("video");
      const detectButton = Array.from(target.querySelectorAll("button")).find(
        (button) => button.textContent?.trim() === "Detect",
      );

      expect(transcriptButton).not.toBeNull();
      expect(media).not.toBeNull();
      expect(detectButton).toBeDefined();

      flushSync(() => transcriptButton?.click());
      flushSync(() => media?.dispatchEvent(new Event("timeupdate")));
      flushSync(() => detectButton?.click());

      expect(onTranscriptSeek).toHaveBeenCalledWith(
        asset.transcript_segments?.[0],
      );
      expect(onTimeUpdate).toHaveBeenCalledTimes(1);
      expect(onDetectVisualEvents).toHaveBeenCalledTimes(1);
    } finally {
      unmount(component);
      target.remove();
    }
  });
});
