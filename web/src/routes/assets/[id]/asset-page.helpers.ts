import { authenticatedResourceUrl, type AudioTrack, type AssetDetail, type TranscriptSegment, type VisualEvent } from '$lib/api';
import type { LocalSpeakerRow } from './asset-page.types';

export const MEDIA_PANE_MIN_WIDTH = 420;
export const TRANSCRIPT_PANE_MIN_WIDTH = 360;
export const PANE_DIVIDER_WIDTH = 12;

export function segmentMediaStart(segment: Pick<TranscriptSegment, 'start' | 'chunk_start'>) {
  return typeof segment.chunk_start === 'number' && Number.isFinite(segment.chunk_start)
    ? segment.chunk_start
    : segment.start;
}

export function segmentMediaEnd(segment: Pick<TranscriptSegment, 'end' | 'chunk_end'>) {
  return typeof segment.chunk_end === 'number' && Number.isFinite(segment.chunk_end) ? segment.chunk_end : segment.end;
}

export function activeTranscriptSegmentIndex(segments: TranscriptSegment[], time: number) {
  return segments.findIndex((segment) => time >= segmentMediaStart(segment) && time < segmentMediaEnd(segment));
}

export function activeVisualEventIndex(markers: VisualEvent[], time: number) {
  let activeIndex = -1;
  for (let index = 0; index < markers.length; index += 1) {
    if (markers[index].timestamp > time + 0.25) break;
    activeIndex = markers[index].event_index;
  }
  return activeIndex;
}

export function audioTrackLabel(track: AudioTrack) {
  const parts = [`Track ${track.audio_index + 1}`];
  if (track.title) parts.push(track.title);
  if (track.language) parts.push(track.language);
  if (track.channel_layout) parts.push(track.channel_layout);
  else if (track.channels) parts.push(`${track.channels}ch`);
  if (track.codec_name) parts.push(track.codec_name);
  return parts.join(' · ');
}

export function progressText(asset: AssetDetail) {
  const job = asset.job;
  if (!job || !job.progress_total_chunks) return '';
  return `${job.progress_done_chunks}/${job.progress_total_chunks}`;
}

export function localSpeakerRows(asset: AssetDetail): LocalSpeakerRow[] {
  const speakers = new Set<string>();
  for (const speaker of Object.keys(asset.speaker_centroids ?? {})) speakers.add(speaker);
  for (const segment of asset.transcript_segments ?? []) speakers.add(segment.speaker);

  return [...speakers].sort().map((localSpeaker) => {
    const segments = asset.transcript_segments?.filter((segment) => segment.speaker === localSpeaker) ?? [];
    const named = segments.find((segment) => segment.speaker_name && segment.speaker_name !== localSpeaker);
    const matched = segments.find((segment) => segment.speaker_id && segment.speaker_id !== localSpeaker);
    return {
      localSpeaker,
      displayName: named?.speaker_name ?? localSpeaker,
      speakerId: matched?.speaker_id,
      similarity: matched?.speaker_similarity ?? null,
      count: segments.length,
      firstStart: segments.length ? Math.min(...segments.map((segment) => segmentMediaStart(segment))) : null,
      lastEnd: segments.length ? Math.max(...segments.map((segment) => segmentMediaEnd(segment))) : null
    };
  });
}

export function mediaUrl(assetId: string, selectedAudioTrack: string) {
  const params = new URLSearchParams();
  if (selectedAudioTrack !== 'default') {
    params.set('audio_track', selectedAudioTrack);
  }
  return authenticatedResourceUrl(`/api/assets/${assetId}/media`, params);
}

export function thumbnailUrl(assetId: string, event: VisualEvent) {
  return authenticatedResourceUrl(`/api/assets/${assetId}/visual-events/${event.event_index}/thumbnail`);
}

export function exportHref(assetId: string, format: string) {
  return authenticatedResourceUrl(`/api/assets/${assetId}/exports/${format}`);
}

export function clampMediaPaneWidth(width: number, availableWidth: number) {
  const maxWidth = Math.max(MEDIA_PANE_MIN_WIDTH, availableWidth - TRANSCRIPT_PANE_MIN_WIDTH);
  return Math.min(maxWidth, Math.max(MEDIA_PANE_MIN_WIDTH, width));
}

export function formatStatValue(value: unknown) {
  if (typeof value === 'number') return `${Number(value.toFixed(3))}`;
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
