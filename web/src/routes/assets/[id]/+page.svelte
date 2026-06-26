<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import { page } from '$app/stores';
  import SpeakerProgressBar from '$lib/SpeakerProgressBar.svelte';
  import {
    deleteAsset,
    detectAssetVisualEvents,
    fetchAsset,
    fetchAssetAudioTracks,
    recomputeAssetSpeakers,
    retryAsset,
    saveAssetSpeaker,
    type AudioTrack,
    type AssetDetail,
    type TranscriptSegment,
    type VisualEvent
  } from '$lib/api';
  import { formatDate, formatTime } from '$lib/format';

  type SpeakerEditor = {
    localSpeaker: string;
    displayName: string;
    x: number;
    y: number;
  };

  let asset: AssetDetail | null = null;
  let error = '';
  let mediaEl: HTMLMediaElement | null = null;
  let stripEl: HTMLDivElement | null = null;
  let currentTime = 0;
  let poll: ReturnType<typeof setInterval> | null = null;
  let showHistory = false;
  let speakerDrafts: Record<string, string> = {};
  let speakerMessage = '';
  let visualMessage = '';
  let speakerEditor: SpeakerEditor | null = null;
  let editorName = '';
  let audioTracks: AudioTrack[] = [];
  let audioTracksAssetId = '';
  let selectedAudioTrack = 'default';
  let pendingMediaSeek: number | null = null;
  let dragStartX = 0;
  let dragScrollLeft = 0;
  let thumbDragging = false;
  let thumbMoved = false;
  let currentSlideEventIndex = -1;
  let lastScrolledSlideIndex = -1;
  let playbackFrame: number | null = null;

  $: assetId = $page.params.id ?? '';
  $: currentSlideEventIndex = activeVisualEventIndex(asset?.visual_events ?? [], currentTime);
  $: scrollActiveSlideIntoView(currentSlideEventIndex);

  onMount(async () => {
    await load();
    poll = setInterval(load, 3000);
  });

  onDestroy(() => {
    if (poll) clearInterval(poll);
    stopPlaybackClock();
  });

  async function load() {
    try {
      if (!assetId) return;
      asset = await fetchAsset(assetId);
      await loadAudioTracks(asset.id);
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

  function seek(segment: Pick<TranscriptSegment, 'start' | 'end'>) {
    if (!mediaEl) return;
    mediaEl.currentTime = segment.start;
    mediaEl.play().catch(() => {});
  }

  function seekToTime(time: number) {
    seek({ start: time, end: time + 1 });
  }

  function isActive(segment: TranscriptSegment) {
    return currentTime >= segment.start && currentTime < segment.end;
  }

  function updateCurrentTime() {
    currentTime = mediaEl?.currentTime ?? 0;
  }

  async function loadAudioTracks(nextAssetId: string) {
    if (audioTracksAssetId === nextAssetId) return;
    try {
      audioTracks = await fetchAssetAudioTracks(nextAssetId);
      audioTracksAssetId = nextAssetId;
      selectedAudioTrack = 'default';
    } catch {
      audioTracks = [];
      audioTracksAssetId = nextAssetId;
      selectedAudioTrack = 'default';
    }
  }

  function mediaUrl(assetId: string) {
    const params = new URLSearchParams();
    if (selectedAudioTrack !== 'default') {
      params.set('audio_track', selectedAudioTrack);
    }
    const query = params.toString();
    return `/api/assets/${assetId}/media${query ? `?${query}` : ''}`;
  }

  async function changeAudioTrack() {
    pendingMediaSeek = mediaEl?.currentTime ?? currentTime;
    await tick();
    mediaEl?.load();
  }

  function restoreMediaSeek() {
    if (pendingMediaSeek === null || !mediaEl) return;
    mediaEl.currentTime = pendingMediaSeek;
    pendingMediaSeek = null;
  }

  function audioTrackLabel(track: AudioTrack) {
    const parts = [`Track ${track.audio_index + 1}`];
    if (track.title) parts.push(track.title);
    if (track.language) parts.push(track.language);
    if (track.channel_layout) parts.push(track.channel_layout);
    else if (track.channels) parts.push(`${track.channels}ch`);
    if (track.codec_name) parts.push(track.codec_name);
    return parts.join(' · ');
  }

  function startPlaybackClock() {
    stopPlaybackClock();
    const tick = () => {
      updateCurrentTime();
      if (mediaEl && !mediaEl.paused) {
        playbackFrame = requestAnimationFrame(tick);
      }
    };
    tick();
  }

  function stopPlaybackClock() {
    if (playbackFrame !== null) {
      cancelAnimationFrame(playbackFrame);
      playbackFrame = null;
    }
    updateCurrentTime();
  }

  function progressText(asset: AssetDetail) {
    const job = asset.job;
    if (!job || !job.progress_total_chunks) return '';
    return `${job.progress_done_chunks}/${job.progress_total_chunks}`;
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

  async function saveSpeakerName(localSpeaker: string, displayName?: string) {
    if (!asset) return;
    const nextName = (displayName ?? speakerDrafts[localSpeaker])?.trim();
    if (!nextName) return;
    try {
      const speaker = await saveAssetSpeaker(asset.id, localSpeaker, nextName);
      speakerDrafts[localSpeaker] = speaker.display_name;
      speakerMessage = `${localSpeaker} saved as ${speaker.display_name}`;
      speakerEditor = null;
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
      visualMessage = `${result.events} slide frames detected`;
      await load();
    } catch (err) {
      visualMessage = '';
      error = err instanceof Error ? err.message : String(err);
    }
  }

  function openSpeakerEditor(event: MouseEvent, segment: TranscriptSegment) {
    event.preventDefault();
    const displayName = segment.speaker_name ?? segment.speaker;
    speakerDrafts[segment.speaker] = speakerDrafts[segment.speaker] ?? displayName;
    editorName = speakerDrafts[segment.speaker];
    speakerEditor = {
      localSpeaker: segment.speaker,
      displayName,
      x: Math.min(event.clientX, window.innerWidth - 280),
      y: Math.min(event.clientY, window.innerHeight - 150)
    };
  }

  function startThumbDrag(event: MouseEvent) {
    if (!stripEl) return;
    thumbDragging = true;
    thumbMoved = false;
    dragStartX = event.pageX;
    dragScrollLeft = stripEl.scrollLeft;
  }

  function moveThumbDrag(event: MouseEvent) {
    if (!stripEl || !thumbDragging) return;
    const delta = event.pageX - dragStartX;
    if (Math.abs(delta) > 3) thumbMoved = true;
    stripEl.scrollLeft = dragScrollLeft - delta;
  }

  function endThumbDrag() {
    thumbDragging = false;
  }

  function seekVisualEvent(event: MouseEvent, marker: VisualEvent) {
    if (thumbMoved) {
      event.preventDefault();
      thumbMoved = false;
      return;
    }
    seek({ start: marker.timestamp, end: marker.timestamp + 1 });
  }

  function activeVisualEventIndex(markers: VisualEvent[], time: number) {
    let activeIndex = -1;
    for (let index = 0; index < markers.length; index += 1) {
      if (markers[index].timestamp > time + 0.25) break;
      activeIndex = markers[index].event_index;
    }
    return activeIndex;
  }

  function scrollActiveSlideIntoView(index: number) {
    if (!stripEl || thumbDragging || index < 0 || index === lastScrolledSlideIndex) return;
    lastScrolledSlideIndex = index;
    requestAnimationFrame(() => {
      const item = stripEl?.querySelector<HTMLElement>(`[data-slide-index="${index}"]`);
      if (!stripEl || !item) return;
      const target =
        item.offsetLeft - stripEl.clientWidth / 2 + item.clientWidth / 2;
      stripEl.scrollTo({ left: Math.max(0, target), behavior: 'smooth' });
    });
  }

  function thumbnailUrl(event: VisualEvent) {
    return `/api/assets/${assetId}/visual-events/${event.event_index}/thumbnail`;
  }

  function formatStatValue(value: unknown) {
    if (typeof value === 'number') return `${Number(value.toFixed(3))}`;
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }
</script>

<main>
  {#if error}<p class="error">{error}</p>{/if}

  {#if asset}
    <section class="asset-head">
      <div class="title">
        <h1>{asset.filename}</h1>
        <p>{asset.status} · {formatTime(asset.duration)} {#if progressText(asset)}· {progressText(asset)} chunks{/if}</p>
      </div>
      <div class="actions">
        {#if asset.status === 'failed' || asset.status === 'partial'}
          <button on:click={retry}>Retry</button>
        {/if}
        <button class="danger" on:click={remove}>Delete</button>
      </div>
    </section>

    <section class="workspace">
      <div class="media-pane">
        <video
          bind:this={mediaEl}
          controls
          src={mediaUrl(asset.id)}
          on:timeupdate={updateCurrentTime}
          on:seeked={updateCurrentTime}
          on:play={updateCurrentTime}
          on:playing={startPlaybackClock}
          on:pause={stopPlaybackClock}
          on:ended={stopPlaybackClock}
          on:loadedmetadata={restoreMediaSeek}
        >
          <track
            kind="captions"
            src={asset.exports?.vtt ? `/api/assets/${asset.id}/exports/vtt` : ''}
            srclang="en"
            label="Transcript"
            default
          />
        </video>

        {#if audioTracks.length > 1}
          <label class="audio-track-picker">
            <span>Audio</span>
            <select bind:value={selectedAudioTrack} on:change={changeAudioTrack}>
              <option value="default">Original playback</option>
              <option value="all">All tracks mixed</option>
              {#each audioTracks as track}
                <option value={`${track.audio_index}`}>{audioTrackLabel(track)}</option>
              {/each}
            </select>
          </label>
        {/if}

        <SpeakerProgressBar
          segments={asset.transcript_segments ?? []}
          duration={asset.duration}
          {currentTime}
          on:seek={(event) => seekToTime(event.detail.time)}
        />

        {#if asset.media_type === 'video'}
          <div class="visual-bar">
            <div class="visual-actions">
              <strong>Slides</strong>
              <button on:click={detectVisualEvents}>Detect</button>
              {#if visualMessage}<span>{visualMessage}</span>{/if}
            </div>
            {#if asset.visual_events?.length}
              <div
                class:dragging={thumbDragging}
                class="thumb-strip"
                role="listbox"
                aria-label="Slide thumbnails"
                tabindex="0"
                bind:this={stripEl}
                on:mousedown={startThumbDrag}
                on:mousemove={moveThumbDrag}
                on:mouseup={endThumbDrag}
                on:mouseleave={endThumbDrag}
              >
                {#each asset.visual_events as event}
                  <button
                    role="option"
                    data-slide-index={event.event_index}
                    aria-selected={currentSlideEventIndex === event.event_index}
                    class:active={currentSlideEventIndex === event.event_index}
                    on:click={(clickEvent) => seekVisualEvent(clickEvent, event)}
                    title={`${formatTime(event.timestamp)} · ${event.score.toFixed(1)}`}
                  >
                    <img draggable="false" src={thumbnailUrl(event)} alt={formatTime(event.timestamp)} />
                    <span>{formatTime(event.timestamp)}</span>
                  </button>
                {/each}
              </div>
            {:else}
              <p class="muted">No slide frames detected yet.</p>
            {/if}
          </div>
        {/if}

        <div class="foldouts">
          <details>
            <summary>Details</summary>
            {#if asset.job}
              <div class="kv">
                <span>Stage</span><strong>{asset.job.stage ?? asset.status}</strong>
                <span>Done</span><strong>{asset.job.progress_done_chunks}</strong>
                <span>Total</span><strong>{asset.job.progress_total_chunks}</strong>
                <span>Retries</span><strong>{asset.job.progress_failed_chunks}</strong>
                {#if asset.job.next_retry_at}
                  <span>Retry after</span><strong>{formatDate(asset.job.next_retry_at)}</strong>
                {/if}
              </div>
            {/if}
            {#if asset.diarization_stats}
              <div class="stats">
                {#each Object.entries(asset.diarization_stats) as [key, value]}
                  <span>{key}: {formatStatValue(value)}</span>
                {/each}
              </div>
            {/if}
            {#if asset.error}
              <pre class="error-box">{JSON.stringify(asset.error, null, 2)}</pre>
            {/if}
          </details>

          {#if asset.exports}
            <details>
              <summary>Downloads</summary>
              <div class="exports">
                {#each Object.keys(asset.exports) as format}
                  <a href={`/api/assets/${asset.id}/exports/${format}`} target="_blank">{format}</a>
                {/each}
              </div>
            </details>
          {/if}

          {#if Object.keys(asset.speaker_centroids ?? {}).length}
            <details>
              <summary>Speakers</summary>
              <div class="speaker-controls">
                <button on:click={recomputeSpeakers}>Recompute matches</button>
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
            </details>
          {/if}

          <details>
            <summary>{showHistory ? 'Full Log' : 'Current Run Log'}</summary>
            <button class="log-toggle" on:click={() => (showHistory = !showHistory)}>
              {showHistory ? 'Current run' : 'All history'}
            </button>
            <div class="events">
              {#each (showHistory ? asset.event_history : asset.events) ?? [] as event}
                <div class={`event ${event.level}`}>
                  <small>
                    {formatDate(event.created_at)} · run {event.run_attempt ?? 0} · {event.stage ?? event.level}
                  </small>
                  <p>{event.message}</p>
                </div>
              {/each}
            </div>
          </details>
        </div>
      </div>

      <article class="transcript">
        {#if asset.transcript_segments?.length}
          {#each asset.transcript_segments as segment}
            <button
              class:active={isActive(segment)}
              on:click={() => seek(segment)}
              on:contextmenu={(event) => openSpeakerEditor(event, segment)}
            >
              <span class="row-head">
                <strong>{segment.speaker_name ?? segment.speaker}</strong>
                <small>{formatTime(segment.start)} - {formatTime(segment.end)}</small>
              </span>
              <span class="text">{segment.text}</span>
            </button>
          {/each}
        {:else}
          <p class="muted">Completed chunks will appear here during processing.</p>
        {/if}
      </article>
    </section>

    {#if speakerEditor}
      <form
        class="speaker-editor"
        style={`left:${speakerEditor.x}px; top:${speakerEditor.y}px;`}
        on:submit|preventDefault={() => saveSpeakerName(speakerEditor?.localSpeaker ?? '', editorName)}
      >
        <small>{speakerEditor.localSpeaker}</small>
        <input bind:value={editorName} />
        <div>
          <button type="submit">Save</button>
          <button type="button" on:click={() => (speakerEditor = null)}>Cancel</button>
        </div>
      </form>
    {/if}
  {/if}
</main>

<style>
  main {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    gap: 8px;
    height: 100vh;
    overflow: hidden;
    padding: 10px;
  }

  .asset-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-width: 0;
    border: 1px solid #d2cec4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 8px 10px;
  }

  .title {
    min-width: 0;
  }

  h1,
  p {
    margin: 0;
  }

  h1 {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 16px;
  }

  .title p,
  small,
  .muted {
    color: #666052;
    font-size: 11px;
  }

  .actions,
  .exports,
  .visual-actions {
    display: flex;
    gap: 6px;
    align-items: center;
    flex-wrap: wrap;
  }

  .workspace {
    display: grid;
    grid-template-columns: minmax(420px, 47vw) minmax(360px, 1fr);
    gap: 10px;
    align-items: start;
    min-height: 0;
    overflow: hidden;
  }

  .media-pane,
  .transcript {
    min-width: 0;
    max-height: 100%;
    overflow-y: auto;
  }

  .media-pane {
    padding-right: 2px;
  }

  video {
    width: 100%;
    max-height: 58vh;
    background: #111;
    border-radius: 8px;
  }

  .audio-track-picker {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 6px;
    color: #666052;
    font-size: 12px;
  }

  .audio-track-picker select {
    min-width: 0;
    max-width: 100%;
    border: 1px solid #c7c1b4;
    border-radius: 6px;
    background: white;
    color: #151515;
    font: inherit;
    padding: 5px 8px;
  }

  .visual-bar {
    display: grid;
    gap: 6px;
    margin-top: 6px;
    border: 1px solid #d2cec4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 8px;
  }

  .visual-actions strong {
    font-size: 13px;
  }

  .visual-actions span {
    color: #2f6f73;
    font-size: 12px;
  }

  .thumb-strip {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    overscroll-behavior-x: contain;
    padding-bottom: 4px;
    cursor: grab;
    user-select: none;
  }

  .thumb-strip.dragging {
    cursor: grabbing;
  }

  .thumb-strip button {
    position: relative;
    flex: 0 0 144px;
    height: 81px;
    overflow: hidden;
    padding: 0;
    border-color: #c7c1b4;
    background: #111;
  }

  .thumb-strip button.active {
    border-color: #2f6f73;
    box-shadow: 0 0 0 2px #2f6f7333;
  }

  .thumb-strip img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
    pointer-events: none;
  }

  .thumb-strip span {
    position: absolute;
    right: 4px;
    bottom: 4px;
    border-radius: 4px;
    background: rgb(0 0 0 / 68%);
    color: white;
    font-size: 11px;
    padding: 2px 4px;
  }

  .foldouts {
    display: grid;
    gap: 6px;
    margin-top: 8px;
  }

  details {
    border: 1px solid #d2cec4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 6px 8px;
  }

  summary {
    cursor: pointer;
    font-weight: 700;
    font-size: 13px;
  }

  .kv {
    display: grid;
    grid-template-columns: 88px minmax(0, 1fr);
    gap: 4px 8px;
    margin-top: 8px;
    font-size: 12px;
  }

  .kv span {
    color: #666052;
  }

  .stats,
  .exports,
  .speaker-controls,
  .events {
    margin-top: 8px;
  }

  .stats {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }

  .stats span {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    border: 1px solid #d2cec4;
    border-radius: 6px;
    padding: 4px 6px;
    background: white;
    font-size: 11px;
  }

  .speaker-controls {
    display: grid;
    gap: 6px;
  }

  .speaker-row {
    display: grid;
    grid-template-columns: 92px minmax(140px, 1fr) minmax(180px, 1.3fr) auto;
    gap: 6px;
    align-items: center;
  }

  .transcript {
    display: grid;
    align-content: start;
    gap: 4px;
    padding-right: 4px;
    overscroll-behavior: contain;
  }

  .transcript button {
    display: grid;
    gap: 3px;
    width: 100%;
    padding: 6px 8px;
    text-align: left;
    border-radius: 6px;
    background: #fffdfa;
  }

  .transcript button.active {
    border-color: #2f6f73;
    background: #e4f0ed;
  }

  .row-head {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    align-items: baseline;
    min-width: 0;
  }

  .row-head strong {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
  }

  .row-head small {
    white-space: nowrap;
  }

  .text {
    line-height: 1.35;
    font-size: 13px;
  }

  .events {
    display: grid;
    gap: 4px;
    max-height: 280px;
    overflow: auto;
  }

  .event {
    border-left: 3px solid #b9b2a4;
    padding: 4px 6px;
    background: white;
  }

  .event p {
    font-size: 12px;
  }

  .event.warning {
    border-left-color: #a66b00;
  }

  .event.error {
    border-left-color: #9b1c1c;
  }

  .log-toggle {
    margin-top: 8px;
  }

  .speaker-editor {
    position: fixed;
    z-index: 30;
    display: grid;
    gap: 6px;
    width: 260px;
    border: 1px solid #c7c1b4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 8px;
    box-shadow: 0 12px 32px rgb(0 0 0 / 18%);
  }

  .speaker-editor div {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
  }

  .error,
  .danger {
    color: #9b1c1c;
  }

  .message {
    color: #2f6f73;
  }

  .error-box {
    margin-top: 8px;
    white-space: pre-wrap;
    background: #fff1f1;
    border: 1px solid #efb4b4;
    border-radius: 6px;
    padding: 8px;
  }

  @media (max-width: 980px) {
    main {
      height: auto;
      min-height: 100vh;
      overflow: visible;
      padding: 8px;
    }

    .workspace {
      grid-template-columns: 1fr;
      overflow: visible;
    }

    .media-pane,
    .transcript {
      max-height: none;
      overflow: visible;
    }

    .speaker-row {
      grid-template-columns: 1fr;
    }
  }
</style>
