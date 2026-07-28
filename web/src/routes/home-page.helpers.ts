import type { AssetSummary, FolderNode, FolderTree } from "$lib/api/types";

export type FlatFolder = { folder: FolderNode; depth: number };

export function flattenFolders(folders: FolderNode[], depth = 0): FlatFolder[] {
  return folders.flatMap((folder) => [
    { folder, depth },
    ...flattenFolders(folder.children, depth + 1),
  ]);
}

export function assetsInTree(tree: FolderTree): AssetSummary[] {
  return [
    ...tree.assets,
    ...flattenFolders(tree.folders).flatMap(({ folder }) => folder.assets),
  ];
}

export function folderContains(
  folder: FolderNode,
  candidateId: string,
): boolean {
  return (
    folder.id === candidateId ||
    folder.children.some((child) => folderContains(child, candidateId))
  );
}

export function findFolder(
  folders: FolderNode[],
  id: string,
): FolderNode | null {
  for (const folder of folders) {
    if (folder.id === id) return folder;
    const child = findFolder(folder.children, id);
    if (child) return child;
  }
  return null;
}

export function findFolderPath(
  folders: FolderNode[],
  id: string,
  path: FolderNode[] = [],
): FolderNode[] {
  for (const folder of folders) {
    const nextPath = [...path, folder];
    if (folder.id === id) return nextPath;
    const childPath = findFolderPath(folder.children, id, nextPath);
    if (childPath.length) return childPath;
  }
  return [];
}
