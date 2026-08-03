import { flushSync, mount, tick, unmount } from "svelte";
import { writable } from "svelte/store";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AssetDetail } from "$lib/api/types";

const { fetchAssetAudioTracks, loadAssetWithSpeakerMatching } = vi.hoisted(
  () => ({
    fetchAssetAudioTracks: vi.fn(),
    loadAssetWithSpeakerMatching: vi.fn(),
  }),
);

vi.mock("$app/stores", () => ({
  page: writable({ params: { id: "asset-1" } }),
}));
vi.mock("$lib/api/endpoints", () => ({
  deleteAsset: vi.fn(),
  detectAssetVisualEvents: vi.fn(),
  fetchAssetAudioTracks,
  retryAsset: vi.fn(),
}));
vi.mock("./asset-load.controller", () => ({ loadAssetWithSpeakerMatching }));

import AssetPage from "./+page.svelte";

const asset: AssetDetail = {
  id: "asset-1",
  filename: "recording.m4a",
  media_type: "audio",
  duration: 30,
  status: "processing",
  created_at: 0,
  updated_at: 0,
  original_path: "/recordings/recording.m4a",
  transcript_segments: [
    {
      start: 4,
      end: 8,
      speaker: "SPEAKER_00",
      text: "Timestamp target",
    },
  ],
  visual_events: [],
};

describe("asset page transcript playback", () => {
  let component: ReturnType<typeof mount> | undefined;
  let target: HTMLDivElement | undefined;

  afterEach(() => {
    if (component) unmount(component);
    target?.remove();
    component = undefined;
    target = undefined;
    vi.clearAllMocks();
  });

  it("seeks and starts the route-bound audio element when a transcript timestamp is clicked", async () => {
    loadAssetWithSpeakerMatching.mockResolvedValue({
      asset,
      eventHistory: [],
      autoMatchedAssetId: "asset-1",
    });
    fetchAssetAudioTracks.mockResolvedValue([]);
    target = document.createElement("div");
    document.body.append(target);
    component = mount(AssetPage, { target });
    await tick();
    await tick();

    const media = target.querySelector<HTMLAudioElement>("audio");
    const transcriptButton = target.querySelector<HTMLButtonElement>(
      '[data-segment-index="0"]',
    );
    const play = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(media, "play", { configurable: true, value: play });

    flushSync(() => transcriptButton?.click());

    expect(media?.currentTime).toBe(4);
    expect(play).toHaveBeenCalledOnce();
  });
});
