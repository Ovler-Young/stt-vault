import type {
  AssetSummary,
  BatchUploadResult,
  FolderNode,
  UploadProgress,
} from "$lib/api/types";

import type { FlatFolder } from "./home-page.helpers";

export type HomePageShellProps = {
  allAssetCount: number;
  folderCount: number;
  authRequired: boolean;
  authenticated: boolean;
  busy: boolean;
  adminPassword: string;
  selectedFolderId: string | null;
  flatFolders: FlatFolder[];
  breadcrumbs: FolderNode[];
  visibleAssets: AssetSummary[];
  currentFolder: FolderNode | null;
  folderMoveOptions: FlatFolder[];
  folderMoveTarget: string;
  uploadFile: File | null;
  uploadEntryCount: number;
  uploadProgress: UploadProgress | null;
  error: string;
  batchResults: BatchUploadResult[];
  assetTargets: Record<string, string>;
  onRefresh: () => void;
  onSignOut: () => void;
  onAdminPasswordChange: (value: string) => void;
  onLogin: () => void;
  onSelectFolder: (folderId: string | null) => void;
  onAddFolder: () => void;
  onFileChange: (file: File | null) => void;
  onDirectoryChange: (files: FileList | null) => void;
  onUpload: () => void;
  onRenameFolder: () => void;
  onFolderMoveTargetChange: (targetId: string) => void;
  onMoveFolder: () => void;
  onDeleteFolder: () => void;
  onAssetTargetChange: (assetId: string, targetId: string) => void;
  onMoveAsset: (asset: AssetSummary) => void;
  onDeleteAsset: (asset: AssetSummary) => void;
};

export type HomeWorkspaceProps = Pick<
  HomePageShellProps,
  | "selectedFolderId"
  | "flatFolders"
  | "breadcrumbs"
  | "visibleAssets"
  | "currentFolder"
  | "folderMoveOptions"
  | "folderMoveTarget"
  | "uploadFile"
  | "uploadEntryCount"
  | "uploadProgress"
  | "error"
  | "batchResults"
  | "assetTargets"
  | "busy"
  | "onSelectFolder"
  | "onAddFolder"
  | "onFileChange"
  | "onDirectoryChange"
  | "onUpload"
  | "onRenameFolder"
  | "onFolderMoveTargetChange"
  | "onMoveFolder"
  | "onDeleteFolder"
  | "onAssetTargetChange"
  | "onMoveAsset"
  | "onDeleteAsset"
>;
