import { flushSync, mount, tick, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FolderTree } from "$lib/api/types";

const {
  createFolder,
  deleteAsset,
  deleteFolder,
  fetchConfig,
  fetchFolderTree,
  goto,
  moveAsset,
  moveFolder,
  renameFolder,
  uploadAssetBatch,
} = vi.hoisted(() => ({
  createFolder: vi.fn(),
  deleteAsset: vi.fn(),
  deleteFolder: vi.fn(),
  fetchConfig: vi.fn(),
  fetchFolderTree: vi.fn(),
  goto: vi.fn(),
  moveAsset: vi.fn(),
  moveFolder: vi.fn(),
  renameFolder: vi.fn(),
  uploadAssetBatch: vi.fn(),
}));

vi.mock("$app/navigation", () => ({ goto }));
vi.mock("$lib/api/endpoints", () => ({
  createFolder,
  deleteAsset,
  deleteFolder,
  fetchConfig,
  fetchFolderTree,
  moveAsset,
  moveFolder,
  renameFolder,
}));
vi.mock("$lib/api/uploads", () => ({ uploadAssetBatch }));

import HomePage from "./+page.svelte";

const tree: FolderTree = { folders: [], assets: [] };

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement | undefined;

afterEach(() => {
  if (component) unmount(component);
  target?.remove();
  component = undefined;
  target = undefined;
  vi.clearAllMocks();
});

function fileList(...files: File[]): FileList {
  return Object.assign([...files], {
    item: (index: number) => files[index] ?? null,
  });
}

async function mountPage() {
  fetchConfig.mockResolvedValue({ auth_required: false });
  fetchFolderTree.mockResolvedValue(tree);
  target = document.createElement("div");
  document.body.append(target);
  component = mount(HomePage, { target });
  await tick();
  await tick();
}

async function selectAndUpload(input: HTMLInputElement, files: FileList) {
  Object.defineProperty(input, "files", { configurable: true, value: files });
  flushSync(() => input.dispatchEvent(new Event("change", { bubbles: true })));
  const upload = Array.from(target!.querySelectorAll("button")).find(
    (button) => button.textContent?.trim() === `Upload ${files.length}`,
  );
  flushSync(() => upload?.click());
  await tick();
  await tick();
}

describe("home page upload route", () => {
  it("opens a queued ordinary single-file upload without a post-upload tree refresh", async () => {
    uploadAssetBatch.mockResolvedValue({
      results: [{ path: "recording.wav", status: "queued", id: "asset-1" }],
    });
    await mountPage();
    const picker = target!.querySelector<HTMLInputElement>(
      'input[type="file"]:not([webkitdirectory])',
    );
    expect(picker).toBeTruthy();

    await selectAndUpload(
      picker!,
      fileList(new File(["audio"], "recording.wav")),
    );

    expect(goto).toHaveBeenCalledExactlyOnceWith("/assets/asset-1");
    expect(fetchFolderTree).toHaveBeenCalledOnce();
  });

  it("keeps a one-file directory import on the home page and refreshes its tree", async () => {
    const file = new File(["audio"], "recording.wav");
    Object.defineProperty(file, "webkitRelativePath", {
      value: "meeting/recording.wav",
    });
    uploadAssetBatch.mockResolvedValue({
      results: [
        {
          path: "meeting/recording.wav",
          status: "queued",
          id: "asset-1",
        },
      ],
    });
    await mountPage();
    const picker = target!.querySelector<HTMLInputElement>(
      'input[type="file"][webkitdirectory]',
    );
    expect(picker).toBeTruthy();

    await selectAndUpload(picker!, fileList(file));

    expect(goto).not.toHaveBeenCalled();
    expect(fetchFolderTree).toHaveBeenCalledTimes(2);
    expect(uploadAssetBatch).toHaveBeenCalledWith(
      [{ file, path: "meeting/recording.wav" }],
      expect.any(Function),
    );
  });

  it("disables both selection inputs while a batch is uploading", async () => {
    let resolveBatch: (value: {
      results: { path: string; status: "queued"; id: string }[];
    }) => void = () => undefined;
    uploadAssetBatch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveBatch = resolve;
        }),
    );
    await mountPage();
    const ordinaryPicker = target!.querySelector<HTMLInputElement>(
      'input[type="file"]:not([webkitdirectory])',
    );
    const directoryPicker = target!.querySelector<HTMLInputElement>(
      'input[type="file"][webkitdirectory]',
    );
    expect(ordinaryPicker).toBeTruthy();
    expect(directoryPicker).toBeTruthy();

    await selectAndUpload(
      ordinaryPicker!,
      fileList(new File(["audio"], "recording.wav")),
    );

    expect(ordinaryPicker?.disabled).toBe(true);
    expect(directoryPicker?.disabled).toBe(true);

    resolveBatch({
      results: [{ path: "recording.wav", status: "queued", id: "asset-1" }],
    });
    await new Promise((resolve) => setTimeout(resolve));
    await tick();

    expect(ordinaryPicker?.disabled).toBe(false);
    expect(directoryPicker?.disabled).toBe(false);
  });
});
