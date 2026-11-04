export type AssetKeyboardActions = {
  togglePlay: () => void;
  seekRelative: (delta: number) => void;
  seekPreviousSegment: () => void;
  seekNextSegment: () => void;
  seekPreviousSpeakerSegment: () => void;
  seekNextSpeakerSegment: () => void;
  seekToStart: () => void;
  centerTimeline: () => void;
  zoomTimeline: (factor: number) => void;
  panTimeline: (delta: number) => void;
};

export function handleAssetKeydown(
  event: KeyboardEvent,
  mediaElement: HTMLMediaElement | null,
  actions: AssetKeyboardActions,
): void {
  const target = event.target instanceof HTMLElement ? event.target : null;
  if (shouldIgnorePlaybackKey(target)) return;

  if (event.code === "Space") {
    event.preventDefault();
    actions.togglePlay();
  } else if (event.code === "ArrowRight") {
    event.preventDefault();
    actions.seekRelative(5);
  } else if (event.code === "ArrowLeft") {
    event.preventDefault();
    actions.seekRelative(-5);
  } else if (event.code === "Comma") {
    event.preventDefault();
    actions.seekPreviousSegment();
  } else if (event.code === "Period") {
    event.preventDefault();
    actions.seekNextSegment();
  } else if (event.code === "BracketLeft") {
    event.preventDefault();
    actions.seekPreviousSpeakerSegment();
  } else if (event.code === "BracketRight") {
    event.preventDefault();
    actions.seekNextSpeakerSegment();
  } else if (event.code === "KeyK") {
    event.preventDefault();
    actions.seekToStart();
  } else if (event.code === "KeyM" && mediaElement) {
    event.preventDefault();
    mediaElement.muted = !mediaElement.muted;
  } else if (event.code === "KeyV") {
    event.preventDefault();
    actions.centerTimeline();
  } else if (event.code === "KeyW") {
    event.preventDefault();
    actions.zoomTimeline(0.88);
  } else if (event.code === "KeyS") {
    event.preventDefault();
    actions.zoomTimeline(1.12);
  } else if (event.code === "KeyA") {
    event.preventDefault();
    actions.panTimeline(-0.12);
  } else if (event.code === "KeyD") {
    event.preventDefault();
    actions.panTimeline(0.12);
  }
}

function shouldIgnorePlaybackKey(target: HTMLElement | null): boolean {
  if (!target) return false;
  const tagName = target.tagName;
  if (
    target.isContentEditable ||
    tagName === "INPUT" ||
    tagName === "TEXTAREA" ||
    tagName === "SELECT"
  ) {
    return true;
  }
  if (tagName === "BUTTON" && !target.closest(".transcript")) return true;
  return tagName === "A" || tagName === "SUMMARY";
}
