import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AssetSummary, FolderNode } from "$lib/api/types";

const { createFolder, moveAsset } = vi.hoisted(() => ({
  createFolder: vi.fn(),
  moveAsset: vi.fn(),
}));

vi.mock("$lib/api/endpoints", () => ({
  createFolder,
  deleteAsset: vi.fn(),
  deleteFolder: vi.fn(),
  moveAsset,
  moveFolder: vi.fn(),
  renameFolder: vi.fn(),
}));

import { createHomeFileActions } from "./home-page.file-actions";

const folder: FolderNode = {
  id: "folder-1",
  name: "Inbox",
  parent_id: null,
  created_at: 0,
  updated_at: 0,
  children: [],
  assets: [],
};

const asset: AssetSummary = {
  id: "asset-1",
  filename: "clip.wav",
  media_type: "audio",
  duration: null,
  status: "success",
  parent_folder_id: null,
  created_at: 0,
  updated_at: 0,
};

describe("home page file actions", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("prompt", vi.fn().mockReturnValue("New folder"));
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  });

  it("owns folder creation sequencing and reload", async () => {
    createFolder.mockResolvedValue({ ...folder, id: "new-folder" });
    const loadTree = vi.fn().mockResolvedValue(undefined);
    const selectFolder = vi.fn();
    const actions = createHomeFileActions({
      currentFolder: null,
      selectedFolderId: null,
      folderMoveTarget: "",
      assetTargets: {},
      loadTree,
      selectFolder,
      setBusy: vi.fn(),
      setError: vi.fn(),
      reportError: vi.fn(),
    });

    await actions.addFolder();

    expect(createFolder).toHaveBeenCalledWith("New folder", null);
    expect(loadTree).toHaveBeenCalledOnce();
    expect(selectFolder).toHaveBeenCalledWith("new-folder");
  });

  it("moves an asset using the selected target", async () => {
    const loadTree = vi.fn().mockResolvedValue(undefined);
    const actions = createHomeFileActions({
      currentFolder: null,
      selectedFolderId: null,
      folderMoveTarget: "",
      assetTargets: { "asset-1": "folder-2" },
      loadTree,
      selectFolder: vi.fn(),
      setBusy: vi.fn(),
      setError: vi.fn(),
      reportError: vi.fn(),
    });

    await actions.moveAsset(asset);

    expect(moveAsset).toHaveBeenCalledWith("asset-1", "folder-2");
    expect(loadTree).toHaveBeenCalledOnce();
  });
});
