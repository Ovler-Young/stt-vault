import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FolderTree, UploadEntry } from "$lib/api/types";

const { fetchFolderTree, moveAsset, uploadAssetBatch } = vi.hoisted(() => ({
  fetchFolderTree: vi.fn(),
  moveAsset: vi.fn(),
  uploadAssetBatch: vi.fn(),
}));

vi.mock("$lib/api/endpoints", () => ({ fetchFolderTree, moveAsset }));
vi.mock("$lib/api/uploads", () => ({ uploadAssetBatch }));

import {
  getSingleUploadAssetId,
  loadHomeTree,
  uploadHomeFiles,
} from "./home-page.controller";

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

  it("uploads every selected file and moves only queued results into the selected folder", async () => {
    const entries = [
      { file: new File(["a"], "a.wav"), path: "a.wav" },
      { file: new File(["b"], "b.wav"), path: "b.wav" },
    ];
    uploadAssetBatch.mockResolvedValue({
      results: [
        { path: "a.wav", status: "queued", id: "asset-1" },
        { path: "b.wav", status: "failed", detail: "invalid" },
      ],
    });

    await expect(
      uploadHomeFiles({ entries, destination: "folder-1" }),
    ).resolves.toEqual({
      results: [
        { path: "a.wav", status: "queued", id: "asset-1" },
        { path: "b.wav", status: "failed", detail: "invalid" },
      ],
    });
    expect(moveAsset).toHaveBeenCalledExactlyOnceWith("asset-1", "folder-1");
    expect(uploadAssetBatch).toHaveBeenCalledExactlyOnceWith(
      entries,
      undefined,
    );
  });

  it("forwards current-file progress from a batch", async () => {
    const entries = [{ file: new File(["a"], "a.wav"), path: "a.wav" }];
    const onProgress = vi.fn();
    uploadAssetBatch.mockImplementation(async (_, reportProgress) => {
      reportProgress?.({ filename: "a.wav", uploaded: 1, total: 1 });
      return { results: [{ path: "a.wav", status: "queued", id: "asset-1" }] };
    });

    await uploadHomeFiles({ entries, destination: null, onProgress });

    expect(onProgress).toHaveBeenCalledExactlyOnceWith({
      filename: "a.wav",
      uploaded: 1,
      total: 1,
    });
  });

  it("opens only a queued ordinary single-file upload", () => {
    const queued = [
      { path: "a.wav", status: "queued" as const, id: "asset-1" },
    ];

    expect(getSingleUploadAssetId("files", queued)).toBe("asset-1");
    expect(getSingleUploadAssetId("directory", queued)).toBeNull();
    expect(
      getSingleUploadAssetId("files", [
        ...queued,
        { path: "b.wav", status: "queued" as const, id: "asset-2" },
      ]),
    ).toBeNull();
  });

  it("reports a destination move failure and continues moving queued assets", async () => {
    const entries = [
      { file: new File(["a"], "a.wav"), path: "a.wav" },
      { file: new File(["b"], "b.wav"), path: "b.wav" },
    ];
    uploadAssetBatch.mockResolvedValue({
      results: [
        { path: "a.wav", status: "queued", id: "asset-1" },
        { path: "b.wav", status: "queued", id: "asset-2" },
      ],
    });
    moveAsset.mockRejectedValueOnce(new Error("destination is unavailable"));

    await expect(
      uploadHomeFiles({ entries, destination: "folder-1" }),
    ).resolves.toEqual({
      results: [
        {
          path: "a.wav",
          status: "failed",
          id: "asset-1",
          detail:
            "Uploaded but could not move to the selected folder: destination is unavailable",
        },
        { path: "b.wav", status: "queued", id: "asset-2" },
      ],
    });
    expect(moveAsset).toHaveBeenNthCalledWith(1, "asset-1", "folder-1");
    expect(moveAsset).toHaveBeenNthCalledWith(2, "asset-2", "folder-1");
  });
});
