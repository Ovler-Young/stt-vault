import { flushSync, mount, tick, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import AssetDownloadsFoldout from "./components/AssetDownloadsFoldout.svelte";
import { setStoredAccessToken } from "$lib/api/auth";

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement | undefined;

afterEach(() => {
  if (component) unmount(component);
  target?.remove();
  component = undefined;
  target = undefined;
  vi.unstubAllGlobals();
  setStoredAccessToken("");
  delete (document as Partial<Document>).execCommand;
});

function renderDownloads() {
  target = document.createElement("div");
  document.body.append(target);
  component = mount(AssetDownloadsFoldout, {
    target,
    props: { assetId: "asset-1", assetExports: { txt: "transcript.txt" } },
  });
  flushSync();
  return target.querySelector<HTMLButtonElement>(
    'button[aria-label="Copy txt download link"]',
  )!;
}

describe("download link copying", () => {
  it("copies the download URL from an explicit click and reports success", async () => {
    setStoredAccessToken("download-token");
    let copied = "";
    vi.stubGlobal("navigator", {
      clipboard: {
        writeText: async (text: string) => {
          copied = text;
        },
      },
    });
    const button = renderDownloads();
    const link = target!.querySelector("a")!;

    button.click();
    await tick();

    expect(copied).toBe(
      `${window.location.origin}/api/assets/asset-1/exports/txt?access_token=download-token`,
    );
    expect(link.hasAttribute("download")).toBe(true);
    await vi.waitFor(() =>
      expect(target!.textContent).toContain("Download link copied"),
    );
  });

  it("copies through the document fallback when clipboard access rejects", async () => {
    let copied = "";
    vi.stubGlobal("navigator", {
      clipboard: {
        writeText: async () => {
          throw new Error("Denied");
        },
      },
    });
    document.execCommand = () => {
      copied = (document.activeElement as HTMLTextAreaElement).value;
      return true;
    };
    const button = renderDownloads();

    button.click();
    await tick();

    expect(copied).toBe(
      `${window.location.origin}/api/assets/asset-1/exports/txt`,
    );
    await vi.waitFor(() =>
      expect(target!.textContent).toContain("Download link copied"),
    );
  });

  it("reports failure when clipboard access and the fallback are unavailable", async () => {
    vi.stubGlobal("navigator", {});
    document.execCommand = () => false;
    const button = renderDownloads();

    button.click();
    await tick();

    await vi.waitFor(() =>
      expect(target!.textContent).toContain(
        "Copy failed: Clipboard access is unavailable",
      ),
    );
  });
});
