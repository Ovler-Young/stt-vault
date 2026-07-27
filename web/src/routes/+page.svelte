<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { goto } from "$app/navigation";
  import {
    type AssetSummary,
    type BatchUploadResult,
    type FolderNode,
    type FolderTree,
    type UploadEntry,
    type UploadProgress,
  } from "$lib/api-types";
  import { ApiError } from "$lib/api-transport";
  import {
    getStoredAccessToken,
    login,
    setStoredAccessToken,
  } from "$lib/api-auth";
  import {
    createFolder,
    deleteAsset,
    deleteFolder,
    fetchConfig,
    moveAsset,
    moveFolder,
    renameFolder,
  } from "$lib/api-endpoints";
  import { hasActivePolling } from "$lib/polling";
  import HomeAssetList from "./components/HomeAssetList.svelte";
  import FolderSidebar from "./components/FolderSidebar.svelte";
  import { loadHomeTree, uploadHomeFiles } from "./home-page.controller";
  import {
    assetsInTree,
    findFolder,
    findFolderPath,
    flattenFolders,
    folderContains,
  } from "./home-page.helpers";

  let tree: FolderTree = { folders: [], assets: [] };
  let selectedFolderId: string | null = null;
  let uploadFile: File | null = null;
  let uploadEntries: UploadEntry[] = [];
  let batchResults: BatchUploadResult[] = [];
  let uploadProgress: UploadProgress | null = null;
  let assetTargets: Record<string, string> = {};
  let folderMoveTarget = "";
  let busy = false;
  let error = "";
  let adminPassword = "";
  let authRequired = false;
  let authenticated = false;
  let poll: ReturnType<typeof setInterval> | null = null;

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
    authenticated = Boolean(getStoredAccessToken());
    await loadTree();
  });

  onDestroy(() => {
    if (poll) clearInterval(poll);
  });

  async function loadTree() {
    try {
      const result = await loadHomeTree(
        authRequired,
        authenticated,
        selectedFolderId,
      );
      tree = result.tree;
      selectedFolderId = result.selectedFolderId;
      updatePolling();
      error = "";
    } catch (requestError) {
      reportRequestError(requestError);
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
      reportRequestError(requestError);
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

  function setAssetTarget(assetId: string, targetId: string) {
    assetTargets = { ...assetTargets, [assetId]: targetId };
  }

  async function addFolder() {
    const name = prompt("Folder name")?.trim();
    if (!name) return;
    await runFileOperation(async () => {
      const folder = await createFolder(name, selectedFolderId);
      await loadTree();
      selectFolder(folder.id);
    });
  }

  async function editCurrentFolder() {
    if (!currentFolder) return;
    const name = prompt("Folder name", currentFolder.name)?.trim();
    if (!name || name === currentFolder.name) return;
    await runFileOperation(async () => {
      await renameFolder(currentFolder!.id, name);
      await loadTree();
    });
  }

  async function removeCurrentFolder() {
    if (
      !currentFolder ||
      !confirm(`Delete empty folder ${currentFolder.name}?`)
    )
      return;
    const parentId = currentFolder.parent_id;
    await runFileOperation(async () => {
      await deleteFolder(currentFolder!.id);
      selectFolder(parentId);
      await loadTree();
    });
  }

  async function moveCurrentFolder() {
    if (!currentFolder) return;
    const targetId = folderMoveTarget || null;
    await runFileOperation(async () => {
      await moveFolder(currentFolder!.id, targetId);
      await loadTree();
    });
  }

  async function moveSelectedAsset(asset: AssetSummary) {
    const targetId = assetTargets[asset.id] ?? asset.parent_folder_id ?? "";
    await runFileOperation(async () => {
      await moveAsset(asset.id, targetId || null);
      await loadTree();
    });
  }

  async function removeAsset(asset: AssetSummary) {
    if (!confirm(`Delete ${asset.title || asset.filename}?`)) return;
    await runFileOperation(async () => {
      await deleteAsset(asset.id);
      await loadTree();
    });
  }

  async function runFileOperation(operation: () => Promise<void>) {
    busy = true;
    error = "";
    try {
      await operation();
    } catch (requestError) {
      reportRequestError(requestError);
    } finally {
      busy = false;
    }
  }

  async function submitLogin() {
    busy = true;
    error = "";
    try {
      await login(adminPassword);
      adminPassword = "";
      authenticated = true;
      await loadTree();
    } catch (requestError) {
      error =
        requestError instanceof Error
          ? requestError.message
          : String(requestError);
    } finally {
      busy = false;
    }
  }

  function selectFolder(folderId: string | null) {
    selectedFolderId = folderId;
    const folder = folderId ? findFolder(tree.folders, folderId) : null;
    folderMoveTarget = folder?.parent_id ?? "";
  }

  function updatePolling() {
    const shouldPoll = hasActivePolling(assetsInTree(tree));
    if (shouldPoll && !poll) poll = setInterval(loadTree, 3000);
    else if (!shouldPoll && poll) {
      clearInterval(poll);
      poll = null;
    }
  }

  function reportRequestError(requestError: unknown) {
    if (requestError instanceof ApiError && requestError.status === 401) {
      setStoredAccessToken("");
      authenticated = false;
      tree = { folders: [], assets: [] };
      updatePolling();
      error = "Session expired. Sign in again.";
      return;
    }
    error =
      requestError instanceof Error
        ? requestError.message
        : String(requestError);
  }

  function signOut() {
    setStoredAccessToken("");
    authenticated = false;
    tree = { folders: [], assets: [] };
    updatePolling();
    error = "";
  }
</script>

<main>
  <header class="topbar">
    <div>
      <h1>Files</h1>
      <p>{allAssets.length} assets · {flatFolders.length} folders</p>
    </div>
    <div class="actions">
      <button on:click={loadTree}>Refresh</button>
      {#if authenticated}<button on:click={signOut}>Sign out</button>{/if}
    </div>
  </header>

  {#if authRequired && !authenticated}
    <section class="auth">
      <input
        bind:value={adminPassword}
        type="password"
        placeholder="Admin password"
        on:keydown={(event) => event.key === "Enter" && submitLogin()}
      />
      <button disabled={!adminPassword || busy} on:click={submitLogin}
        >Sign in</button
      >
    </section>
  {:else}
    <section class="workspace">
      <FolderSidebar
        folders={flatFolders}
        {selectedFolderId}
        onSelect={selectFolder}
        onAdd={addFolder}
      />

      <div class="file-pane">
        <nav class="breadcrumbs" aria-label="Current folder">
          <button on:click={() => selectFolder(null)}>Root</button>
          {#each breadcrumbs as folder}
            <span>/</span><button on:click={() => selectFolder(folder.id)}
              >{folder.name}</button
            >
          {/each}
        </nav>

        <div class="commandbar">
          <label
            >Choose file<input
              type="file"
              accept="audio/*,video/*"
              on:change={(event) =>
                (uploadFile = event.currentTarget.files?.[0] ?? null)}
            /></label
          >
          <label
            >Import folder<input
              type="file"
              accept="audio/*,video/*"
              multiple
              webkitdirectory
              on:change={(event) => selectDirectory(event.currentTarget.files)}
            /></label
          >
          <button
            disabled={(!uploadFile && uploadEntries.length === 0) || busy}
            on:click={submitUpload}
          >
            {busy && uploadProgress
              ? "Uploading"
              : uploadEntries.length
                ? `Upload ${uploadEntries.length}`
                : "Upload"}
          </button>
          {#if currentFolder}
            <button on:click={editCurrentFolder}>Rename folder</button>
            <select
              bind:value={folderMoveTarget}
              aria-label="Move folder destination"
            >
              <option value="">Root</option>
              {#each folderMoveOptions as item}
                <option value={item.folder.id}
                  >{"  ".repeat(item.depth)}{item.folder.name}</option
                >
              {/each}
            </select>
            <button
              disabled={folderMoveTarget === (currentFolder.parent_id ?? "")}
              on:click={moveCurrentFolder}>Move folder</button
            >
            <button class="danger" on:click={removeCurrentFolder}
              >Delete folder</button
            >
          {/if}
        </div>

        {#if uploadFile || uploadEntries.length}
          <p class="selection">
            {uploadFile?.name ?? `${uploadEntries.length} files selected`}
          </p>
        {/if}
        {#if uploadProgress}
          <div class="progress">
            <span>{uploadProgress.filename}</span>
            <progress value={uploadProgress.uploaded} max={uploadProgress.total}
            ></progress>
          </div>
        {/if}
        {#if error}<p class="error" aria-live="polite">{error}</p>{/if}
        {#if batchResults.length}
          <ul class="batch-results">
            {#each batchResults as result}
              <li class:failed={result.status === "failed"}>
                {result.path}: {result.status}{result.detail
                  ? ` (${result.detail})`
                  : ""}
              </li>
            {/each}
          </ul>
        {/if}

        <HomeAssetList
          assets={visibleAssets}
          folders={flatFolders}
          {assetTargets}
          {busy}
          onTargetChange={setAssetTarget}
          onMove={moveSelectedAsset}
          onDelete={removeAsset}
        />
      </div>
    </section>
  {/if}
</main>

<style src="./home-page.css"></style>
