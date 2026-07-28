import {
  createFolder,
  deleteAsset,
  deleteFolder,
  moveAsset,
  moveFolder,
  renameFolder,
} from "$lib/api/endpoints";
import type { AssetSummary, FolderNode } from "$lib/api/types";

type HomeFileActionDependencies = {
  get currentFolder(): FolderNode | null;
  get selectedFolderId(): string | null;
  get folderMoveTarget(): string;
  get assetTargets(): Record<string, string>;
  loadTree: () => Promise<void>;
  selectFolder: (folderId: string | null) => void;
  setBusy: (busy: boolean) => void;
  setError: (error: string) => void;
  reportError: (requestError: unknown) => void;
};

export function createHomeFileActions(deps: HomeFileActionDependencies) {
  async function run(operation: () => Promise<void>) {
    deps.setBusy(true);
    deps.setError("");
    try {
      await operation();
    } catch (requestError) {
      deps.reportError(requestError);
    } finally {
      deps.setBusy(false);
    }
  }

  return {
    async addFolder() {
      const name = prompt("Folder name")?.trim();
      if (!name) return;
      await run(async () => {
        const folder = await createFolder(name, deps.selectedFolderId);
        await deps.loadTree();
        deps.selectFolder(folder.id);
      });
    },
    async renameFolder() {
      const folder = deps.currentFolder;
      if (!folder) return;
      const name = prompt("Folder name", folder.name)?.trim();
      if (!name || name === folder.name) return;
      await run(async () => {
        await renameFolder(folder.id, name);
        await deps.loadTree();
      });
    },
    async deleteFolder() {
      const folder = deps.currentFolder;
      if (!folder || !confirm(`Delete empty folder ${folder.name}?`)) return;
      await run(async () => {
        await deleteFolder(folder.id);
        deps.selectFolder(folder.parent_id);
        await deps.loadTree();
      });
    },
    async moveFolder() {
      const folder = deps.currentFolder;
      if (!folder) return;
      await run(async () => {
        await moveFolder(folder.id, deps.folderMoveTarget || null);
        await deps.loadTree();
      });
    },
    async moveAsset(asset: AssetSummary) {
      const targetId =
        deps.assetTargets[asset.id] ?? asset.parent_folder_id ?? "";
      await run(async () => {
        await moveAsset(asset.id, targetId || null);
        await deps.loadTree();
      });
    },
    async deleteAsset(asset: AssetSummary) {
      if (!confirm(`Delete ${asset.title || asset.filename}?`)) return;
      await run(async () => {
        await deleteAsset(asset.id);
        await deps.loadTree();
      });
    },
  };
}
