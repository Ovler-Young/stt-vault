import type {
  BatchUploadResult,
  FolderTree,
  UploadEntry,
  UploadProgress,
} from "$lib/api/types";
import { fetchFolderTree, moveAsset } from "$lib/api/endpoints";
import { uploadAssetBatch } from "$lib/api/uploads";

import { findFolder } from "./home-page.helpers";

type HomeTreeLoadResult = {
  tree: FolderTree;
  selectedFolderId: string | null;
};

type HomeUploadInput = {
  entries: UploadEntry[];
  destination: string | null;
  onProgress?: (progress: UploadProgress) => void;
};

type HomeUploadResult = { results: BatchUploadResult[] };

export type UploadSelectionSource = "files" | "directory";

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
  const { destination, entries, onProgress } = input;
  const { results } = await uploadAssetBatch(entries, onProgress);
  const movedResults: BatchUploadResult[] = [];
  for (const result of results) {
    if (result.status === "queued" && result.id && destination) {
      try {
        await moveAsset(result.id, destination);
      } catch (error) {
        movedResults.push({
          ...result,
          status: "failed",
          detail: `Uploaded but could not move to the selected folder: ${error instanceof Error ? error.message : String(error)}`,
        });
        continue;
      }
    }
    movedResults.push(result);
  }
  return { results: movedResults };
}

export function getSingleUploadAssetId(
  source: UploadSelectionSource,
  results: BatchUploadResult[],
): string | null {
  const [result] = results;
  return source === "files" &&
    results.length === 1 &&
    result?.status === "queued"
    ? (result.id ?? null)
    : null;
}
