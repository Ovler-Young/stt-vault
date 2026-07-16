<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    ApiError,
    createFolder,
    deleteAsset,
    deleteFolder,
    fetchConfig,
    fetchFolderTree,
    getStoredAccessToken,
    login,
    moveAsset,
    moveFolder,
    renameFolder,
    setStoredAccessToken,
    uploadAsset,
    uploadAssetBatch,
    type AssetSummary,
    type FolderNode,
    type FolderTree,
    type UploadProgress
  } from '$lib/api';
  import { formatDate, formatRecordedAt, formatTime } from '$lib/format';

  type FlatFolder = { folder: FolderNode; depth: number };

  let tree: FolderTree = { folders: [], assets: [] };
  let selectedFolderId: string | null = null;
  let uploadFile: File | null = null;
  let uploadEntries: Array<{ file: File; path: string }> = [];
  let batchResults: Array<{ path: string; status: string; detail?: string }> = [];
  let uploadProgress: UploadProgress | null = null;
  let assetTargets: Record<string, string> = {};
  let folderMoveTarget = '';
  let busy = false;
  let error = '';
  let adminPassword = '';
  let authRequired = false;
  let authenticated = false;
  let poll: ReturnType<typeof setInterval> | null = null;

  $: flatFolders = flattenFolders(tree.folders);
  $: currentFolder = selectedFolderId ? findFolder(tree.folders, selectedFolderId) : null;
  $: visibleAssets = currentFolder?.assets ?? tree.assets;
  $: breadcrumbs = selectedFolderId ? findFolderPath(tree.folders, selectedFolderId) : [];
  $: allAssets = assetsInTree(tree);
  $: folderMoveOptions = currentFolder
    ? flatFolders.filter(({ folder }) => !folderContains(currentFolder!, folder.id))
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
    if (authRequired && !authenticated) {
      tree = { folders: [], assets: [] };
      updatePolling();
      return;
    }
    try {
      tree = await fetchFolderTree();
      if (selectedFolderId && !findFolder(tree.folders, selectedFolderId)) selectedFolderId = null;
      updatePolling();
      error = '';
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
      if (uploadEntries.length > 0) {
        const result = await uploadAssetBatch(uploadEntries, (progress) => (uploadProgress = progress));
        for (const item of result.results) {
          if (item.status === 'queued' && item.id && destination) await moveAsset(item.id, destination);
        }
        batchResults = result.results;
        uploadEntries = [];
        uploadFile = null;
        await loadTree();
        return;
      }
      const selectedFile = uploadFile;
      if (!selectedFile) return;
      const result = await uploadAsset(selectedFile, selectedFile.name, (progress) => (uploadProgress = progress));
      if (destination) await moveAsset(result.id, destination);
      uploadFile = null;
      await goto(`/assets/${result.id}`);
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
      path: file.webkitRelativePath || file.name
    }));
    uploadFile = null;
  }

  async function addFolder() {
    const name = prompt('Folder name')?.trim();
    if (!name) return;
    await runFileOperation(async () => {
      const folder = await createFolder(name, selectedFolderId);
      await loadTree();
      selectFolder(folder.id);
    });
  }

  async function editCurrentFolder() {
    if (!currentFolder) return;
    const name = prompt('Folder name', currentFolder.name)?.trim();
    if (!name || name === currentFolder.name) return;
    await runFileOperation(async () => {
      await renameFolder(currentFolder!.id, name);
      await loadTree();
    });
  }

  async function removeCurrentFolder() {
    if (!currentFolder || !confirm(`Delete empty folder ${currentFolder.name}?`)) return;
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
    const targetId = assetTargets[asset.id] ?? asset.parent_folder_id ?? '';
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
    error = '';
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
    error = '';
    try {
      await login(adminPassword);
      adminPassword = '';
      authenticated = true;
      await loadTree();
    } catch (requestError) {
      error = requestError instanceof Error ? requestError.message : String(requestError);
    } finally {
      busy = false;
    }
  }

  function selectFolder(folderId: string | null) {
    selectedFolderId = folderId;
    const folder = folderId ? findFolder(tree.folders, folderId) : null;
    folderMoveTarget = folder?.parent_id ?? '';
  }

  function updatePolling() {
    const shouldPoll = assetsInTree(tree).some(
      (asset) => asset.status === 'queued' || asset.status === 'processing' || asset.summary_status === 'running'
    );
    if (shouldPoll && !poll) poll = setInterval(loadTree, 3000);
    else if (!shouldPoll && poll) {
      clearInterval(poll);
      poll = null;
    }
  }

  function reportRequestError(requestError: unknown) {
    if (requestError instanceof ApiError && requestError.status === 401) {
      setStoredAccessToken('');
      authenticated = false;
      tree = { folders: [], assets: [] };
      updatePolling();
      error = 'Session expired. Sign in again.';
      return;
    }
    error = requestError instanceof Error ? requestError.message : String(requestError);
  }

  function signOut() {
    setStoredAccessToken('');
    authenticated = false;
    tree = { folders: [], assets: [] };
    updatePolling();
    error = '';
  }

  function flattenFolders(folders: FolderNode[], depth = 0): FlatFolder[] {
    return folders.flatMap((folder) => [
      { folder, depth },
      ...flattenFolders(folder.children, depth + 1)
    ]);
  }

  function assetsInTree(folderTree: FolderTree): AssetSummary[] {
    return [
      ...folderTree.assets,
      ...flattenFolders(folderTree.folders).flatMap(({ folder }) => folder.assets)
    ];
  }

  function folderContains(folder: FolderNode, candidateId: string): boolean {
    return folder.id === candidateId || folder.children.some((child) => folderContains(child, candidateId));
  }

  function findFolder(folders: FolderNode[], id: string): FolderNode | null {
    for (const folder of folders) {
      if (folder.id === id) return folder;
      const child = findFolder(folder.children, id);
      if (child) return child;
    }
    return null;
  }

  function findFolderPath(folders: FolderNode[], id: string, path: FolderNode[] = []): FolderNode[] {
    for (const folder of folders) {
      const nextPath = [...path, folder];
      if (folder.id === id) return nextPath;
      const childPath = findFolderPath(folder.children, id, nextPath);
      if (childPath.length) return childPath;
    }
    return [];
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
      <input bind:value={adminPassword} type="password" placeholder="Admin password" on:keydown={(event) => event.key === 'Enter' && submitLogin()} />
      <button disabled={!adminPassword || busy} on:click={submitLogin}>Sign in</button>
    </section>
  {:else}
    <section class="workspace">
      <aside>
        <div class="aside-head">
          <strong>Folders</strong>
          <button on:click={addFolder}>New</button>
        </div>
        <button class:active={selectedFolderId === null} class="folder root" on:click={() => selectFolder(null)}>Root</button>
        {#each flatFolders as item}
          <button
            class:active={selectedFolderId === item.folder.id}
            class="folder"
            style={`padding-left: ${12 + item.depth * 18}px`}
            on:click={() => selectFolder(item.folder.id)}
          >{item.folder.name}</button>
        {/each}
      </aside>

      <div class="file-pane">
        <nav class="breadcrumbs" aria-label="Current folder">
          <button on:click={() => selectFolder(null)}>Root</button>
          {#each breadcrumbs as folder}
            <span>/</span><button on:click={() => selectFolder(folder.id)}>{folder.name}</button>
          {/each}
        </nav>

        <div class="commandbar">
          <label>Choose file<input type="file" accept="audio/*,video/*" on:change={(event) => (uploadFile = event.currentTarget.files?.[0] ?? null)} /></label>
          <label>Import folder<input type="file" accept="audio/*,video/*" multiple webkitdirectory on:change={(event) => selectDirectory(event.currentTarget.files)} /></label>
          <button disabled={(!uploadFile && uploadEntries.length === 0) || busy} on:click={submitUpload}>
            {busy && uploadProgress ? 'Uploading' : uploadEntries.length ? `Upload ${uploadEntries.length}` : 'Upload'}
          </button>
          {#if currentFolder}
            <button on:click={editCurrentFolder}>Rename folder</button>
            <select bind:value={folderMoveTarget} aria-label="Move folder destination">
              <option value="">Root</option>
              {#each folderMoveOptions as item}
                <option value={item.folder.id}>{'  '.repeat(item.depth)}{item.folder.name}</option>
              {/each}
            </select>
            <button disabled={folderMoveTarget === (currentFolder.parent_id ?? '')} on:click={moveCurrentFolder}>Move folder</button>
            <button class="danger" on:click={removeCurrentFolder}>Delete folder</button>
          {/if}
        </div>

        {#if uploadFile || uploadEntries.length}
          <p class="selection">{uploadFile?.name ?? `${uploadEntries.length} files selected`}</p>
        {/if}
        {#if uploadProgress}
          <div class="progress">
            <span>{uploadProgress.filename}</span>
            <progress value={uploadProgress.uploaded} max={uploadProgress.total}></progress>
          </div>
        {/if}
        {#if error}<p class="error" aria-live="polite">{error}</p>{/if}
        {#if batchResults.length}
          <ul class="batch-results">
            {#each batchResults as result}
              <li class:failed={result.status === 'failed'}>{result.path}: {result.status}{result.detail ? ` (${result.detail})` : ''}</li>
            {/each}
          </ul>
        {/if}

        <div class="asset-list">
          {#each visibleAssets as asset}
            <article>
              <a class="asset-link" href={`/assets/${asset.id}`}>
                <strong>{asset.title || asset.filename}</strong>
                <span>{asset.filename}</span>
                <small>
                  {asset.status} · {formatTime(asset.duration)} · {asset.recorded_at ? formatRecordedAt(asset.recorded_at) : formatDate(asset.updated_at)}
                </small>
              </a>
              <select
                aria-label={`Move ${asset.filename}`}
                value={assetTargets[asset.id] ?? asset.parent_folder_id ?? ''}
                on:change={(event) => (assetTargets = { ...assetTargets, [asset.id]: event.currentTarget.value })}
              >
                <option value="">Root</option>
                {#each flatFolders as item}
                  <option value={item.folder.id}>{'  '.repeat(item.depth)}{item.folder.name}</option>
                {/each}
              </select>
              <button
                disabled={(assetTargets[asset.id] ?? asset.parent_folder_id ?? '') === (asset.parent_folder_id ?? '') || busy}
                on:click={() => moveSelectedAsset(asset)}
              >Move</button>
              <button class="danger" disabled={busy} on:click={() => removeAsset(asset)}>Delete</button>
            </article>
          {:else}
            <p class="empty">No assets in this folder.</p>
          {/each}
        </div>
      </div>
    </section>
  {/if}
</main>

<style>
  main {
    min-height: 100vh;
    background: var(--color-page-subtle);
  }

  .topbar {
    min-height: 64px;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 10px 18px;
    border-bottom: 1px solid var(--color-border);
    background: var(--color-surface-strong);
  }

  h1,
  p {
    margin: 0;
  }

  h1 {
    font-size: 19px;
  }

  .topbar p,
  .selection,
  .empty {
    color: var(--color-text-muted);
    font-size: 12px;
  }

  .actions,
  .auth,
  .aside-head,
  .commandbar,
  .breadcrumbs {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .auth {
    max-width: 560px;
    margin: 32px auto;
    padding: 16px;
  }

  .auth input {
    flex: 1;
  }

  .workspace {
    display: grid;
    grid-template-columns: 230px minmax(0, 1fr);
    min-height: calc(100vh - 64px);
  }

  aside {
    padding: 12px 8px;
    border-right: 1px solid var(--color-border);
    background: var(--color-surface-subtle);
  }

  .aside-head {
    justify-content: space-between;
    padding: 0 4px 8px;
  }

  .folder {
    width: 100%;
    min-height: 36px;
    border: 0;
    background: transparent;
    text-align: left;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .folder.active {
    background: var(--color-accent-surface);
    color: var(--color-accent-text);
  }

  .file-pane {
    min-width: 0;
    padding: 14px 18px 24px;
  }

  .breadcrumbs {
    min-height: 34px;
    overflow-x: auto;
  }

  .breadcrumbs button {
    border: 0;
    padding: 4px;
    background: transparent;
  }

  .commandbar {
    min-height: 42px;
    margin-top: 8px;
    flex-wrap: wrap;
    padding: 8px 0;
    border-top: 1px solid var(--color-border);
    border-bottom: 1px solid var(--color-border);
  }

  .commandbar label {
    display: inline-flex;
    align-items: center;
    border: 1px solid var(--color-border-strong);
    border-radius: 6px;
    background: var(--color-surface-strong);
    padding: 8px 10px;
    cursor: pointer;
  }

  .commandbar input[type='file'] {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
    pointer-events: none;
  }

  .selection,
  .progress,
  .error,
  .batch-results {
    margin-top: 10px;
  }

  .progress {
    display: grid;
    grid-template-columns: minmax(120px, 240px) minmax(160px, 1fr);
    gap: 10px;
    align-items: center;
    font-size: 12px;
  }

  progress {
    width: 100%;
  }

  .error,
  .danger,
  .failed {
    color: var(--color-danger);
  }

  .batch-results {
    max-height: 150px;
    overflow: auto;
    padding-left: 20px;
    font-size: 12px;
  }

  .asset-list {
    display: grid;
    margin-top: 12px;
    border-top: 1px solid var(--color-border);
  }

  article {
    display: grid;
    grid-template-columns: minmax(240px, 1fr) minmax(150px, 220px) auto auto;
    gap: 8px;
    align-items: center;
    min-height: 64px;
    padding: 8px 0;
    border-bottom: 1px solid var(--color-border);
  }

  .asset-link {
    display: grid;
    min-width: 0;
    gap: 2px;
    border: 0;
    background: transparent;
    padding: 2px 4px;
  }

  .asset-link strong,
  .asset-link span,
  .asset-link small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .asset-link span,
  .asset-link small {
    color: var(--color-text-muted);
    font-size: 12px;
  }

  .empty {
    padding: 24px 4px;
  }

  @media (max-width: 900px) {
    .workspace {
      grid-template-columns: 170px minmax(0, 1fr);
    }

    article {
      grid-template-columns: minmax(0, 1fr) auto auto;
    }

    article select {
      grid-column: 1 / -1;
      grid-row: 2;
    }
  }

  @media (max-width: 640px) {
    .topbar {
      align-items: flex-start;
    }

    .workspace {
      display: block;
    }

    aside {
      max-height: 190px;
      overflow: auto;
      border-right: 0;
      border-bottom: 1px solid var(--color-border);
    }

    .file-pane {
      padding: 12px;
    }

    .progress {
      grid-template-columns: 1fr;
    }

    article {
      grid-template-columns: minmax(0, 1fr) auto;
    }

    article select {
      grid-column: 1 / -1;
    }
  }
</style>
