type PollableRecord = {
  status: string;
  summary_status?: string | null;
};

export function needsActivePolling(record: PollableRecord): boolean {
  return (
    record.status === "queued" ||
    record.status === "processing" ||
    record.summary_status === "running"
  );
}

export function hasActivePolling(records: Iterable<PollableRecord>): boolean {
  return Array.from(records).some(needsActivePolling);
}
