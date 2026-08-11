import type { TimedTranscriptUnit, TranscriptSegment } from "$lib/api/types";
import { segmentMediaEnd, segmentMediaStart } from "./asset-page.helpers";

type PlaybackSegment = Pick<
  TranscriptSegment,
  "start" | "end" | "chunk_start" | "chunk_end" | "speaker"
>;

export function seekAndPlay(mediaElement: HTMLMediaElement, time: number) {
  mediaElement.currentTime = time;
  mediaElement.play().catch(() => {});
}

export function activeTimedTranscriptUnitIndex(
  units: Pick<TimedTranscriptUnit, "unit_index" | "start_ms" | "end_ms">[],
  currentTime: number,
  playbackEnded: boolean,
): number {
  if (playbackEnded) return -1;

  const currentMs = currentTime * 1000;
  const roundedCurrentMs = Math.floor(currentMs + 0.5);
  let activeIndex = -1;
  for (let index = 0; index < units.length; index += 1) {
    const unit = units[index];
    const isActive =
      unit.start_ms === unit.end_ms
        ? roundedCurrentMs === unit.start_ms
        : currentMs >= unit.start_ms && currentMs < unit.end_ms;
    if (
      isActive &&
      (activeIndex === -1 ||
        unit.start_ms > units[activeIndex].start_ms ||
        (unit.start_ms === units[activeIndex].start_ms &&
          unit.unit_index < units[activeIndex].unit_index))
    ) {
      activeIndex = index;
    }
  }
  return activeIndex;
}

export function boundedSeekTime(
  current: number,
  duration: number,
  delta: number,
): number {
  return Math.min(duration, Math.max(0, current + delta));
}

export function nextSegment(
  segments: PlaybackSegment[],
  currentTime: number,
): PlaybackSegment | undefined {
  return segments.find(
    (segment) => segmentMediaStart(segment) > currentTime + 0.05,
  );
}

export function previousSegment(
  segments: PlaybackSegment[],
  currentTime: number,
): PlaybackSegment | undefined {
  const current = segments.find(
    (segment) =>
      currentTime >= segmentMediaStart(segment) &&
      currentTime < segmentMediaEnd(segment),
  );
  if (current && currentTime - segmentMediaStart(current) > 5) return current;
  return [...segments]
    .reverse()
    .find((segment) => segmentMediaEnd(segment) < currentTime - 0.05);
}

export function adjacentSpeakerSegment(
  segments: PlaybackSegment[],
  currentTime: number,
  direction: "next" | "previous",
): PlaybackSegment | undefined {
  const current = segments.find(
    (segment) =>
      currentTime >= segmentMediaStart(segment) &&
      currentTime < segmentMediaEnd(segment),
  );
  if (!current) return undefined;
  const candidates =
    direction === "previous" ? [...segments].reverse() : segments;
  return candidates.find((segment) =>
    direction === "previous"
      ? segment.speaker === current.speaker &&
        segmentMediaEnd(segment) < currentTime - 0.05
      : segment.speaker === current.speaker &&
        segmentMediaStart(segment) > currentTime + 0.05,
  );
}
