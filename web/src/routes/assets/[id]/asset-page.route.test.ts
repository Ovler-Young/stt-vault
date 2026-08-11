import { flushSync, mount, tick, unmount } from "svelte";
import { writable } from "svelte/store";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AssetDetail } from "$lib/api/types";
import transcriptPaneSource from "./components/TranscriptPane.svelte?raw";

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

  it("renders exact whitespace-free, punctuation, and repeated timed-unit text", async () => {
    loadAssetWithSpeakerMatching.mockResolvedValue({
      asset: {
        ...asset,
        transcript_segments: [
          {
            ...asset.transcript_segments![0],
            timed_units: [
              {
                unit_index: 0,
                text: "你好",
                start_ms: 0,
                end_ms: 450,
                confidence: null,
                language: "zh",
                token_kind: "word",
              },
              {
                unit_index: 1,
                text: ",",
                start_ms: 4250,
                end_ms: 5000,
                confidence: null,
                language: "en",
                token_kind: "punctuation",
              },
              {
                unit_index: 2,
                text: "你好",
                start_ms: 5000,
                end_ms: 5500,
                confidence: null,
                language: "zh",
                token_kind: "word",
              },
            ],
          },
        ],
      },
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
    const controls = Array.from(
      target.querySelectorAll<HTMLButtonElement>("[data-timed-unit-control]"),
    );
    const play = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(media, "play", { configurable: true, value: play });

    expect(controls.map((control) => control.textContent)).toEqual([
      "你好",
      ",",
      "你好",
    ]);
    expect(
      controls.map((control) => control.getAttribute("aria-label")),
    ).toEqual(["Seek to 0:00: 你好", "Seek to 0:04: ,", "Seek to 0:05: 你好"]);
    expect(
      target.querySelector('[data-segment-index="0"] > button'),
    ).toBeNull();

    for (const code of ["Enter", "Space"]) {
      const event = new KeyboardEvent("keydown", {
        code,
        bubbles: true,
        cancelable: true,
      });
      controls[1].dispatchEvent(event);
      expect(event.defaultPrevented).toBe(false);
      expect(play).not.toHaveBeenCalled();
    }

    flushSync(() => controls[1].click());
    expect(media?.currentTime).toBe(4.25);
    expect(play).toHaveBeenCalledOnce();

    if (media) {
      media.currentTime = 4.25;
      flushSync(() => media.dispatchEvent(new Event("timeupdate")));
      await tick();
      expect(
        target
          .querySelector('[data-timed-unit-control][data-unit-index="1"]')
          ?.classList.contains("active"),
      ).toBe(true);
    }
  });

  it("retains paused highlights, clears ended playback, and resets after seeking", async () => {
    loadAssetWithSpeakerMatching.mockResolvedValue({
      asset: {
        ...asset,
        transcript_segments: [
          {
            ...asset.transcript_segments![0],
            timed_units: [
              {
                unit_index: 0,
                text: "final",
                start_ms: 5000,
                end_ms: 5000,
                confidence: null,
                language: "en",
                token_kind: "word",
              },
            ],
          },
        ],
      },
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
    const control = target.querySelector<HTMLButtonElement>(
      "[data-timed-unit-control]",
    );
    const play = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(media, "play", { configurable: true, value: play });

    if (!media || !control) throw new Error("Timed playback controls missing");
    media.currentTime = 5;
    flushSync(() => media.dispatchEvent(new Event("timeupdate")));
    await tick();
    expect(control.classList.contains("active")).toBe(true);

    flushSync(() => media.dispatchEvent(new Event("pause")));
    await tick();
    expect(control.classList.contains("active")).toBe(true);

    flushSync(() => media.dispatchEvent(new Event("ended")));
    await tick();
    expect(control.classList.contains("active")).toBe(false);

    flushSync(() => media.dispatchEvent(new Event("seeking")));
    await tick();
    expect(control.classList.contains("active")).toBe(true);

    flushSync(() => media.dispatchEvent(new Event("ended")));
    flushSync(() => media.dispatchEvent(new Event("play")));
    await tick();
    expect(control.classList.contains("active")).toBe(true);

    flushSync(() => control.click());
    expect(media.currentTime).toBe(5);
    expect(play).toHaveBeenCalledOnce();
    expect(control.classList.contains("active")).toBe(true);
  });

  it("allows long unbroken timed units to shrink and wrap", () => {
    expect(transcriptPaneSource).toContain("min-width: 0;");
    expect(transcriptPaneSource).toContain("overflow-wrap: anywhere;");
    expect(transcriptPaneSource).toContain("word-break: break-word;");
  });
});
