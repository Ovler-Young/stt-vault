export type JsonPrimitive = boolean | number | string | null;
export type JsonValue =
  JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type AccessToken = {
  access_token: string;
  token_type: "bearer";
  expires_in: number | null;
};
export type AssetSummary = {
  id: string;
  filename: string;
  title?: string | null;
  recorded_at?: number | null;
  parent_folder_id?: string | null;
  media_type: "audio" | "video";
  duration: number | null;
  status: "queued" | "processing" | "success" | "partial" | "failed";
  summary_status?: "running" | "success" | "failed" | null;
  error?: JsonValue;
  created_at: number;
  updated_at: number;
};
export type ApiConfig = {
  auth_required: boolean;
  transcribe_model: string;
  senko_device: string;
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
export type FolderTree = { folders: FolderNode[]; assets: AssetSummary[] };
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
export type Job = {
  id: string;
  asset_id: string;
  filename: string;
  media_type: "audio" | "video";
  duration: number | null;
  status: "queued" | "processing" | "success" | "partial" | "failed";
  stage: string | null;
  error?: JsonValue;
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
  level: "info" | "warning" | "error";
  stage: string | null;
  message: string;
  payload?: JsonValue;
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
export type AssetDetail = AssetSummary & {
  original_path: string;
  transcript_segments?: TranscriptSegment[];
  exports?: Record<string, string>;
  diarization_stats?: Record<string, JsonValue>;
  speaker_centroids?: Record<string, number[]>;
  job?: Job;
  events?: JobEvent[];
  event_history?: JobEvent[];
  visual_events?: VisualEvent[];
  summary_text?: string;
  summary_error?: string;
  summary_model?: string;
};
export type UploadSession = {
  id: string;
  filename: string;
  size: number;
  offset: number;
};
export type UploadProgress = {
  filename: string;
  uploaded: number;
  total: number;
};
export type UploadCompletion = { id: string; status: string };
export type UploadEntry = { file: File; path: string };
export type BatchUploadResult = {
  path: string;
  status: "queued" | "failed";
  id?: string;
  detail?: string;
};
export type BatchUploadResponse = { results: BatchUploadResult[] };
export type AssetCountResponse = { assets: number };
export type SummaryResponse = {
  status: string;
  summary: string;
  title: string;
};
export type VisualEventDetectionResponse = { events: number };
