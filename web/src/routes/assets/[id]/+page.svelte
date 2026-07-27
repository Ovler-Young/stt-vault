<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";
  import { page } from "$app/stores";
  import {
    type AudioTrack,
    type AssetDetail,
    type TranscriptSegment,
  } from "$lib/api-types";
  import {
    deleteAsset,
    detectAssetVisualEvents,
    fetchAssetAudioTracks,
    retryAsset,
  } from "$lib/api-endpoints";
  import { loadAssetWithSpeakerMatching } from "./asset-load.controller";
  import { segmentMediaStart } from "./asset-page.helpers";
  import { needsActivePolling } from "$lib/polling";
  import {
    adjacentSpeakerSegment,
    boundedSeekTime,
    nextSegment,
    previousSegment,
  } from "./asset-playback.controller";
  import type {
    SpeakerControlsHandle,
    SpeakerProgressBarHandle,
  } from "./asset-page.types";
  import AssetPageShell from "./components/AssetPageShell.svelte";

  let asset: AssetDetail | null = null;
  let error = "";
  let mediaEl: HTMLMediaElement | null = null;
  let speakerProgressBar: SpeakerProgressBarHandle | null = null;
  let currentTime = 0;
  let poll: ReturnType<typeof setInterval> | null = null;
  let visualMessage = "";
  let speakerMatchMessage = "";
  let speakerControls: SpeakerControlsHandle | null = null;
  let audioTracks: AudioTrack[] = [];
  let audioTracksAssetId = "";
  let selectedAudioTrack = "default";
  let playbackRate = 1;
  let pendingMediaSeek: number | null = null;
  let playbackFrame: number | null = null;
  let autoMatchedAssetId = "";

  $: assetId = $page.params.id ?? "";

  onMount(async () => {
    playbackRate =
      Number(localStorage.getItem("stt-vault-playback-rate") ?? 1) || 1;
    document.addEventListener("keydown", handleGlobalKeydown);
    await load();
  });

  onDestroy(() => {
    document.removeEventListener("keydown", handleGlobalKeydown);
    if (poll) clearInterval(poll);
    stopPlaybackClock();
  });

  async function load() {
    try {
      if (!assetId) return;
      const loaded = await loadAssetWithSpeakerMatching(
        assetId,
        autoMatchedAssetId,
      );
      asset = loaded.asset;
      autoMatchedAssetId = loaded.autoMatchedAssetId;
      speakerMatchMessage = loaded.speakerMatchError ?? "";
      await loadAudioTracks(asset.id);
      updatePolling();
      error = "";
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  function updatePolling() {
    const shouldPoll = asset ? needsActivePolling(asset) : false;
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
    location.href = "/";
  }

  function seek(
    segment: Pick<
      TranscriptSegment,
      "start" | "end" | "chunk_start" | "chunk_end"
    >,
  ) {
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
    localStorage.setItem("stt-vault-playback-rate", String(playbackRate));
    applyPlaybackRate();
  }

  async function loadAudioTracks(nextAssetId: string) {
    if (audioTracksAssetId === nextAssetId) return;
    try {
      audioTracks = await fetchAssetAudioTracks(nextAssetId);
      audioTracksAssetId = nextAssetId;
      selectedAudioTrack = "default";
    } catch {
      audioTracks = [];
      audioTracksAssetId = nextAssetId;
      selectedAudioTrack = "default";
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
    const duration = Number.isFinite(mediaEl.duration)
      ? mediaEl.duration
      : (asset?.duration ?? 0);
    const nextTime = boundedSeekTime(mediaEl.currentTime, duration, delta);
    mediaEl.currentTime = nextTime;
    updateCurrentTime();
  }

  function seekNextSegment() {
    const next = nextSegment(asset?.transcript_segments ?? [], currentTime);
    if (next) seek(next);
  }

  function seekPreviousSegment() {
    const previous = previousSegment(
      asset?.transcript_segments ?? [],
      currentTime,
    );
    if (previous) seek(previous);
    else seekToTime(0);
  }

  function seekPreviousSpeakerSegment() {
    const previous = adjacentSpeakerSegment(
      asset?.transcript_segments ?? [],
      currentTime,
      "previous",
    );
    if (previous) seek(previous);
  }

  function seekNextSpeakerSegment() {
    const next = adjacentSpeakerSegment(
      asset?.transcript_segments ?? [],
      currentTime,
      "next",
    );
    if (next) seek(next);
  }

  function handleGlobalKeydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null;
    if (shouldIgnorePlaybackKey(target)) return;

    if (event.code === "Space") {
      event.preventDefault();
      togglePlay();
    } else if (event.code === "ArrowRight") {
      event.preventDefault();
      seekRelative(5);
    } else if (event.code === "ArrowLeft") {
      event.preventDefault();
      seekRelative(-5);
    } else if (event.code === "Comma") {
      event.preventDefault();
      seekPreviousSegment();
    } else if (event.code === "Period") {
      event.preventDefault();
      seekNextSegment();
    } else if (event.code === "BracketLeft") {
      event.preventDefault();
      seekPreviousSpeakerSegment();
    } else if (event.code === "BracketRight") {
      event.preventDefault();
      seekNextSpeakerSegment();
    } else if (event.code === "KeyK") {
      event.preventDefault();
      seekToTime(0);
    } else if (event.code === "KeyM" && mediaEl) {
      event.preventDefault();
      mediaEl.muted = !mediaEl.muted;
    } else if (event.code === "KeyV") {
      event.preventDefault();
      speakerProgressBar?.centerOnTime(currentTime);
    } else if (event.code === "KeyW") {
      event.preventDefault();
      speakerProgressBar?.zoomAtTime(currentTime, 0.88);
    } else if (event.code === "KeyS") {
      event.preventDefault();
      speakerProgressBar?.zoomAtTime(currentTime, 1.12);
    } else if (event.code === "KeyA") {
      event.preventDefault();
      speakerProgressBar?.panByWindow(-0.12);
    } else if (event.code === "KeyD") {
      event.preventDefault();
      speakerProgressBar?.panByWindow(0.12);
    }
  }

  function shouldIgnorePlaybackKey(target: HTMLElement | null) {
    if (!target) return false;
    const tagName = target.tagName;
    if (
      target.isContentEditable ||
      tagName === "INPUT" ||
      tagName === "TEXTAREA" ||
      tagName === "SELECT"
    )
      return true;
    if (tagName === "BUTTON" && !target.closest(".transcript")) return true;
    return tagName === "A" || tagName === "SUMMARY";
  }

  async function detectVisualEvents() {
    if (!asset) return;
    try {
      const result = await detectAssetVisualEvents(asset.id);
      visualMessage = `${result.events} slide frames detected`;
      await load();
    } catch (err) {
      visualMessage = "";
      error = err instanceof Error ? err.message : String(err);
    }
  }
</script>

<AssetPageShell
  {asset}
  {error}
  {audioTracks}
  {selectedAudioTrack}
  {playbackRate}
  {currentTime}
  {visualMessage}
  {speakerMatchMessage}
  bind:mediaElement={mediaEl}
  bind:progressBar={speakerProgressBar}
  bind:speakerControls
  onRetry={retry}
  onRemove={remove}
  onTimeUpdate={updateCurrentTime}
  onStartClock={startPlaybackClock}
  onStopClock={stopPlaybackClock}
  onRestoreMediaSeek={restoreMediaSeek}
  onAudioTrackChange={changeAudioTrack}
  onPlaybackRateChange={changePlaybackRate}
  onTimelineSeek={seekToTime}
  onDetectVisualEvents={detectVisualEvents}
  onLoad={load}
  onError={(message) => (error = message)}
  onTranscriptSeek={seek}
  onEditSpeaker={(event, segment) =>
    speakerControls?.editSpeaker(event, segment)}
/>
