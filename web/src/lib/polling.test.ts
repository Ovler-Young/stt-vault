import { describe, expect, it } from "vitest";

import { hasActivePolling, needsActivePolling } from "./polling";

describe("active polling", () => {
  it("keeps polling for queued work, processing work, and active summaries", () => {
    expect(needsActivePolling({ status: "queued" })).toBe(true);
    expect(needsActivePolling({ status: "processing" })).toBe(true);
    expect(
      needsActivePolling({ status: "success", summary_status: "running" }),
    ).toBe(true);
  });

  it("stops polling when every record is terminal", () => {
    expect(
      hasActivePolling([
        { status: "success", summary_status: "success" },
        { status: "failed", summary_status: "failed" },
      ]),
    ).toBe(false);
  });
});
