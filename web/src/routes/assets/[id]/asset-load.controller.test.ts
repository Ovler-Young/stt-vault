import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AssetDetail } from "$lib/api/types";

const { fetchAsset, fetchAssetEvents, recomputeAssetSpeakers } = vi.hoisted(
  () => ({
    fetchAsset: vi.fn(),
    fetchAssetEvents: vi.fn(),
    recomputeAssetSpeakers: vi.fn(),
  }),
);

vi.mock("$lib/api/endpoints", () => ({
  fetchAsset,
  fetchAssetEvents,
  recomputeAssetSpeakers,
}));

import {
  hasUnmatchedSpeakers,
  loadAssetWithSpeakerMatching,
} from "./asset-load.controller";

const asset: AssetDetail = {
  id: "asset-1",
  filename: "recording.mp3",
  media_type: "audio",
  duration: 1,
  status: "success",
  created_at: 0,
  updated_at: 0,
  original_path: "/recording.mp3",
  transcript_segments: [
    { start: 0, end: 1, speaker: "SPEAKER_00", text: "one" },
  ],
};

describe("asset load controller", () => {
  beforeEach(() => vi.resetAllMocks());

  it("recognizes unmatched local speakers only for completed assets", () => {
    expect(hasUnmatchedSpeakers(asset)).toBe(true);
    expect(hasUnmatchedSpeakers({ ...asset, status: "queued" })).toBe(false);
  });

  it("matches an asset once before returning its refreshed detail", async () => {
    fetchAsset.mockResolvedValueOnce(asset).mockResolvedValueOnce({
      ...asset,
      transcript_segments: [
        {
          start: 0,
          end: 1,
          speaker: "SPEAKER_00",
          speaker_name: "Ada",
          text: "one",
        },
      ],
    });
    fetchAssetEvents
      .mockResolvedValueOnce([
        { id: 1, level: "info", message: "started", created_at: 1 },
      ])
      .mockResolvedValueOnce([
        { id: 2, level: "info", message: "matched", created_at: 2 },
      ]);

    await expect(
      loadAssetWithSpeakerMatching("asset-1", ""),
    ).resolves.toMatchObject({
      autoMatchedAssetId: "asset-1",
      speakerMatchError: null,
      eventHistory: [
        { id: 2, level: "info", message: "matched", created_at: 2 },
      ],
    });
    expect(recomputeAssetSpeakers).toHaveBeenCalledWith("asset-1");
    expect(fetchAsset).toHaveBeenCalledTimes(2);
    expect(fetchAssetEvents).toHaveBeenNthCalledWith(1, "asset-1");
    expect(fetchAssetEvents).toHaveBeenNthCalledWith(2, "asset-1");
  });
});
