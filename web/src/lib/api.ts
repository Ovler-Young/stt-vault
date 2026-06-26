export type AssetSummary = {
  id: string;
  filename: string;
  media_type: 'audio' | 'video';
  duration: number | null;
  status: 'queued' | 'processing' | 'success' | 'failed';
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

