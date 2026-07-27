import { ApiError, request } from "$lib/api-transport";
import type {
  AccessToken,
  ApiConfig,
  AssetCountResponse,
  AssetDetail,
  AssetSummary,
  AudioTrack,
  BatchUploadResponse,
  FolderNode,
  FolderTree,
  Job,
  Speaker,
  SummaryResponse,
  UploadCompletion,
  UploadEntry,
  UploadProgress,
  UploadSession,
  VisualEventDetectionResponse,
} from "$lib/api-types";

const uploadChunkSize = 8 * 1024 * 1024;
const uploadSessionPrefix = "stt-vault-upload:";
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
export async function fetchAsset(id: string): Promise<AssetDetail> {
  return request(`/api/assets/${id}`);
}
export async function uploadAsset(
  file: File,
  path = file.name,
  onProgress: (progress: UploadProgress) => void = () => {},
): Promise<UploadCompletion> {
  const storageKey = `${uploadSessionPrefix}${path}:${file.size}:${file.lastModified}`;
  let upload = await resumeUpload(storageKey, path, file.size);
  onProgress({ filename: path, uploaded: upload.offset, total: file.size });
  while (upload.offset < file.size) {
    const start = upload.offset;
    const endExclusive = Math.min(start + uploadChunkSize, file.size);
    let retries = 0;
    while (true) {
      try {
        upload = await request<UploadSession>(`/api/uploads/${upload.id}`, {
          method: "PUT",
          headers: {
            "Content-Range": `bytes ${start}-${endExclusive - 1}/${file.size}`,
          },
          body: file.slice(start, endExclusive),
        });
        break;
      } catch (error) {
        if (++retries >= 3) throw error;
        upload = await request<UploadSession>(`/api/uploads/${upload.id}`);
        if (upload.offset !== start) break;
      }
    }
    onProgress({ filename: path, uploaded: upload.offset, total: file.size });
  }
  const result = await request<UploadCompletion>(
    `/api/uploads/${upload.id}/complete`,
    { method: "POST" },
  );
  localStorage.removeItem(storageKey);
  return result;
}
async function resumeUpload(
  storageKey: string,
  filename: string,
  size: number,
): Promise<UploadSession> {
  const existingId = localStorage.getItem(storageKey);
  if (existingId) {
    try {
      return await request<UploadSession>(`/api/uploads/${existingId}`);
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) throw error;
      localStorage.removeItem(storageKey);
    }
  }
  const upload = await request<UploadSession>("/api/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, size }),
  });
  localStorage.setItem(storageKey, upload.id);
  return upload;
}
export async function uploadAssetBatch(
  entries: UploadEntry[],
  onProgress: (progress: UploadProgress) => void = () => {},
): Promise<BatchUploadResponse> {
  const results: BatchUploadResponse["results"] = [];
  for (const entry of entries) {
    try {
      const result = await uploadAsset(entry.file, entry.path, onProgress);
      results.push({ path: entry.path, status: "queued", id: result.id });
    } catch (error) {
      results.push({
        path: entry.path,
        status: "failed",
        detail: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return { results };
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
