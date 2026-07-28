import { describe, expect, it } from "vitest";

import type { AssetSummary, FolderNode, FolderTree } from "$lib/api/types";
import {
  assetsInTree,
  findFolderPath,
  flattenFolders,
  folderContains,
} from "./home-page.helpers";

const asset = (id: string): AssetSummary => ({
  id,
  filename: `${id}.wav`,
  media_type: "audio",
  duration: null,
  status: "queued",
  created_at: 0,
  updated_at: 0,
});

const folder = (
  id: string,
  children: FolderNode[] = [],
  assets: AssetSummary[] = [],
): FolderNode => ({
  id,
  name: id,
  parent_id: null,
  created_at: 0,
  updated_at: 0,
  children,
  assets,
});

const tree: FolderTree = {
  assets: [asset("root")],
  folders: [folder("parent", [folder("child", [], [asset("nested")])])],
};

describe("home page tree helpers", () => {
  it("flattens nested folders and gathers visible asset candidates", () => {
    expect(
      flattenFolders(tree.folders).map(({ folder, depth }) => [
        folder.id,
        depth,
      ]),
    ).toEqual([
      ["parent", 0],
      ["child", 1],
    ]);
    expect(assetsInTree(tree).map((asset) => asset.id)).toEqual([
      "root",
      "nested",
    ]);
  });

  it("finds paths and prevents moves into a folder descendant", () => {
    expect(findFolderPath(tree.folders, "child").map(({ id }) => id)).toEqual([
      "parent",
      "child",
    ]);
    expect(folderContains(tree.folders[0], "child")).toBe(true);
    expect(folderContains(tree.folders[0], "root")).toBe(false);
  });
});
