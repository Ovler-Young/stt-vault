<script lang="ts">
  import type { HomePageShellProps } from "../home-page.types";
  import HomeWorkspace from "./HomeWorkspace.svelte";

  let {
    allAssetCount,
    folderCount,
    authRequired,
    authenticated,
    authPending,
    busy,
    adminPassword,
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
    {#if !authPending}
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
    {/if}
  {:else}
    <HomeWorkspace
      {selectedFolderId}
      {flatFolders}
      {breadcrumbs}
      {visibleAssets}
      {currentFolder}
      {folderMoveOptions}
      {folderMoveTarget}
      {uploadEntryCount}
      {uploadProgress}
      {error}
      {batchResults}
      {assetTargets}
      {busy}
      {onSelectFolder}
      {onAddFolder}
      {onFileChange}
      {onDirectoryChange}
      {onUpload}
      {onRenameFolder}
      {onFolderMoveTargetChange}
      {onMoveFolder}
      {onDeleteFolder}
      {onAssetTargetChange}
      {onMoveAsset}
      {onDeleteAsset}
    />
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
  .topbar p {
    color: var(--color-text-muted);
    font-size: 12px;
  }
  .actions,
  .auth {
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
  @media (max-width: 640px) {
    .topbar {
      align-items: flex-start;
    }
  }
</style>
