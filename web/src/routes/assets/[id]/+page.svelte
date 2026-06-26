<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { page } from '$app/stores';
  import {
    deleteAsset,
    fetchAsset,
    retryAsset,
    type AssetDetail,
    type AssetSummary,
    type TranscriptSegment
  } from '$lib/api';
  import { formatDate, formatTime } from '$lib/format';

  let asset: AssetDetail | null = null;
  let error = '';
  let mediaEl: HTMLMediaElement | null = null;
  let currentTime = 0;
  let poll: ReturnType<typeof setInterval> | null = null;

  $: assetId = $page.params.id ?? '';

  onMount(async () => {
    await load();
    poll = setInterval(load, 3000);
  });

  onDestroy(() => {
    if (poll) clearInterval(poll);
  });

  async function load() {
    try {
      if (!assetId) return;
      asset = await fetchAsset(assetId);
      error = '';
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function retry() {
    if (!assetId) return;
    await retryAsset(assetId);
    await load();
  }

  async function remove() {
    if (!asset) return;
    await deleteAsset(asset.id);
    location.href = '/';
  }

  function seek(segment: TranscriptSegment) {
    if (!mediaEl) return;
    mediaEl.currentTime = segment.start;
    mediaEl.play();
  }

  function isActive(segment: TranscriptSegment) {
    return currentTime >= segment.start && currentTime < segment.end;
  }

  function progressText(asset: AssetDetail) {
    const job = asset.job;
    if (!job || !job.progress_total_chunks) return '';
    return `${job.progress_done_chunks}/${job.progress_total_chunks} chunks`;
  }
</script>

<main>
  {#if error}<p class="error">{error}</p>{/if}

  {#if asset}
    <section class="panel">
      <div class="toolbar">
        <div>
          <h1>{asset.filename}</h1>
          <p>{asset.status} {formatTime(asset.duration)} {progressText(asset)}</p>
        </div>
        <div class="actions">
          {#if asset.status === 'failed' || asset.status === 'partial'}
            <button on:click={retry}>Retry</button>
          {/if}
          <button class="danger" on:click={remove}>Delete</button>
        </div>
      </div>

      {#if asset.job}
        <div class="progress">
          <span>Stage: {asset.job.stage ?? asset.status}</span>
          <span>Done: {asset.job.progress_done_chunks}</span>
          <span>Total: {asset.job.progress_total_chunks}</span>
          <span>Retries: {asset.job.progress_failed_chunks}</span>
          {#if asset.job.next_retry_at}
            <span>Retry after: {formatDate(asset.job.next_retry_at)}</span>
          {/if}
        </div>
      {/if}

      {#if asset.error}
        <pre class="error-box">{JSON.stringify(asset.error, null, 2)}</pre>
      {/if}
    </section>

    <section class="panel">
      <video
        bind:this={mediaEl}
        controls
        src={`/api/assets/${asset.id}/media`}
        on:timeupdate={() => {
          currentTime = mediaEl?.currentTime ?? 0;
        }}
      >
        <track
          kind="captions"
          src={asset.exports?.vtt ? `/api/assets/${asset.id}/exports/vtt` : ''}
          srclang="en"
          label="Transcript"
          default
        />
      </video>

      {#if asset.diarization_stats}
        <div class="stats">
          {#each Object.entries(asset.diarization_stats) as [key, value]}
            <span>{key}: {value}s</span>
          {/each}
        </div>
      {/if}

      {#if asset.exports}
        <div class="exports">
          {#each Object.keys(asset.exports) as format}
            <a href={`/api/assets/${asset.id}/exports/${format}`} target="_blank">{format}</a>
          {/each}
        </div>
      {/if}
    </section>

    <section class="panel split">
      <article class="transcript">
        <h2>Transcript</h2>
        {#if asset.transcript_segments?.length}
          {#each asset.transcript_segments as segment}
            <button class:active={isActive(segment)} on:click={() => seek(segment)}>
              <span class="speaker">{segment.speaker_name ?? segment.speaker}</span>
              <span class="time">{formatTime(segment.start)} - {formatTime(segment.end)}</span>
              <span class="text">{segment.text}</span>
            </button>
          {/each}
        {:else}
          <p class="muted">Completed chunks will appear here during processing.</p>
        {/if}
      </article>

      <aside class="events">
        <h2>Log</h2>
        {#each asset.events ?? [] as event}
          <div class={`event ${event.level}`}>
            <small>{formatDate(event.created_at)} · {event.stage ?? event.level}</small>
            <p>{event.message}</p>
          </div>
        {/each}
      </aside>
    </section>
  {/if}
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

  .toolbar,
  .actions,
  .progress,
  .stats,
  .exports {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }

  .toolbar {
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
    font-size: 16px;
  }

  video {
    width: 100%;
    max-height: 52vh;
    background: #111;
    border-radius: 8px;
  }

  .progress,
  .stats,
  .exports {
    margin-top: 12px;
  }

  .progress span,
  .stats span {
    border: 1px solid #d2cec4;
    border-radius: 6px;
    padding: 6px 8px;
    background: white;
    font-size: 12px;
  }

  .split {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 360px;
    gap: 16px;
  }

  .transcript,
  .events {
    display: grid;
    align-content: start;
    gap: 8px;
  }

  .transcript button {
    display: grid;
    grid-template-columns: 130px 120px 1fr;
    gap: 10px;
    text-align: left;
    align-items: baseline;
  }

  .transcript button.active {
    border-color: #2f6f73;
    background: #e4f0ed;
  }

  .speaker {
    font-weight: 700;
  }

  .time,
  small,
  .muted,
  .toolbar p {
    color: #666052;
    font-size: 12px;
  }

  .event {
    border-left: 3px solid #b9b2a4;
    padding: 6px 8px;
    background: white;
  }

  .event.warning {
    border-left-color: #a66b00;
  }

  .event.error {
    border-left-color: #9b1c1c;
  }

  .error,
  .danger {
    color: #9b1c1c;
  }

  .error-box {
    margin-top: 12px;
    white-space: pre-wrap;
    background: #fff1f1;
    border: 1px solid #efb4b4;
    border-radius: 6px;
    padding: 10px;
  }

  @media (max-width: 980px) {
    .split {
      grid-template-columns: 1fr;
    }

    .transcript button {
      grid-template-columns: 1fr;
    }
  }
</style>
