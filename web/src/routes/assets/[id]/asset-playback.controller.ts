type PlaybackSegment = {
  start: number;
  end: number;
  chunk_start?: number;
  chunk_end?: number;
  speaker: string;
};

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
  return segments.find((segment) => mediaStart(segment) > currentTime + 0.05);
}

export function previousSegment(
  segments: PlaybackSegment[],
  currentTime: number,
): PlaybackSegment | undefined {
  const current = segments.find(
    (segment) =>
      currentTime >= mediaStart(segment) && currentTime < mediaEnd(segment),
  );
  if (current && currentTime - mediaStart(current) > 5) return current;
  return [...segments]
    .reverse()
    .find((segment) => mediaEnd(segment) < currentTime - 0.05);
}

export function adjacentSpeakerSegment(
  segments: PlaybackSegment[],
  currentTime: number,
  direction: "next" | "previous",
): PlaybackSegment | undefined {
  const current = segments.find(
    (segment) =>
      currentTime >= mediaStart(segment) && currentTime < mediaEnd(segment),
  );
  if (!current) return undefined;
  const candidates =
    direction === "previous" ? [...segments].reverse() : segments;
  return candidates.find((segment) =>
    direction === "previous"
      ? segment.speaker === current.speaker &&
        mediaEnd(segment) < currentTime - 0.05
      : segment.speaker === current.speaker &&
        mediaStart(segment) > currentTime + 0.05,
  );
}

function mediaStart(segment: PlaybackSegment): number {
  return segment.chunk_start ?? segment.start;
}

function mediaEnd(segment: PlaybackSegment): number {
  return segment.chunk_end ?? segment.end;
}
