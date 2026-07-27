import type { TranscriptSegment } from "$lib/api-types";
import {
  clampRatio,
  clampTimelineWindow,
  panTimelineWindow,
  segmentAt,
  zoomWindowAround,
  type TimelineHover,
  type TimelineRow,
  type TimelineWindow,
} from "$lib/speakerTimeline";

export function seekTimeForTimelineEvent(
  segments: TranscriptSegment[],
  time: number,
) {
  return segmentAt(segments, time)?.start ?? time;
}

export function doubleClickTimelineWindow(
  segments: TranscriptSegment[],
  time: number,
  duration: number,
  currentWindow: TimelineWindow,
  minZoomSize: number,
) {
  const segment = segmentAt(segments, time);
  const fullStart = segment ? segment.start / duration : time / duration;
  const fullEnd = segment ? segment.end / duration : fullStart;
  const center = (fullStart + fullEnd) / 2;
  const segmentWidth = Math.max(fullEnd - fullStart, 0.015);
  const nextWidth = Math.max(
    0.02,
    Math.min(
      0.35,
      segmentWidth * 4,
      (currentWindow.end - currentWindow.start) * 0.55,
    ),
  );
  return clampTimelineWindow(
    center - nextWidth / 2,
    center + nextWidth / 2,
    minZoomSize,
  );
}

export function wheelTimelineWindow(
  currentWindow: TimelineWindow,
  center: number,
  deltaX: number,
  deltaY: number,
  isZoomed: boolean,
  minZoomSize: number,
  zoomSensitivity: number,
  panSensitivity: number,
) {
  if (Math.abs(deltaX) > Math.abs(deltaY) && isZoomed) {
    return panTimelineWindow(
      currentWindow,
      deltaX * panSensitivity * (currentWindow.end - currentWindow.start),
      minZoomSize,
    );
  }
  return zoomWindowAround(
    currentWindow,
    center,
    1 + deltaY * zoomSensitivity,
    minZoomSize,
  );
}

export function keyboardTimelineWindow(
  code: string,
  currentTime: number,
  duration: number,
  currentWindow: TimelineWindow,
  minZoomSize: number,
  zoomStep: number,
  panStep: number,
) {
  const currentRatio = clampRatio(currentTime / duration);
  if (code === "KeyW") {
    return zoomWindowAround(
      currentWindow,
      currentRatio,
      1 - zoomStep,
      minZoomSize,
    );
  }
  if (code === "KeyS") {
    return zoomWindowAround(
      currentWindow,
      currentRatio,
      1 + zoomStep,
      minZoomSize,
    );
  }
  if (code === "KeyA") {
    return panTimelineWindow(
      currentWindow,
      -panStep * (currentWindow.end - currentWindow.start),
      minZoomSize,
    );
  }
  if (code === "KeyD") {
    return panTimelineWindow(
      currentWindow,
      panStep * (currentWindow.end - currentWindow.start),
      minZoomSize,
    );
  }
  if (code === "Escape") return { start: 0, end: 1 };
  return null;
}

export function draggedTimelineWindow(
  dragStartWindow: TimelineWindow,
  dragDistance: number,
  containerWidth: number,
  minZoomSize: number,
) {
  const size = dragStartWindow.end - dragStartWindow.start;
  const delta = -(dragDistance / Math.max(1, containerWidth)) * size;
  return clampTimelineWindow(
    dragStartWindow.start + delta,
    dragStartWindow.end + delta,
    minZoomSize,
  );
}

export function timelineHover(
  row: TimelineRow,
  x: number,
  time: number,
  segments: TranscriptSegment[],
): TimelineHover {
  const segment = segmentAt(segments, time);
  return {
    row,
    x,
    time,
    speaker: segment?.speaker_name ?? segment?.speaker ?? null,
  };
}
