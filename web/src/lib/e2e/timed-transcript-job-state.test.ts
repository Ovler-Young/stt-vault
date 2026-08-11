import { describe, expect, it } from "vitest";

import { classifyTimedTranscriptJobState } from "./timed-transcript-job-state";

describe("classifyTimedTranscriptJobState", () => {
  it("treats partial asset and job states as immediate failures", () => {
    expect(
      classifyTimedTranscriptJobState({
        status: "partial",
        job: { status: "partial" },
      }),
    ).toBe("failure");
  });

  it("keeps queued and processing states in progress", () => {
    expect(
      classifyTimedTranscriptJobState({
        status: "processing",
        job: { status: "queued" },
      }),
    ).toBe("in-progress");
  });

  it("requires both persisted states to succeed", () => {
    expect(
      classifyTimedTranscriptJobState({
        status: "success",
        job: { status: "success" },
      }),
    ).toBe("success");
  });

  it("fails closed for unknown or inconsistent states", () => {
    expect(
      classifyTimedTranscriptJobState({
        status: "success",
        job: { status: "processing" },
      }),
    ).toBe("unknown");
    expect(
      classifyTimedTranscriptJobState({ status: "queued", job: null }),
    ).toBe("unknown");
  });
});
