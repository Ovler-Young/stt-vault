<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import { page } from '$app/stores';
  import {
    deleteAsset,
    detectAssetVisualEvents,
    fetchAsset,
    fetchAssetAudioTracks,
    recomputeAssetSpeakers,
    retryAsset,
    summarizeAsset,
    saveAssetSpeaker,
    type AudioTrack,
    type AssetDetail,
    type TranscriptSegment
  } from '$lib/api';
  import { localSpeakerRows, segmentMediaEnd, segmentMediaStart } from './asset-page.helpers';
  import type { SpeakerEditor, SpeakerProgressBarHandle } from './asset-page.types';
  import AssetDetailsFoldout from './components/AssetDetailsFoldout.svelte';
  import AssetDownloadsFoldout from './components/AssetDownloadsFoldout.svelte';
  import AssetEventsFoldout from './components/AssetEventsFoldout.svelte';
  import AssetFoldoutGroup from './components/AssetFoldoutGroup.svelte';
  import AssetHeader from './components/AssetHeader.svelte';
  import AssetMediaPane from './components/AssetMediaPane.svelte';
  import AssetSpeakersFoldout from './components/AssetSpeakersFoldout.svelte';
  import ResizableAssetWorkspace from './components/ResizableAssetWorkspace.svelte';
  import SpeakerEditorPopover from './components/SpeakerEditorPopover.svelte';
  import TranscriptPane from './components/TranscriptPane.svelte';
  import VisualEventsStrip from './components/VisualEventsStrip.svelte';

  let asset: AssetDetail | null = null;
  let error = '';
  let mediaEl: HTMLMediaElement | null = null;
  let speakerProgressBar: SpeakerProgressBarHandle | null = null;
  let currentTime = 0;
  let poll: ReturnType<typeof setInterval> | null = null;
  let speakerDrafts: Record<string, string> = {};
  let speakerMessage = '';
  let visualMessage = '';
  let summaryMessage = '';
  let speakerEditor: SpeakerEditor | null = null;
  let editorName = '';
  let audioTracks: AudioTrack[] = [];
  let audioTracksAssetId = '';
  let selectedAudioTrack = 'default';
  let playbackRate = 1;
  let pendingMediaSeek: number | null = null;
  let playbackFrame: number | null = null;

  $: assetId = $page.params.id ?? '';
  $: speakerRows = asset ? localSpeakerRows(asset) : [];

  onMount(async () => {
    playbackRate = Number(localStorage.getItem('stt-vault-playback-rate') ?? 1) || 1;
    document.addEventListener('keydown', handleGlobalKeydown);
    await load();
  });

  onDestroy(() => {
    document.removeEventListener('keydown', handleGlobalKeydown);
    if (poll) clearInterval(poll);
    stopPlaybackClock();
  });

  async function load() {
    try {
      if (!assetId) return;
      asset = await fetchAsset(assetId);
      await loadAudioTracks(asset.id);
      syncSpeakerDrafts();
      updatePolling();
      error = '';
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  function updatePolling() {
    const shouldPoll = asset?.status === 'queued' || asset?.status === 'processing';
    if (shouldPoll && !poll) {
      poll = setInterval(load, 3000);
    } else if (!shouldPoll && poll) {
      clearInterval(poll);
      poll = null;
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

  async function summarize() {
    if (!asset) return;
    summaryMessage = 'Generating summary';
    try {
      await summarizeAsset(asset.id);
      await load();
      summaryMessage = '';
    } catch (err) {
      await load();
      summaryMessage = asset?.summary_error ? '' : err instanceof Error ? err.message : String(err);
    }
  }

  function seek(segment: Pick<TranscriptSegment, 'start' | 'end' | 'chunk_start' | 'chunk_end'>) {
    if (!mediaEl) return;
    mediaEl.currentTime = segmentMediaStart(segment);
    mediaEl.play().catch(() => {});
  }

  function seekToTime(time: number) {
    seek({ start: time, end: time + 1 });
  }

  function updateCurrentTime() {
    currentTime = mediaEl?.currentTime ?? 0;
  }

  function applyPlaybackRate() {
    if (!mediaEl) return;
    mediaEl.playbackRate = playbackRate;
  }

  function changePlaybackRate(nextPlaybackRate: number) {
    playbackRate = nextPlaybackRate;
    localStorage.setItem('stt-vault-playback-rate', String(playbackRate));
    applyPlaybackRate();
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

  async function changeAudioTrack(nextAudioTrack: string) {
    selectedAudioTrack = nextAudioTrack;
    pendingMediaSeek = mediaEl?.currentTime ?? currentTime;
    await tick();
    mediaEl?.load();
  }

  function restoreMediaSeek() {
    applyPlaybackRate();
    if (pendingMediaSeek === null || !mediaEl) return;
    mediaEl.currentTime = pendingMediaSeek;
    pendingMediaSeek = null;
  }

  function startPlaybackClock() {
    stopPlaybackClock();
    const tickClock = () => {
      updateCurrentTime();
      if (mediaEl && !mediaEl.paused) {
        playbackFrame = requestAnimationFrame(tickClock);
      }
    };
    tickClock();
  }

  function stopPlaybackClock() {
    if (playbackFrame !== null) {
      cancelAnimationFrame(playbackFrame);
      playbackFrame = null;
    }
    updateCurrentTime();
  }

  function togglePlay() {
    if (!mediaEl) return;
    if (mediaEl.paused) mediaEl.play().catch(() => {});
    else mediaEl.pause();
  }

  function seekRelative(delta: number) {
    if (!mediaEl) return;
    const duration = Number.isFinite(mediaEl.duration) ? mediaEl.duration : asset?.duration ?? 0;
    const nextTime = Math.min(duration, Math.max(0, mediaEl.currentTime + delta));
    mediaEl.currentTime = nextTime;
    updateCurrentTime();
  }

  function seekNextSegment() {
    const segments = asset?.transcript_segments ?? [];
    const next = segments.find((segment) => segmentMediaStart(segment) > currentTime + 0.05);
    if (next) seek(next);
  }

  function seekPreviousSegment() {
    const segments = asset?.transcript_segments ?? [];
    const current = segments.find(
      (segment) => currentTime >= segmentMediaStart(segment) && currentTime < segmentMediaEnd(segment)
    );
    if (current && currentTime - segmentMediaStart(current) > 5) {
      seek(current);
      return;
    }
    const previous = [...segments].reverse().find((segment) => segmentMediaEnd(segment) < currentTime - 0.05);
    if (previous) seek(previous);
    else seekToTime(0);
  }

  function seekPreviousSpeakerSegment() {
    const segments = asset?.transcript_segments ?? [];
    const current = segments.find(
      (segment) => currentTime >= segmentMediaStart(segment) && currentTime < segmentMediaEnd(segment)
    );
    if (!current) return;
    const previous = [...segments]
      .reverse()
      .find((segment) => segment.speaker === current.speaker && segmentMediaEnd(segment) < currentTime - 0.05);
    if (previous) seek(previous);
  }

  function seekNextSpeakerSegment() {
    const segments = asset?.transcript_segments ?? [];
    const current = segments.find(
      (segment) => currentTime >= segmentMediaStart(segment) && currentTime < segmentMediaEnd(segment)
    );
    if (!current) return;
    const next = segments.find(
      (segment) => segment.speaker === current.speaker && segmentMediaStart(segment) > currentTime + 0.05
    );
    if (next) seek(next);
  }

  function handleGlobalKeydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null;
    if (shouldIgnorePlaybackKey(target)) return;

    if (event.code === 'Space') {
      event.preventDefault();
      togglePlay();
    } else if (event.code === 'ArrowRight') {
      event.preventDefault();
      seekRelative(5);
    } else if (event.code === 'ArrowLeft') {
      event.preventDefault();
      seekRelative(-5);
    } else if (event.code === 'Comma') {
      event.preventDefault();
      seekPreviousSegment();
    } else if (event.code === 'Period') {
      event.preventDefault();
      seekNextSegment();
    } else if (event.code === 'BracketLeft') {
      event.preventDefault();
      seekPreviousSpeakerSegment();
    } else if (event.code === 'BracketRight') {
      event.preventDefault();
      seekNextSpeakerSegment();
    } else if (event.code === 'KeyK') {
      event.preventDefault();
      seekToTime(0);
    } else if (event.code === 'KeyM' && mediaEl) {
      event.preventDefault();
      mediaEl.muted = !mediaEl.muted;
    } else if (event.code === 'KeyV') {
      event.preventDefault();
      speakerProgressBar?.centerOnTime(currentTime);
    } else if (event.code === 'KeyW') {
      event.preventDefault();
      speakerProgressBar?.zoomAtTime(currentTime, 0.88);
    } else if (event.code === 'KeyS') {
      event.preventDefault();
      speakerProgressBar?.zoomAtTime(currentTime, 1.12);
    } else if (event.code === 'KeyA') {
      event.preventDefault();
      speakerProgressBar?.panByWindow(-0.12);
    } else if (event.code === 'KeyD') {
      event.preventDefault();
      speakerProgressBar?.panByWindow(0.12);
    }
  }

  function shouldIgnorePlaybackKey(target: HTMLElement | null) {
    if (!target) return false;
    const tagName = target.tagName;
    if (target.isContentEditable || tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT') return true;
    if (tagName === 'BUTTON' && !target.closest('.transcript')) return true;
    return tagName === 'A' || tagName === 'SUMMARY';
  }

  function syncSpeakerDrafts() {
    if (!asset) return;
    for (const row of localSpeakerRows(asset)) {
      if (!(row.localSpeaker in speakerDrafts)) {
        speakerDrafts[row.localSpeaker] = row.displayName;
      }
    }
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
</script>

<main>
  {#if error}<p class="error">{error}</p>{/if}

  {#if asset}
    <AssetHeader {asset} onRetry={retry} onRemove={remove} />

    <ResizableAssetWorkspace>
      <svelte:fragment slot="media">
        <AssetMediaPane
          {asset}
          {audioTracks}
          {selectedAudioTrack}
          {playbackRate}
          {currentTime}
          bind:mediaElement={mediaEl}
          bind:progressBar={speakerProgressBar}
          onTimeUpdate={updateCurrentTime}
          onStartClock={startPlaybackClock}
          onStopClock={stopPlaybackClock}
          onRestoreMediaSeek={restoreMediaSeek}
          onAudioTrackChange={changeAudioTrack}
          onPlaybackRateChange={changePlaybackRate}
          onTimelineSeek={seekToTime}
        />

        {#if asset.media_type === 'video'}
          <VisualEventsStrip
            assetId={asset.id}
            events={asset.visual_events ?? []}
            {currentTime}
            message={visualMessage}
            onDetect={detectVisualEvents}
            onSeek={seekToTime}
          />
        {/if}

        <AssetFoldoutGroup>
          {#if asset.status === 'success'}
            <section class="summary">
              <button on:click={summarize}>Generate summary</button>
              {#if summaryMessage}<p aria-live="polite">{summaryMessage}</p>{/if}
              {#if asset.summary_text}<p>{asset.summary_text}</p>{/if}
              {#if asset.summary_error}<p class="error">{asset.summary_error}</p>{/if}
            </section>
          {/if}
          <AssetDetailsFoldout {asset} />
          {#if asset.exports}
            <AssetDownloadsFoldout assetId={asset.id} assetExports={asset.exports} />
          {/if}
          {#if Object.keys(asset.speaker_centroids ?? {}).length}
            <AssetSpeakersFoldout
              rows={speakerRows}
              bind:speakerDrafts
              {speakerMessage}
              onRecompute={recomputeSpeakers}
              onSave={saveSpeakerName}
            />
          {/if}
          <AssetEventsFoldout events={asset.events ?? []} eventHistory={asset.event_history ?? []} />
        </AssetFoldoutGroup>
      </svelte:fragment>

      <TranscriptPane
        slot="transcript"
        segments={asset.transcript_segments ?? []}
        {currentTime}
        onSeek={seek}
        onEditSpeaker={openSpeakerEditor}
      />
    </ResizableAssetWorkspace>

    {#if speakerEditor}
      <SpeakerEditorPopover
        editor={speakerEditor}
        bind:editorName
        onSave={saveSpeakerName}
        onCancel={() => (speakerEditor = null)}
      />
    {/if}
  {/if}
</main>

<style>
  main {
    box-sizing: border-box;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    gap: 8px;
    height: 100vh;
    overflow: hidden;
    padding: 10px;
  }

  .error {
    color: #9b1c1c;
    margin: 0;
  }

  @media (max-width: 980px) {
    main {
      height: 100vh;
      min-height: 0;
      overflow: hidden;
      padding: 8px;
    }
  }

  @media (max-width: 760px) {
    main {
      height: calc(100vh - 54px);
    }
  }
</style>
