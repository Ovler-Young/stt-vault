import { request } from "$lib/api-transport";
import type {
  AccessToken,
  ApiConfig,
  AssetCountResponse,
  AssetDetail,
  AssetSummary,
  AudioTrack,
  FolderNode,
  FolderTree,
  Job,
  JobEvent,
  Speaker,
  SummaryResponse,
  VisualEventDetectionResponse,
} from "$lib/api-types";

export async function fetchConfig(): Promise<ApiConfig> {
  return request("/api/config");
}
export async function fetchAssets(): Promise<AssetSummary[]> {
  return request("/api/assets");
}
export async function fetchAssetAudioTracks(
  assetId: string,
): Promise<AudioTrack[]> {
  return request(`/api/assets/${assetId}/audio-tracks`);
}
export async function fetchJobs(): Promise<Job[]> {
  return request("/api/jobs");
}
export async function fetchSpeakers(): Promise<Speaker[]> {
  return request("/api/speakers");
}
export async function renameSpeaker(
  id: string,
  displayName: string,
): Promise<Speaker> {
  return request(`/api/speakers/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: displayName }),
  });
}
export async function mergeSpeaker(
  targetId: string,
  sourceId: string,
): Promise<Speaker> {
  return request(`/api/speakers/${targetId}/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_speaker_id: sourceId }),
  });
}
export async function recomputeAllSpeakers(): Promise<AssetCountResponse> {
  return request("/api/speakers/recompute", { method: "POST" });
}
export async function deleteSpeaker(id: string): Promise<void> {
  await request(`/api/speakers/${id}`, { method: "DELETE" });
}
export async function fetchAsset(
  id: string,
  includeEventHistory = false,
): Promise<AssetDetail> {
  const query = includeEventHistory ? "" : "?include_event_history=false";
  return request(`/api/assets/${id}${query}`);
}
export async function fetchAssetEvents(id: string): Promise<JobEvent[]> {
  return request(`/api/assets/${id}/events`);
}
export async function fetchFolderTree(): Promise<FolderTree> {
  return request("/api/folders");
}
export async function createFolder(
  name: string,
  parentId: string | null,
): Promise<FolderNode> {
  return request("/api/folders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, parent_id: parentId }),
  });
}
export async function renameFolder(
  id: string,
  name: string,
): Promise<FolderNode> {
  return request(`/api/folders/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}
export async function deleteFolder(id: string): Promise<void> {
  await request(`/api/folders/${id}`, { method: "DELETE" });
}
export async function moveFolder(
  id: string,
  parentId: string | null,
): Promise<void> {
  await request(`/api/folders/${id}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_id: parentId }),
  });
}
export async function moveAsset(
  id: string,
  parentFolderId: string | null,
): Promise<void> {
  await request(`/api/assets/${id}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_folder_id: parentFolderId }),
  });
}
export async function deleteAsset(id: string): Promise<void> {
  await request(`/api/assets/${id}`, { method: "DELETE" });
}
export async function retryAsset(id: string): Promise<void> {
  await request(`/api/assets/${id}/retry`, { method: "POST" });
}
export async function summarizeAsset(id: string): Promise<SummaryResponse> {
  return request(`/api/assets/${id}/summary`, { method: "POST" });
}
export async function recomputeAssetSpeakers(
  id: string,
): Promise<AssetCountResponse> {
  return request(`/api/assets/${id}/speaker-matches/recompute`, {
    method: "POST",
  });
}
export async function detectAssetVisualEvents(
  id: string,
): Promise<VisualEventDetectionResponse> {
  return request(`/api/assets/${id}/visual-events`, { method: "POST" });
}
export async function saveAssetSpeaker(
  assetId: string,
  localSpeaker: string,
  displayName: string,
): Promise<Speaker> {
  return request(
    `/api/assets/${assetId}/speakers/${encodeURIComponent(localSpeaker)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName }),
    },
  );
}
export type { AccessToken };
