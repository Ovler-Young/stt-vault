<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    fetchAssets,
    fetchConfig,
    getStoredPassword,
    setStoredPassword,
    uploadAsset,
    type AssetSummary
  } from '$lib/api';
  import { formatDate, formatTime } from '$lib/format';

  let assets: AssetSummary[] = [];
  let uploadFile: File | null = null;
  let busy = false;
  let error = '';
  let adminPassword = '';
  let authRequired = false;
  let poll: ReturnType<typeof setInterval> | null = null;

  onMount(async () => {
    adminPassword = getStoredPassword();
    const config = await fetchConfig();
    authRequired = config.auth_required;
    await loadAssets();
    poll = setInterval(loadAssets, 3000);
  });

  onDestroy(() => {
    if (poll) clearInterval(poll);
  });

  async function loadAssets() {
    try {
      assets = await fetchAssets();
      error = '';
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function submitUpload() {
    if (!uploadFile) return;
    busy = true;
    try {
      const result = await uploadAsset(uploadFile);
      uploadFile = null;
      await goto(`/assets/${result.id}`);
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      busy = false;
    }
  }

  function savePassword() {
    setStoredPassword(adminPassword);
    loadAssets();
  }
</script>

<main>
  <section class="panel">
    <header>
      <div>
        <h1>STT Vault</h1>
        <p>Upload media, track processing, and open completed transcripts.</p>
      </div>
      <button on:click={loadAssets}>Refresh</button>
    </header>

    {#if authRequired}
      <div class="auth">
        <input bind:value={adminPassword} type="password" placeholder="Admin password" />
        <button on:click={savePassword}>Save</button>
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
      <button disabled={!uploadFile || busy} on:click={submitUpload}>
        {busy ? 'Uploading' : 'Upload'}
      </button>
      {#if uploadFile}<p>{uploadFile.name}</p>{/if}
    </div>

    {#if error}<p class="error">{error}</p>{/if}
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

  .error {
    margin-top: 12px;
    color: #9b1c1c;
  }
</style>
