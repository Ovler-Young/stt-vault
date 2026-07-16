export type AssetSummary = {
  id: string;
  filename: string;
  title?: string | null;
  recorded_at?: number | null;
  parent_folder_id?: string | null;
  media_type: 'audio' | 'video';
  duration: number | null;
  status: 'queued' | 'processing' | 'success' | 'partial' | 'failed';
  summary_status?: 'running' | 'success' | 'failed' | null;
  error?: unknown;
  created_at: number;
  updated_at: number;
};

export type FolderNode = {
  id: string;
  name: string;
  parent_id: string | null;
  created_at: number;
  updated_at: number;
  children: FolderNode[];
  assets: AssetSummary[];
};

export type FolderTree = {
  folders: FolderNode[];
  assets: AssetSummary[];
};

export type TranscriptSegment = {
  start: number;
  end: number;
  chunk_start?: number;
  chunk_end?: number;
  speaker: string;
  speaker_id?: string;
  speaker_name?: string;
  speaker_similarity?: number | null;
  text: string;
};

export type AssetDetail = AssetSummary & {
  original_path: string;
  transcript_segments?: TranscriptSegment[];
  exports?: Record<string, string>;
  diarization_stats?: Record<string, unknown>;
  speaker_centroids?: Record<string, number[]>;
  job?: Job;
  events?: JobEvent[];
  event_history?: JobEvent[];
  visual_events?: VisualEvent[];
  summary_text?: string;
  summary_error?: string;
  summary_model?: string;
};

export type Job = {
  id: string;
  asset_id: string;
  filename: string;
  media_type: 'audio' | 'video';
  duration: number | null;
  status: 'queued' | 'processing' | 'success' | 'partial' | 'failed';
  stage: string | null;
  error?: unknown;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  progress_total_chunks: number;
  progress_done_chunks: number;
  progress_failed_chunks: number;
  next_retry_at: number | null;
};

export type JobEvent = {
  id: number;
  level: 'info' | 'warning' | 'error';
  stage: string | null;
  message: string;
  payload?: unknown;
  run_attempt?: number;
  created_at: number;
};

export type Speaker = {
  id: string;
  display_name: string;
  centroid: number[];
  sample_count: number;
  created_at: number;
  updated_at: number;
};

export type VisualEvent = {
  event_index: number;
  timestamp: number;
  score: number;
  kind: string;
  created_at: number;
};

export type AudioTrack = {
  audio_index: number;
  stream_index: number | null;
  codec_name: string | null;
  channels: number | null;
  channel_layout: string | null;
  bit_rate: string | null;
  language: string | null;
  title: string | null;
};

const accessTokenKey = 'stt-vault-access-token';

export type AccessToken = {
  access_token: string;
  token_type: 'bearer';
  expires_in: number | null;
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export function getStoredAccessToken(): string {
  if (typeof localStorage === 'undefined') return '';
  const storedToken = localStorage.getItem(accessTokenKey);
  if (storedToken) return storedToken;

  if (typeof sessionStorage === 'undefined') return '';
  const sessionToken = sessionStorage.getItem(accessTokenKey);
  if (sessionToken) localStorage.setItem(accessTokenKey, sessionToken);
  return sessionToken ?? '';
}

export function setStoredAccessToken(value: string) {
  if (typeof localStorage === 'undefined') return;
  if (value) localStorage.setItem(accessTokenKey, value);
  else localStorage.removeItem(accessTokenKey);
}

export function authenticatedResourceUrl(path: string, params = new URLSearchParams()): string {
  const token = getStoredAccessToken();
  if (token) params.set('access_token', token);
  const query = params.toString();
  return `${path}${query ? `?${query}` : ''}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getStoredAccessToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch(path, {
    ...init,
    headers
  });
  if (!response.ok) {
    throw new ApiError(response.status, `${response.status} ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

export async function login(password: string): Promise<AccessToken> {
  const token = await request<AccessToken>('/api/auth/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password })
  });
  setStoredAccessToken(token.access_token);
  return token;
}

export async function fetchConfig(): Promise<{ auth_required: boolean; transcribe_model: string; senko_device: string }> {
  return request('/api/config');
}

export async function fetchAssets(): Promise<AssetSummary[]> {
  return request('/api/assets');
}

export async function fetchAssetAudioTracks(assetId: string): Promise<AudioTrack[]> {
  return request(`/api/assets/${assetId}/audio-tracks`);
}

export async function fetchJobs(): Promise<Job[]> {
  return request('/api/jobs');
}

export async function fetchSpeakers(): Promise<Speaker[]> {
  return request('/api/speakers');
}

export async function renameSpeaker(id: string, displayName: string): Promise<Speaker> {
  return request(`/api/speakers/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: displayName })
  });
}

export async function mergeSpeaker(targetId: string, sourceId: string): Promise<Speaker> {
  return request(`/api/speakers/${targetId}/merge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_speaker_id: sourceId })
  });
}

export async function recomputeAllSpeakers(): Promise<{ assets: number }> {
  return request('/api/speakers/recompute', { method: 'POST' });
}

export async function deleteSpeaker(id: string): Promise<void> {
  await request(`/api/speakers/${id}`, { method: 'DELETE' });
}

export async function fetchAsset(id: string): Promise<AssetDetail> {
  return request(`/api/assets/${id}`);
}

const uploadChunkSize = 8 * 1024 * 1024;
const uploadSessionPrefix = 'stt-vault-upload:';

type UploadSession = { id: string; filename: string; size: number; offset: number };
export type UploadProgress = { filename: string; uploaded: number; total: number };

export async function uploadAsset(
  file: File,
  path = file.name,
  onProgress: (progress: UploadProgress) => void = () => {}
): Promise<{ id: string; status: string }> {
  const storageKey = `${uploadSessionPrefix}${path}:${file.size}:${file.lastModified}`;
  let upload = await resumeUpload(storageKey, path, file.size);
  onProgress({ filename: path, uploaded: upload.offset, total: file.size });

  while (upload.offset < file.size) {
    const start = upload.offset;
    const endExclusive = Math.min(start + uploadChunkSize, file.size);
    const body = file.slice(start, endExclusive);
    let retries = 0;
    while (true) {
      try {
        upload = await request<UploadSession>(`/api/uploads/${upload.id}`, {
          method: 'PUT',
          headers: { 'Content-Range': `bytes ${start}-${endExclusive - 1}/${file.size}` },
          body
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

  const result = await request<{ id: string; status: string }>(`/api/uploads/${upload.id}/complete`, {
    method: 'POST'
  });
  localStorage.removeItem(storageKey);
  return result;
}

async function resumeUpload(storageKey: string, filename: string, size: number): Promise<UploadSession> {
  const existingId = localStorage.getItem(storageKey);
  if (existingId) {
    try {
      return await request<UploadSession>(`/api/uploads/${existingId}`);
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) throw error;
      localStorage.removeItem(storageKey);
    }
  }
  const upload = await request<UploadSession>('/api/uploads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, size })
  });
  localStorage.setItem(storageKey, upload.id);
  return upload;
}

export type BatchUploadResult = { path: string; status: 'queued' | 'failed'; id?: string; detail?: string };

export async function uploadAssetBatch(
  entries: Array<{ file: File; path: string }>,
  onProgress: (progress: UploadProgress) => void = () => {}
): Promise<{ results: BatchUploadResult[] }> {
  const results: BatchUploadResult[] = [];
  for (const entry of entries) {
    try {
      const result = await uploadAsset(entry.file, entry.path, onProgress);
      results.push({ path: entry.path, status: 'queued', id: result.id });
    } catch (error) {
      results.push({
        path: entry.path,
        status: 'failed',
        detail: error instanceof Error ? error.message : String(error)
      });
    }
  }
  return { results };
}

export async function fetchFolderTree(): Promise<FolderTree> {
  return request('/api/folders');
}

export async function createFolder(name: string, parentId: string | null): Promise<FolderNode> {
  return request('/api/folders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, parent_id: parentId })
  });
}

export async function renameFolder(id: string, name: string): Promise<FolderNode> {
  return request(`/api/folders/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
}

export async function deleteFolder(id: string): Promise<void> {
  await request(`/api/folders/${id}`, { method: 'DELETE' });
}

export async function moveFolder(id: string, parentId: string | null): Promise<void> {
  await request(`/api/folders/${id}/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ parent_id: parentId })
  });
}

export async function moveAsset(id: string, parentFolderId: string | null): Promise<void> {
  await request(`/api/assets/${id}/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ parent_folder_id: parentFolderId })
  });
}

export async function deleteAsset(id: string): Promise<void> {
  await request(`/api/assets/${id}`, { method: 'DELETE' });
}

export async function retryAsset(id: string): Promise<void> {
  await request(`/api/assets/${id}/retry`, { method: 'POST' });
}

export async function summarizeAsset(id: string): Promise<{ status: string; summary: string; title: string }> {
  return request(`/api/assets/${id}/summary`, { method: 'POST' });
}

export async function recomputeAssetSpeakers(id: string): Promise<{ assets: number }> {
  return request(`/api/assets/${id}/speaker-matches/recompute`, { method: 'POST' });
}

export async function detectAssetVisualEvents(id: string): Promise<{ events: number }> {
  return request(`/api/assets/${id}/visual-events`, { method: 'POST' });
}

export async function saveAssetSpeaker(
  assetId: string,
  localSpeaker: string,
  displayName: string
): Promise<Speaker> {
  return request(`/api/assets/${assetId}/speakers/${encodeURIComponent(localSpeaker)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: displayName })
  });
}
