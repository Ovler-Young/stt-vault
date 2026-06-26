<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { page } from '$app/stores';
  import {
    deleteAsset,
    detectAssetVisualEvents,
    fetchAsset,
    recomputeAssetSpeakers,
    retryAsset,
    saveAssetSpeaker,
    type AssetDetail,
    type TranscriptSegment
  } from '$lib/api';
  import { formatDate, formatTime } from '$lib/format';

  let asset: AssetDetail | null = null;
  let error = '';
  let mediaEl: HTMLMediaElement | null = null;
  let currentTime = 0;
  let poll: ReturnType<typeof setInterval> | null = null;
  let showHistory = false;
  let speakerDrafts: Record<string, string> = {};
  let speakerMessage = '';
  let visualMessage = '';

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
      syncSpeakerDrafts();
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

  function syncSpeakerDrafts() {
    if (!asset) return;
    for (const row of localSpeakerRows(asset)) {
      if (!(row.localSpeaker in speakerDrafts)) {
        speakerDrafts[row.localSpeaker] = row.displayName;
      }
    }
  }

  function localSpeakerRows(asset: AssetDetail) {
    const speakers = new Set<string>();
    for (const speaker of Object.keys(asset.speaker_centroids ?? {})) speakers.add(speaker);
    for (const segment of asset.transcript_segments ?? []) speakers.add(segment.speaker);

    return [...speakers].sort().map((localSpeaker) => {
      const segments = asset.transcript_segments?.filter((segment) => segment.speaker === localSpeaker) ?? [];
      const named = segments.find((segment) => segment.speaker_name && segment.speaker_name !== localSpeaker);
      const matched = segments.find((segment) => segment.speaker_id && segment.speaker_id !== localSpeaker);
      return {
        localSpeaker,
        displayName: named?.speaker_name ?? localSpeaker,
        speakerId: matched?.speaker_id,
        similarity: matched?.speaker_similarity ?? null,
        count: segments.length,
        firstStart: segments.length ? Math.min(...segments.map((segment) => segment.start)) : null,
        lastEnd: segments.length ? Math.max(...segments.map((segment) => segment.end)) : null
      };
    });
  }

  async function saveSpeakerName(localSpeaker: string) {
    if (!asset) return;
    const displayName = speakerDrafts[localSpeaker]?.trim();
    if (!displayName) return;
    try {
      const speaker = await saveAssetSpeaker(asset.id, localSpeaker, displayName);
      speakerMessage = `${localSpeaker} saved as ${speaker.display_name}`;
      await load();
    } catch (err) {
      speakerMessage = '';
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function recomputeSpeakers() {
    if (!asset) return;
    try {
      const result = await recomputeAssetSpeakers(asset.id);
      speakerMessage = `${result.assets} asset recomputed`;
      await load();
    } catch (err) {
      speakerMessage = '';
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function detectVisualEvents() {
    if (!asset) return;
    try {
      const result = await detectAssetVisualEvents(asset.id);
      visualMessage = `${result.events} slide markers detected`;
      await load();
    } catch (err) {
      visualMessage = '';
      error = err instanceof Error ? err.message : String(err);
    }
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

      {#if asset.media_type === 'video'}
        <div class="visual-events">
          <div class="visual-head">
            <h2>Slide Markers</h2>
            <button on:click={detectVisualEvents}>Detect</button>
          </div>
          {#if visualMessage}<p class="message">{visualMessage}</p>{/if}
          {#if asset.visual_events?.length}
            <div class="marker-list">
              {#each asset.visual_events as event}
                <button on:click={() => seek({ start: event.timestamp, end: event.timestamp + 1, speaker: '', text: '' })}>
                  <span>{formatTime(event.timestamp)}</span>
                  <small>{event.score.toFixed(1)}</small>
                </button>
              {/each}
            </div>
          {:else}
            <p class="muted">No slide markers detected yet.</p>
          {/if}
        </div>
      {/if}

      {#if Object.keys(asset.speaker_centroids ?? {}).length}
        <div class="speaker-controls">
          <div class="speaker-head">
            <h2>Speakers</h2>
            <button on:click={recomputeSpeakers}>Recompute matches</button>
          </div>
          {#each localSpeakerRows(asset) as speaker}
            <div class="speaker-row">
              <strong>{speaker.localSpeaker}</strong>
              <input bind:value={speakerDrafts[speaker.localSpeaker]} />
              <small>
                {speaker.count} chunks
                {#if speaker.firstStart !== null && speaker.lastEnd !== null}
                  · {formatTime(speaker.firstStart)} - {formatTime(speaker.lastEnd)}
                {/if}
                {#if speaker.speakerId} · {speaker.speakerId}{/if}
                {#if speaker.similarity !== null} · match {speaker.similarity.toFixed(3)}{/if}
              </small>
              <button on:click={() => saveSpeakerName(speaker.localSpeaker)}>Save</button>
            </div>
          {/each}
          {#if speakerMessage}<p class="message">{speakerMessage}</p>{/if}
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
        <div class="events-head">
          <h2>{showHistory ? 'Full Log' : 'Current Run Log'}</h2>
          <button on:click={() => (showHistory = !showHistory)}>
            {showHistory ? 'Current run' : 'All history'}
          </button>
        </div>
        {#each (showHistory ? asset.event_history : asset.events) ?? [] as event}
          <div class={`event ${event.level}`}>
            <small>
              {formatDate(event.created_at)} · run {event.run_attempt ?? 0} · {event.stage ?? event.level}
            </small>
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
  .exports,
  .speaker-controls {
    margin-top: 12px;
  }

  .speaker-controls {
    display: grid;
    gap: 8px;
  }

  .visual-events {
    display: grid;
    gap: 8px;
    margin-top: 12px;
  }

  .visual-head,
  .speaker-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .marker-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .marker-list button {
    display: flex;
    gap: 6px;
    align-items: center;
  }

  .speaker-row {
    display: grid;
    grid-template-columns: 110px minmax(180px, 1fr) minmax(220px, 1.4fr) auto;
    gap: 8px;
    align-items: center;
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

  .events-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
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

  .message {
    color: #2f6f73;
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

    .speaker-row {
      grid-template-columns: 1fr;
    }
  }
</style>
