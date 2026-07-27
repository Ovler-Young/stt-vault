import { afterEach, describe, expect, it, vi } from "vitest";

import { uploadAsset } from "./uploads";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("uploadAsset", () => {
  it("creates, transfers, and completes a session outside endpoint wrappers", async () => {
    const requests: Array<{ path: string; method: string }> = [];
    const responses = [
      { id: "upload-1", filename: "recording.wav", size: 3, offset: 0 },
      { id: "upload-1", filename: "recording.wav", size: 3, offset: 3 },
      { id: "asset-1", status: "queued" },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init: RequestInit) => {
        requests.push({ path, method: init.method ?? "GET" });
        return new Response(JSON.stringify(responses.shift()), { status: 200 });
      }),
    );

    await expect(
      uploadAsset(new File(["wav"], "recording.wav")),
    ).resolves.toEqual({
      id: "asset-1",
      status: "queued",
    });

    expect(requests).toEqual([
      { path: "/api/uploads", method: "POST" },
      { path: "/api/uploads/upload-1", method: "PUT" },
      { path: "/api/uploads/upload-1/complete", method: "POST" },
    ]);
  });
});
