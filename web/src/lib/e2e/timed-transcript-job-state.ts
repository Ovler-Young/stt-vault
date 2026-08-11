export type TimedTranscriptJobState =
  "failure" | "in-progress" | "success" | "unknown";

type PersistedJobState = {
  status: string;
};

export type TimedTranscriptAssetState = {
  job: PersistedJobState | null;
  status: string;
};

const inProgressStates = new Set(["queued", "processing"]);
const failureStates = new Set(["partial", "failed"]);

export function classifyTimedTranscriptJobState(
  detail: TimedTranscriptAssetState,
): TimedTranscriptJobState {
  if (detail.job === null) return "unknown";
  const states = [detail.status, detail.job.status];
  if (states.some((state) => failureStates.has(state))) return "failure";
  if (states.every((state) => state === "success")) return "success";
  if (states.every((state) => inProgressStates.has(state)))
    return "in-progress";
  return "unknown";
}
