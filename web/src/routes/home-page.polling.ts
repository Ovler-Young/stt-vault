import { hasActivePolling } from "$lib/state/polling";
import type { FolderTree } from "$lib/api/types";

import { assetsInTree } from "./home-page.helpers";

type HomePollingOptions = {
  refresh: () => Promise<void>;
  intervalMs?: number;
};

export function createHomePolling({
  refresh,
  intervalMs = 3000,
}: HomePollingOptions) {
  let timer: ReturnType<typeof setInterval> | null = null;

  function sync(tree: FolderTree) {
    const shouldPoll = hasActivePolling(assetsInTree(tree));
    if (shouldPoll && !timer) timer = setInterval(refresh, intervalMs);
    else if (!shouldPoll && timer) stop();
  }

  function stop() {
    if (!timer) return;
    clearInterval(timer);
    timer = null;
  }

  return { sync, stop };
}
