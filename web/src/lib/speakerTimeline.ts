import type { TranscriptSegment } from '$lib/api';

export type TimelineRow = 'zoom' | 'full';

export type TimelineWindow = {
  start: number;
  end: number;
};

export type SegmentPosition = {
  x: number;
  width: number;
};

export type TimelineHover = {
  row: TimelineRow;
  x: number;
  time: number;
  speaker: string | null;
};

const speakerColors = [
  '#bcab4d',
  '#5f9ea0',
  '#c46f5a',
  '#7f8ccf',
  '#7aa36f',
  '#c17db4',
  '#d18a3a',
  '#5b9f78',
  '#b86464',
  '#6f9bc2',
  '#a68a53',
  '#8f7ac0'
];

function clampRatio(value: number) {
  return Math.min(1, Math.max(0, value));
}

function hashSpeaker(speaker: string) {
  let hash = 0;
  for (const char of speaker) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  return hash;
}

export function speakerColor(speaker: string) {
  return speakerColors[hashSpeaker(speaker) % speakerColors.length];
}

export function effectiveTimelineDuration(duration: number | null, segments: TranscriptSegment[]) {
  return duration && duration > 0
    ? duration
    : Math.max(0, ...segments.map((segment) => segment.end));
}

export function currentTimelineRatio(currentTime: number, effectiveDuration: number) {
  return effectiveDuration ? clampRatio(currentTime / effectiveDuration) : 0;
}

export function segmentAt(segments: TranscriptSegment[], time: number) {
  return segments.find((segment) => time >= segment.start && time < segment.end) ?? null;
}

export function segmentPosition(
  segment: TranscriptSegment,
  effectiveDuration: number,
  windowStart: number,
  windowEnd: number
): SegmentPosition | null {
  if (!effectiveDuration) return null;
  const windowSize = Math.max(0.001, windowEnd - windowStart);
  const start = segment.start / effectiveDuration;
  const end = segment.end / effectiveDuration;
  const visibleStart = Math.max(start, windowStart);
  const visibleEnd = Math.min(end, windowEnd);
  if (visibleStart >= visibleEnd) return null;
  return {
    x: ((visibleStart - windowStart) / windowSize) * 100,
    width: Math.max(0.15, ((visibleEnd - visibleStart) / windowSize) * 100)
  };
}

export function clampTimelineWindow(start: number, end: number, minZoomSize: number): TimelineWindow {
  const nextSize = Math.min(1, Math.max(minZoomSize, end - start));
  let nextStart = Math.min(Math.max(0, start), 1 - nextSize);
  let nextEnd = nextStart + nextSize;
  if (nextEnd > 1) {
    nextEnd = 1;
    nextStart = Math.max(0, 1 - nextSize);
  }
  return { start: nextStart, end: nextEnd };
}

export function zoomWindowAround(
  window: TimelineWindow,
  center: number,
  scale: number,
  minZoomSize: number
): TimelineWindow {
  const clampedCenter = clampRatio(center);
  const currentSize = window.end - window.start;
  const nextSize = Math.min(1, Math.max(minZoomSize, currentSize * scale));
  const centerOffset = currentSize > 0 ? (clampedCenter - window.start) / currentSize : 0.5;
  const nextStart = clampedCenter - nextSize * clampRatio(centerOffset);
  return clampTimelineWindow(nextStart, nextStart + nextSize, minZoomSize);
}

export function panTimelineWindow(
  window: TimelineWindow,
  delta: number,
  minZoomSize: number
): TimelineWindow {
  const currentSize = window.end - window.start;
  if (currentSize >= 0.9999) return window;
  return clampTimelineWindow(window.start + delta, window.end + delta, minZoomSize);
}

export function eventTimeFromClientX(
  clientX: number,
  rectLeft: number,
  rectWidth: number,
  windowStart: number,
  windowEnd: number,
  effectiveDuration: number
) {
  if (!effectiveDuration) return 0;
  const ratio = clampRatio((clientX - rectLeft) / rectWidth);
  const windowSize = Math.max(0.001, windowEnd - windowStart);
  return (windowStart + ratio * windowSize) * effectiveDuration;
}

export function hoverTipX(clientX: number, rectLeft: number, rectWidth: number) {
  return Math.min(rectWidth - 44, Math.max(44, clientX - rectLeft));
}
