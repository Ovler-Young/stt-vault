import type { AssetDetail, JobEvent } from "$lib/api-types";
import {
  fetchAsset,
  fetchAssetEvents,
  recomputeAssetSpeakers,
} from "$lib/api-endpoints";

export type AssetLoadResult = {
  asset: AssetDetail;
  eventHistory: JobEvent[];
  autoMatchedAssetId: string;
  speakerMatchError: string | null;
};

type AssetDetailWithEventHistory = {
  asset: AssetDetail;
  eventHistory: JobEvent[];
};

export function hasUnmatchedSpeakers(asset: AssetDetail) {
  if (asset.status !== "success" && asset.status !== "partial") return false;
  return (asset.transcript_segments ?? []).some((segment) => {
    const displayName = segment.speaker_name?.trim();
    return (
      /^SPEAKER_\d+$/.test(segment.speaker) &&
      (!displayName || displayName === segment.speaker)
    );
  });
}

export async function loadAssetWithSpeakerMatching(
  assetId: string,
  autoMatchedAssetId: string,
): Promise<AssetLoadResult> {
  let loaded = await fetchAssetWithEventHistory(assetId);
  let { asset, eventHistory } = loaded;
  let speakerMatchError: string | null = null;
  let nextAutoMatchedAssetId = autoMatchedAssetId;

  if (asset.id !== autoMatchedAssetId && hasUnmatchedSpeakers(asset)) {
    nextAutoMatchedAssetId = asset.id;
    try {
      await recomputeAssetSpeakers(asset.id);
      loaded = await fetchAssetWithEventHistory(asset.id);
      ({ asset, eventHistory } = loaded);
    } catch (error) {
      speakerMatchError =
        error instanceof Error ? error.message : String(error);
    }
  }

  return {
    asset,
    eventHistory,
    autoMatchedAssetId: nextAutoMatchedAssetId,
    speakerMatchError,
  };
}

async function fetchAssetWithEventHistory(
  assetId: string,
): Promise<AssetDetailWithEventHistory> {
  const [asset, eventHistory] = await Promise.all([
    fetchAsset(assetId),
    fetchAssetEvents(assetId),
  ]);
  return { asset, eventHistory };
}
