<script lang="ts">
  import type { HomeWorkspaceProps } from "../home-page.types";
  import HomeAssetList from "./HomeAssetList.svelte";
  import FolderSidebar from "./FolderSidebar.svelte";
  let {
    selectedFolderId,
    flatFolders,
    breadcrumbs,
    visibleAssets,
    currentFolder,
    folderMoveOptions,
    folderMoveTarget,
    uploadEntryCount,
    uploadProgress,
    error,
    batchResults,
    assetTargets,
    busy,
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
  }: HomeWorkspaceProps = $props();
</script>

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
        >Choose files<input
          type="file"
          accept="audio/*,video/*"
          multiple
          disabled={busy}
          onchange={(event) => onFileChange(event.currentTarget.files)}
          onclick={(event) => (event.currentTarget.value = "")}
        /></label
      >
      <label
        >Import folder<input
          type="file"
          accept="audio/*,video/*"
          multiple
          webkitdirectory
          disabled={busy}
          onchange={(event) => onDirectoryChange(event.currentTarget.files)}
          onclick={(event) => (event.currentTarget.value = "")}
        /></label
      >
      <button disabled={uploadEntryCount === 0 || busy} onclick={onUpload}>
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
          {#each folderMoveOptions as folderEntry}
            <option value={folderEntry.folder.id}
              >{"  ".repeat(folderEntry.depth)}{folderEntry.folder.name}</option
            >
          {/each}
        </select>
        <button
          disabled={folderMoveTarget === (currentFolder.parent_id ?? "")}
          onclick={onMoveFolder}>Move folder</button
        >
        <button class="danger" onclick={onDeleteFolder}>Delete folder</button>
      {/if}
    </div>

    {#if uploadEntryCount}
      <p class="selection">
        {uploadEntryCount} files selected
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

<style>
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
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 34px;
    overflow-x: auto;
  }
  .breadcrumbs button {
    border: 0;
    padding: 4px;
    background: transparent;
  }
  .commandbar {
    display: flex;
    align-items: center;
    gap: 8px;
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
  .selection,
  .progress,
  .batch-results {
    color: var(--color-text-muted);
    font-size: 12px;
  }
  .progress {
    display: grid;
    grid-template-columns: minmax(120px, 240px) minmax(160px, 1fr);
    gap: 10px;
    align-items: center;
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
  }
  @media (max-width: 900px) {
    .workspace {
      grid-template-columns: 170px minmax(0, 1fr);
    }
  }
  @media (max-width: 640px) {
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
