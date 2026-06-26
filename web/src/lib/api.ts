export type AssetSummary = {
  id: string;
  filename: string;
  media_type: 'audio' | 'video';
  duration: number | null;
  status: 'queued' | 'processing' | 'success' | 'partial' | 'failed';
  error?: unknown;
  created_at: number;
  updated_at: number;
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
  diarization_stats?: Record<string, number>;
  speaker_centroids?: Record<string, number[]>;
  job?: Job;
  events?: JobEvent[];
  event_history?: JobEvent[];
  visual_events?: VisualEvent[];
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

const passwordKey = 'stt-vault-admin-password';

export function getStoredPassword(): string {
  return localStorage.getItem(passwordKey) ?? '';
}

export function setStoredPassword(value: string) {
  if (value) localStorage.setItem(passwordKey, value);
  else localStorage.removeItem(passwordKey);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const password = getStoredPassword();
  if (password) headers.set('X-STT-Admin-Password', password);

  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchConfig(): Promise<{ auth_required: boolean; transcribe_model: string; senko_device: string }> {
  return request('/api/config');
}

export async function fetchAssets(): Promise<AssetSummary[]> {
  return request('/api/assets');
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

export async function uploadAsset(file: File): Promise<{ id: string; status: string }> {
  const body = new FormData();
  body.append('file', file);
  return request('/api/assets', {
    method: 'POST',
    body
  });
}

export async function deleteAsset(id: string): Promise<void> {
  await request(`/api/assets/${id}`, { method: 'DELETE' });
}

export async function retryAsset(id: string): Promise<void> {
  await request(`/api/assets/${id}/retry`, { method: 'POST' });
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
