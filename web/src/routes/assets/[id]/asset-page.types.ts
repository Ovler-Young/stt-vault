export type SpeakerEditor = {
  localSpeaker: string;
  displayName: string;
  x: number;
  y: number;
};

export type LocalSpeakerRow = {
  localSpeaker: string;
  displayName: string;
  speakerId?: string;
  similarity: number | null;
  count: number;
  firstStart: number | null;
  lastEnd: number | null;
};

export type SpeakerProgressBarHandle = {
  centerOnTime: (time: number) => void;
  zoomAtTime: (time: number, scale: number) => void;
  panByWindow: (delta: number) => void;
};

export type MaybePromise<T = void> = T | Promise<T>;
