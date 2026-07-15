<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    ApiError,
    fetchAssets,
    fetchConfig,
    getStoredAccessToken,
    login,
    setStoredAccessToken,
    uploadAsset,
    uploadAssetBatch,
    type AssetSummary
  } from '$lib/api';
  import { formatDate, formatTime } from '$lib/format';

  let assets: AssetSummary[] = [];
  let uploadFile: File | null = null;
  let uploadEntries: Array<{ file: File; path: string }> = [];
  let batchResults: Array<{ path: string; status: string; detail?: string }> = [];
  let busy = false;
  let error = '';
  let adminPassword = '';
  let authRequired = false;
  let authenticated = false;
  let poll: ReturnType<typeof setInterval> | null = null;

  onMount(async () => {
    const config = await fetchConfig();
    authRequired = config.auth_required;
    authenticated = Boolean(getStoredAccessToken());
    await loadAssets();
    poll = setInterval(loadAssets, 3000);
  });

  onDestroy(() => {
    if (poll) clearInterval(poll);
  });

  async function loadAssets() {
    if (authRequired && !authenticated) {
      assets = [];
      return;
    }
    try {
      assets = await fetchAssets();
      error = '';
    } catch (err) {
      reportRequestError(err);
    }
  }

  async function submitUpload() {
    if (!uploadFile && uploadEntries.length === 0) return;
    busy = true;
    batchResults = [];
    try {
      if (uploadEntries.length > 0) {
        const result = await uploadAssetBatch(uploadEntries);
        batchResults = result.results;
        uploadEntries = [];
        uploadFile = null;
        await loadAssets();
        return;
      }
      const selectedFile = uploadFile;
      if (!selectedFile) return;
      const result = await uploadAsset(selectedFile);
      uploadFile = null;
      await goto(`/assets/${result.id}`);
    } catch (err) {
      reportRequestError(err);
    } finally {
      busy = false;
    }
  }

  function selectDirectory(files: FileList | null) {
    uploadEntries = Array.from(files ?? []).map((file) => ({
      file,
      path: file.webkitRelativePath || file.name
    }));
    uploadFile = null;
  }

  async function submitLogin() {
    busy = true;
    error = '';
    try {
      await login(adminPassword);
      adminPassword = '';
      authenticated = true;
      await loadAssets();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      busy = false;
    }
  }

  function reportRequestError(err: unknown) {
    if (err instanceof ApiError && err.status === 401) {
      setStoredAccessToken('');
      authenticated = false;
      assets = [];
      error = 'Session expired. Sign in again.';
      return;
    }
    error = err instanceof Error ? err.message : String(err);
  }

  function signOut() {
    setStoredAccessToken('');
    authenticated = false;
    assets = [];
    error = '';
  }
</script>

<main>
  <section class="panel">
    <header>
      <div>
        <h1>STT Vault</h1>
        <p>Upload media, track processing, and open completed transcripts.</p>
      </div>
      <div class="actions">
        <button on:click={loadAssets}>Refresh</button>
        {#if authenticated}<button on:click={signOut}>Sign out</button>{/if}
      </div>
    </header>

    {#if authRequired && !authenticated}
      <div class="auth">
        <input
          bind:value={adminPassword}
          type="password"
          placeholder="Admin password"
          on:keydown={(event) => event.key === 'Enter' && submitLogin()}
        />
        <button disabled={!adminPassword || busy} on:click={submitLogin}>Sign in</button>
      </div>
    {/if}

    <div
      class="upload"
      role="region"
      aria-label="Upload media"
      on:dragover|preventDefault
      on:drop|preventDefault={(event) => {
        uploadFile = event.dataTransfer?.files?.[0] ?? null;
      }}
    >
      <input
        type="file"
        accept="audio/*,video/*"
        on:change={(event) => {
          uploadFile = event.currentTarget.files?.[0] ?? null;
        }}
      />
      <input
        type="file"
        accept="audio/*,video/*"
        multiple
        webkitdirectory
        aria-label="Choose a folder to import"
        on:change={(event) => selectDirectory(event.currentTarget.files)}
      />
      <button disabled={!authenticated || (!uploadFile && uploadEntries.length === 0) || busy} on:click={submitUpload}>
        {busy ? 'Uploading' : uploadEntries.length > 0 ? `Import ${uploadEntries.length} files` : 'Upload'}
      </button>
      {#if uploadFile}<p>{uploadFile.name}</p>{/if}
      {#if uploadEntries.length > 0}<p>{uploadEntries.length} files selected</p>{/if}
      {#if batchResults.length > 0}
        <ul class="batch-results">
          {#each batchResults as result}
            <li class:failed={result.status === 'failed'}>{result.path}: {result.status}{result.detail ? ` (${result.detail})` : ''}</li>
          {/each}
        </ul>
      {/if}
    </div>

    <p class="upload-status" aria-live="polite" aria-atomic="true">
      {#if error}{error}{:else if busy}Uploading {uploadFile?.name}{/if}
    </p>
  </section>

  <section class="panel">
    <h2>Assets</h2>
    <div class="asset-list">
      {#each assets as asset}
        <a href={`/assets/${asset.id}`}>
          <span>{asset.filename}</span>
          <small>{asset.status} {formatTime(asset.duration)} · {formatDate(asset.updated_at)}</small>
        </a>
      {/each}
    </div>
  </section>
</main>

<style>
  main {
    display: grid;
    gap: 16px;
    padding: 16px;
  }

  .panel {
    border: 1px solid #d2cec4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 16px;
  }

  header,
  .actions,
  .auth {
    display: flex;
    gap: 8px;
    align-items: center;
    justify-content: space-between;
  }

  h1,
  h2,
  p {
    margin: 0;
  }

  .auth,
  .upload,
  .asset-list {
    margin-top: 14px;
  }

  .auth input {
    min-width: 0;
    flex: 1;
  }

  .upload {
    border: 1px dashed #b9b2a4;
    border-radius: 8px;
    padding: 14px;
    display: grid;
    gap: 10px;
  }

  .asset-list {
    display: grid;
    gap: 8px;
  }

  .asset-list a {
    display: grid;
    gap: 4px;
  }

  small,
  .panel p {
    color: #666052;
    font-size: 13px;
  }

  .upload-status {
    margin-top: 12px;
    color: #9b1c1c;
  }

  .batch-results {
    margin: 0;
    padding-left: 20px;
  }

  .failed {
    color: #a12a22;
  }
</style>
