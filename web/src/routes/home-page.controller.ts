import type {
  BatchUploadResult,
  FolderTree,
  UploadEntry,
  UploadProgress,
} from "$lib/api-types";
import { fetchFolderTree, moveAsset } from "$lib/api-endpoints";
import { uploadAsset, uploadAssetBatch } from "$lib/api/uploads";

import { findFolder } from "./home-page.helpers";

type HomeTreeLoadResult = {
  tree: FolderTree;
  selectedFolderId: string | null;
};

type HomeUploadInput = {
  file?: File | null;
  entries: UploadEntry[];
  destination: string | null;
  onProgress?: (progress: UploadProgress) => void;
};

type HomeUploadResult =
  | { kind: "asset"; assetId: string }
  | { kind: "batch"; results: BatchUploadResult[] };

export async function loadHomeTree(
  authRequired: boolean,
  authenticated: boolean,
  selectedFolderId: string | null,
): Promise<HomeTreeLoadResult> {
  if (authRequired && !authenticated) {
    return { tree: { folders: [], assets: [] }, selectedFolderId };
  }

  const tree = await fetchFolderTree();
  return {
    tree,
    selectedFolderId:
      selectedFolderId && !findFolder(tree.folders, selectedFolderId)
        ? null
        : selectedFolderId,
  };
}

export async function uploadHomeFiles(
  input: HomeUploadInput,
): Promise<HomeUploadResult> {
  const { destination, entries, file, onProgress } = input;
  if (entries.length > 0) {
    const { results } = await uploadAssetBatch(entries, onProgress);
    for (const result of results) {
      if (result.status === "queued" && result.id && destination) {
        await moveAsset(result.id, destination);
      }
    }
    return { kind: "batch", results };
  }

  if (!file) throw new Error("An upload file is required.");
  const result = await uploadAsset(file, file.name, onProgress);
  if (destination) await moveAsset(result.id, destination);
  return { kind: "asset", assetId: result.id };
}
