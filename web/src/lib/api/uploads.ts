import { ApiError, request } from "$lib/api-transport";
import type {
  BatchUploadResponse,
  UploadCompletion,
  UploadEntry,
  UploadProgress,
  UploadSession,
} from "$lib/api-types";

const uploadChunkSize = 8 * 1024 * 1024;
const uploadSessionPrefix = "stt-vault-upload:";

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
