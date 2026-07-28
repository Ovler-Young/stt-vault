<script lang="ts">
  import type {
    AssetSummary,
    BatchUploadResult,
    FolderNode,
    UploadProgress,
  } from "$lib/api/types";
  import HomeAssetList from "./HomeAssetList.svelte";
  import FolderSidebar from "./FolderSidebar.svelte";
  import type { FlatFolder } from "../home-page.helpers";

  type HomePageShellProps = {
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

  let {
    allAssetCount,
    folderCount,
    authRequired,
    authenticated,
    busy,
    adminPassword,
    selectedFolderId,
    flatFolders,
    breadcrumbs,
    visibleAssets,
    currentFolder,
    folderMoveOptions,
    folderMoveTarget,
    uploadFile,
    uploadEntryCount,
    uploadProgress,
    error,
    batchResults,
    assetTargets,
    onRefresh,
    onSignOut,
    onAdminPasswordChange,
    onLogin,
    onSelectFolder,
    onAddFolder,
    onFileChange,
    onDirectoryChange,
    onUpload,
    onRenameFolder,
    onFolderMoveTargetChange,
    onMoveFolder,
    onDeleteFolder,
    onAssetTargetChange,
    onMoveAsset,
    onDeleteAsset,
  }: HomePageShellProps = $props();
</script>

<main>
  <header class="topbar">
    <div>
      <h1>Files</h1>
      <p>{allAssetCount} assets · {folderCount} folders</p>
    </div>
    <div class="actions">
      <button onclick={onRefresh}>Refresh</button>
      {#if authenticated}<button onclick={onSignOut}>Sign out</button>{/if}
    </div>
  </header>

  {#if authRequired && !authenticated}
    <section class="auth">
      <input
        value={adminPassword}
        type="password"
        placeholder="Admin password"
        oninput={(event) => onAdminPasswordChange(event.currentTarget.value)}
        onkeydown={(event) => event.key === "Enter" && onLogin()}
      />
      <button disabled={!adminPassword || busy} onclick={onLogin}
        >Sign in</button
      >
    </section>
  {:else}
    <section class="workspace">
      <FolderSidebar
        folders={flatFolders}
        {selectedFolderId}
        onSelect={onSelectFolder}
        onAdd={onAddFolder}
      />

      <div class="file-pane">
        <nav class="breadcrumbs" aria-label="Current folder">
          <button onclick={() => onSelectFolder(null)}>Root</button>
          {#each breadcrumbs as folder}
            <span>/</span><button onclick={() => onSelectFolder(folder.id)}
              >{folder.name}</button
            >
          {/each}
        </nav>

        <div class="commandbar">
          <label
            >Choose file<input
              type="file"
              accept="audio/*,video/*"
              onchange={(event) =>
                onFileChange(event.currentTarget.files?.[0] ?? null)}
            /></label
          >
          <label
            >Import folder<input
              type="file"
              accept="audio/*,video/*"
              multiple
              webkitdirectory
              onchange={(event) => onDirectoryChange(event.currentTarget.files)}
            /></label
          >
          <button
            disabled={(!uploadFile && uploadEntryCount === 0) || busy}
            onclick={onUpload}
          >
            {busy && uploadProgress
              ? "Uploading"
              : uploadEntryCount
                ? `Upload ${uploadEntryCount}`
                : "Upload"}
          </button>
          {#if currentFolder}
            <button onclick={onRenameFolder}>Rename folder</button>
            <select
              value={folderMoveTarget}
              aria-label="Move folder destination"
              onchange={(event) =>
                onFolderMoveTargetChange(event.currentTarget.value)}
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
              onclick={onMoveFolder}>Move folder</button
            >
            <button class="danger" onclick={onDeleteFolder}
              >Delete folder</button
            >
          {/if}
        </div>

        {#if uploadFile || uploadEntryCount}
          <p class="selection">
            {uploadFile?.name ?? `${uploadEntryCount} files selected`}
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
          onTargetChange={onAssetTargetChange}
          onMove={onMoveAsset}
          onDelete={onDeleteAsset}
        />
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
  .selection {
    color: var(--color-text-muted);
    font-size: 12px;
  }
  .actions,
  .auth,
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
  .commandbar input[type="file"] {
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
  @media (max-width: 900px) {
    .workspace {
      grid-template-columns: 170px minmax(0, 1fr);
    }
  }
  @media (max-width: 640px) {
    .topbar {
      align-items: flex-start;
    }
    .workspace {
      display: block;
    }
    .file-pane {
      padding: 12px;
    }
    .progress {
      grid-template-columns: 1fr;
    }
  }
</style>
