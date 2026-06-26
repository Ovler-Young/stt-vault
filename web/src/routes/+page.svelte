<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import {
    deleteAsset,
    fetchAsset,
    fetchAssets,
    fetchConfig,
    getStoredPassword,
    setStoredPassword,
    uploadAsset,
    type AssetDetail,
    type AssetSummary,
    type TranscriptSegment
  } from '$lib/api';

  let assets: AssetSummary[] = [];
  let selected: AssetDetail | null = null;
  let uploadFile: File | null = null;
  let busy = false;
  let error = '';
  let adminPassword = '';
  let authRequired = false;
  let mediaEl: HTMLMediaElement | null = null;
  let currentTime = 0;
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
      if (selected) {
        selected = await fetchAsset(selected.id);
      }
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
      await loadAssets();
      selected = await fetchAsset(result.id);
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      busy = false;
    }
  }

  async function selectAsset(asset: AssetSummary) {
    selected = await fetchAsset(asset.id);
  }

  async function removeAsset(asset: AssetSummary) {
    await deleteAsset(asset.id);
    if (selected?.id === asset.id) selected = null;
    await loadAssets();
  }

  function savePassword() {
    setStoredPassword(adminPassword);
    loadAssets();
  }

  function seek(segment: TranscriptSegment) {
    if (!mediaEl) return;
    mediaEl.currentTime = segment.start;
    mediaEl.play();
  }

  function isActive(segment: TranscriptSegment) {
    return currentTime >= segment.start && currentTime < segment.end;
  }

  function formatTime(seconds: number | null | undefined) {
    if (seconds == null) return '';
    const rounded = Math.floor(seconds);
    const h = Math.floor(rounded / 3600);
    const m = Math.floor((rounded % 3600) / 60);
    const s = rounded % 60;
    return h > 0
      ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
      : `${m}:${String(s).padStart(2, '0')}`;
  }
</script>

<svelte:head>
  <title>STT Vault</title>
</svelte:head>

<main>
  <aside>
    <header>
      <h1>STT Vault</h1>
      <button on:click={loadAssets}>Refresh</button>
    </header>

    {#if authRequired}
      <section class="auth">
        <input bind:value={adminPassword} type="password" placeholder="Admin password" />
        <button on:click={savePassword}>Save</button>
      </section>
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

    <section class="asset-list">
      {#each assets as asset}
        <button class:active={selected?.id === asset.id} on:click={() => selectAsset(asset)}>
          <span>{asset.filename}</span>
          <small>{asset.status} {formatTime(asset.duration)}</small>
        </button>
      {/each}
    </section>
  </aside>

  <section class="detail">
    {#if selected}
      <div class="toolbar">
        <div>
          <h2>{selected.filename}</h2>
          <p>{selected.status} {formatTime(selected.duration)}</p>
        </div>
        <button class="danger" on:click={() => removeAsset(selected as AssetSummary)}>Delete</button>
      </div>

      <video
        bind:this={mediaEl}
        controls
        src={`/api/assets/${selected.id}/media`}
        on:timeupdate={() => {
          currentTime = mediaEl?.currentTime ?? 0;
        }}
      >
        <track
          kind="captions"
          src={selected.exports?.vtt ? `/api/assets/${selected.id}/exports/vtt` : ''}
          srclang="en"
          label="Transcript"
          default
        />
      </video>

      {#if selected.diarization_stats}
        <div class="stats">
          {#each Object.entries(selected.diarization_stats) as [key, value]}
            <span>{key}: {value}s</span>
          {/each}
        </div>
      {/if}

      {#if selected.exports}
        <div class="exports">
          {#each Object.keys(selected.exports) as format}
            <a href={`/api/assets/${selected.id}/exports/${format}`} target="_blank">{format}</a>
          {/each}
        </div>
      {/if}

      {#if selected.transcript_segments?.length}
        <article class="transcript">
          {#each selected.transcript_segments as segment}
            <button class:active={isActive(segment)} on:click={() => seek(segment)}>
              <span class="speaker">{segment.speaker_name ?? segment.speaker}</span>
              <span class="time">{formatTime(segment.start)} - {formatTime(segment.end)}</span>
              <span class="text">{segment.text}</span>
            </button>
          {/each}
        </article>
      {:else}
        <p class="empty">Transcript will appear here when processing finishes.</p>
      {/if}
    {:else}
      <div class="empty-screen">Upload or select media.</div>
    {/if}
  </section>
</main>

<style>
  :global(body) {
    margin: 0;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #151515;
    background: #f5f3ef;
  }

  main {
    min-height: 100vh;
    display: grid;
    grid-template-columns: 340px 1fr;
  }

  aside {
    border-right: 1px solid #d2cec4;
    padding: 16px;
    background: #fbfaf7;
    overflow: auto;
  }

  header,
  .toolbar,
  .auth,
  .exports,
  .stats {
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

  h1 {
    font-size: 22px;
  }

  h2 {
    font-size: 18px;
  }

  button,
  input,
  a {
    border: 1px solid #c7c1b4;
    border-radius: 6px;
    background: white;
    color: inherit;
    font: inherit;
    padding: 8px 10px;
    text-decoration: none;
  }

  button {
    cursor: pointer;
  }

  button:disabled {
    cursor: default;
    opacity: 0.55;
  }

  .auth,
  .upload {
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

  .error {
    margin-top: 12px;
    color: #9b1c1c;
    font-size: 13px;
  }

  .asset-list {
    display: grid;
    gap: 8px;
    margin-top: 16px;
  }

  .asset-list button {
    display: grid;
    gap: 4px;
    text-align: left;
  }

  .asset-list button.active,
  .transcript button.active {
    border-color: #2f6f73;
    background: #e4f0ed;
  }

  small,
  .time,
  .toolbar p {
    color: #666052;
    font-size: 12px;
  }

  .detail {
    padding: 18px;
    overflow: auto;
  }

  video {
    width: 100%;
    max-height: 52vh;
    margin-top: 14px;
    background: #111;
    border-radius: 8px;
  }

  .stats,
  .exports {
    justify-content: flex-start;
    flex-wrap: wrap;
    margin-top: 12px;
  }

  .stats span,
  .exports a {
    font-size: 12px;
  }

  .transcript {
    display: grid;
    gap: 6px;
    margin-top: 16px;
  }

  .transcript button {
    display: grid;
    grid-template-columns: 130px 120px 1fr;
    gap: 10px;
    text-align: left;
    align-items: baseline;
  }

  .speaker {
    font-weight: 700;
  }

  .empty,
  .empty-screen {
    color: #666052;
    margin-top: 24px;
  }

  .empty-screen {
    display: grid;
    min-height: 80vh;
    place-items: center;
  }

  .danger {
    color: #9b1c1c;
  }

  @media (max-width: 820px) {
    main {
      grid-template-columns: 1fr;
    }

    aside {
      border-right: 0;
      border-bottom: 1px solid #d2cec4;
    }

    .transcript button {
      grid-template-columns: 1fr;
    }
  }
</style>
