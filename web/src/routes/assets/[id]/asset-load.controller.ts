import type { AssetDetail } from "$lib/api-types";
import { fetchAsset, recomputeAssetSpeakers } from "$lib/api-endpoints";

export type AssetLoadResult = {
  asset: AssetDetail;
  autoMatchedAssetId: string;
  speakerMatchError: string | null;
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
  let asset = await fetchAsset(assetId);
  let speakerMatchError: string | null = null;
  let nextAutoMatchedAssetId = autoMatchedAssetId;

  if (asset.id !== autoMatchedAssetId && hasUnmatchedSpeakers(asset)) {
    nextAutoMatchedAssetId = asset.id;
    try {
      await recomputeAssetSpeakers(asset.id);
      asset = await fetchAsset(asset.id);
    } catch (error) {
      speakerMatchError =
        error instanceof Error ? error.message : String(error);
    }
  }

  return {
    asset,
    autoMatchedAssetId: nextAutoMatchedAssetId,
    speakerMatchError,
  };
}
