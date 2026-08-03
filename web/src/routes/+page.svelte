<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { goto } from "$app/navigation";
  import {
    type AssetSummary,
    type BatchUploadResult,
    type FolderTree,
    type UploadEntry,
    type UploadProgress,
  } from "$lib/api/types";
  import { fetchConfig } from "$lib/api/endpoints";
  import HomePageShell from "./components/HomePageShell.svelte";
  import {
    createHomeAuthController,
    emptyHomeTree,
    type HomeAuthState,
  } from "./home-page.auth";
  import { loadHomeTree, uploadHomeFiles } from "./home-page.controller";
  import { createHomeFileActions } from "./home-page.file-actions";
  import {
    assetsInTree,
    findFolder,
    findFolderPath,
    flattenFolders,
    folderContains,
  } from "./home-page.helpers";
  import { createHomePolling } from "./home-page.polling";

  let tree: FolderTree = emptyHomeTree();
  let selectedFolderId: string | null = null;
  let uploadFile: File | null = null;
  let uploadEntries: UploadEntry[] = [];
  let batchResults: BatchUploadResult[] = [];
  let uploadProgress: UploadProgress | null = null;
  let assetTargets: Record<string, string> = {};
  let folderMoveTarget = "";
  let busy = false;
  let error = "";
  let authRequired = false;
  let authState: HomeAuthState = {
    adminPassword: "",
    authenticated: false,
    authenticationPending: false,
    error: "",
  };

  const authController = createHomeAuthController({
    onChange: (state) => (authState = state),
    onSessionExpired: () => {
      tree = emptyHomeTree();
      polling.stop();
    },
  });

  $: flatFolders = flattenFolders(tree.folders);
  $: currentFolder = selectedFolderId
    ? findFolder(tree.folders, selectedFolderId)
    : null;
  $: visibleAssets = currentFolder?.assets ?? tree.assets;
  $: breadcrumbs = selectedFolderId
    ? findFolderPath(tree.folders, selectedFolderId)
    : [];
  $: allAssets = assetsInTree(tree);
  $: folderMoveOptions = currentFolder
    ? flatFolders.filter(
        ({ folder }) => !folderContains(currentFolder!, folder.id),
      )
    : flatFolders;

  onMount(async () => {
    const config = await fetchConfig();
    authRequired = config.auth_required;
    if (authRequired) await authController.restoreSession();
    await loadTree();
  });

  onDestroy(() => {
    polling.stop();
  });

  async function loadTree() {
    try {
      const result = await loadHomeTree(
        authRequired,
        authState.authenticated,
        selectedFolderId,
      );
      tree = result.tree;
      selectedFolderId = result.selectedFolderId;
      polling.sync(tree);
      error = "";
    } catch (requestError) {
      authController.handleRequestError(requestError);
    }
  }

  async function submitUpload() {
    if (!uploadFile && uploadEntries.length === 0) return;
    busy = true;
    batchResults = [];
    uploadProgress = null;
    const destination = selectedFolderId;
    try {
      const result = await uploadHomeFiles({
        file: uploadFile,
        entries: uploadEntries,
        destination,
        onProgress: (progress) => (uploadProgress = progress),
      });
      if (result.kind === "batch") {
        batchResults = result.results;
        uploadEntries = [];
        uploadFile = null;
        await loadTree();
        return;
      }
      uploadFile = null;
      await goto(`/assets/${result.assetId}`);
    } catch (requestError) {
      authController.handleRequestError(requestError);
    } finally {
      busy = false;
      uploadProgress = null;
    }
  }

  function selectDirectory(files: FileList | null) {
    uploadEntries = Array.from(files ?? []).map((file) => ({
      file,
      path: file.webkitRelativePath || file.name,
    }));
    uploadFile = null;
  }

  function setUploadFile(file: File | null) {
    uploadFile = file;
  }

  function setAssetTarget(assetId: string, targetId: string) {
    assetTargets = { ...assetTargets, [assetId]: targetId };
  }

  async function submitLogin() {
    busy = true;
    try {
      if (await authController.signIn()) await loadTree();
    } finally {
      busy = false;
    }
  }

  function selectFolder(folderId: string | null) {
    selectedFolderId = folderId;
    const folder = folderId ? findFolder(tree.folders, folderId) : null;
    folderMoveTarget = folder?.parent_id ?? "";
  }

  const polling = createHomePolling({ refresh: loadTree });
  const fileActions = createHomeFileActions({
    get currentFolder() {
      return currentFolder;
    },
    get selectedFolderId() {
      return selectedFolderId;
    },
    get folderMoveTarget() {
      return folderMoveTarget;
    },
    get assetTargets() {
      return assetTargets;
    },
    loadTree,
    selectFolder,
    setBusy: (value) => (busy = value),
    setError: (value) => (error = value),
    reportError: authController.handleRequestError,
  });
</script>

<HomePageShell
  allAssetCount={allAssets.length}
  folderCount={flatFolders.length}
  {authRequired}
  authenticated={authState.authenticated}
  authPending={authState.authenticationPending}
  {busy}
  adminPassword={authState.adminPassword}
  {selectedFolderId}
  {flatFolders}
  {breadcrumbs}
  {visibleAssets}
  {currentFolder}
  {folderMoveOptions}
  {folderMoveTarget}
  {uploadFile}
  uploadEntryCount={uploadEntries.length}
  {uploadProgress}
  error={authState.error || error}
  {batchResults}
  {assetTargets}
  onRefresh={loadTree}
  onSignOut={authController.signOut}
  onAdminPasswordChange={authController.setPassword}
  onLogin={submitLogin}
  onSelectFolder={selectFolder}
  onAddFolder={fileActions.addFolder}
  onFileChange={setUploadFile}
  onDirectoryChange={selectDirectory}
  onUpload={submitUpload}
  onRenameFolder={fileActions.renameFolder}
  onFolderMoveTargetChange={(value) => (folderMoveTarget = value)}
  onMoveFolder={fileActions.moveFolder}
  onDeleteFolder={fileActions.deleteFolder}
  onAssetTargetChange={setAssetTarget}
  onMoveAsset={fileActions.moveAsset}
  onDeleteAsset={fileActions.deleteAsset}
/>
