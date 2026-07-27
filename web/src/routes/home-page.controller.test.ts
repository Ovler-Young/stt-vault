import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FolderTree, UploadEntry } from "$lib/api-types";

const { fetchFolderTree, moveAsset, uploadAsset, uploadAssetBatch } =
  vi.hoisted(() => ({
    fetchFolderTree: vi.fn(),
    moveAsset: vi.fn(),
    uploadAsset: vi.fn(),
    uploadAssetBatch: vi.fn(),
  }));

vi.mock("$lib/api-endpoints", () => ({ fetchFolderTree, moveAsset }));
vi.mock("$lib/api/uploads", () => ({ uploadAsset, uploadAssetBatch }));

import { loadHomeTree, uploadHomeFiles } from "./home-page.controller";

const tree: FolderTree = { folders: [], assets: [] };

describe("home page controller", () => {
  beforeEach(() => vi.resetAllMocks());

  it("does not fetch protected folders until authentication succeeds", async () => {
    await expect(loadHomeTree(true, false, "selected")).resolves.toEqual({
      tree,
      selectedFolderId: "selected",
    });
    expect(fetchFolderTree).not.toHaveBeenCalled();
  });

  it("clears a folder selection removed from the refreshed tree", async () => {
    fetchFolderTree.mockResolvedValue(tree);

    await expect(loadHomeTree(false, true, "missing")).resolves.toEqual({
      tree,
      selectedFolderId: null,
    });
  });

  it("moves only queued batch uploads into the selected folder", async () => {
    const entries = [{ file: new File(["a"], "a.wav"), path: "a.wav" }];
    uploadAssetBatch.mockResolvedValue({
      results: [
        { path: "a.wav", status: "queued", id: "asset-1" },
        { path: "b.wav", status: "failed", detail: "invalid" },
      ],
    });

    await expect(
      uploadHomeFiles({ entries, destination: "folder-1" }),
    ).resolves.toEqual({
      kind: "batch",
      results: [
        { path: "a.wav", status: "queued", id: "asset-1" },
        { path: "b.wav", status: "failed", detail: "invalid" },
      ],
    });
    expect(moveAsset).toHaveBeenCalledExactlyOnceWith("asset-1", "folder-1");
  });

  it("uploads one file and moves it before returning its asset id", async () => {
    const file = new File(["a"], "recording.wav");
    uploadAsset.mockResolvedValue({ id: "asset-2" });

    await expect(
      uploadHomeFiles({ file, entries: [], destination: "folder-1" }),
    ).resolves.toEqual({ kind: "asset", assetId: "asset-2" });
    expect(uploadAsset).toHaveBeenCalledWith(file, "recording.wav", undefined);
    expect(moveAsset).toHaveBeenCalledExactlyOnceWith("asset-2", "folder-1");
  });
});
