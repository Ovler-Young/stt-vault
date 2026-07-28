import { afterEach, describe, expect, it, vi } from "vitest";

import type { FolderTree } from "$lib/api/types";

import { createHomePolling } from "./home-page.polling";

const activeTree: FolderTree = {
  assets: [
    {
      id: "asset-1",
      filename: "clip.wav",
      media_type: "audio",
      duration: null,
      status: "processing",
      created_at: 0,
      updated_at: 0,
    },
  ],
  folders: [],
};

describe("home page polling controller", () => {
  afterEach(() => vi.useRealTimers());

  it("starts once for active work and stops after terminal refresh", () => {
    vi.useFakeTimers();
    const refresh = vi.fn().mockResolvedValue(undefined);
    const controller = createHomePolling({ refresh, intervalMs: 100 });

    controller.sync(activeTree);
    controller.sync(activeTree);
    vi.advanceTimersByTime(250);
    expect(refresh).toHaveBeenCalledTimes(2);

    controller.sync({ assets: [], folders: [] });
    vi.advanceTimersByTime(250);
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});
