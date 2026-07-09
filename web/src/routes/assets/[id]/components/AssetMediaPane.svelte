<script lang="ts">
  import SpeakerProgressBar from '$lib/SpeakerProgressBar.svelte';
  import type { AudioTrack, AssetDetail } from '$lib/api';
  import { audioTrackLabel, mediaUrl } from '../asset-page.helpers';
  import type { MaybePromise, SpeakerProgressBarHandle } from '../asset-page.types';

  export let asset: AssetDetail;
  export let audioTracks: AudioTrack[] = [];
  export let selectedAudioTrack = 'default';
  export let playbackRate = 1;
  export let currentTime = 0;
  export let mediaElement: HTMLMediaElement | null = null;
  export let progressBar: SpeakerProgressBarHandle | null = null;
  export let onTimeUpdate: () => void = () => {};
  export let onStartClock: () => void = () => {};
  export let onStopClock: () => void = () => {};
  export let onRestoreMediaSeek: () => void = () => {};
  export let onAudioTrackChange: (track: string) => MaybePromise = () => {};
  export let onPlaybackRateChange: (rate: number) => void = () => {};
  export let onTimelineSeek: (time: number) => void = () => {};

  function handleAudioTrackChange(event: Event) {
    void onAudioTrackChange((event.currentTarget as HTMLSelectElement).value);
  }

  function handlePlaybackRateChange(event: Event) {
    onPlaybackRateChange(Number((event.currentTarget as HTMLSelectElement).value));
  }

  function handleTimelineSeek(event: CustomEvent<{ time: number }>) {
    onTimelineSeek(event.detail.time);
  }
</script>

<video
  bind:this={mediaElement}
  controls
  src={mediaUrl(asset.id, selectedAudioTrack)}
  on:timeupdate={onTimeUpdate}
  on:seeked={onTimeUpdate}
  on:play={onTimeUpdate}
  on:playing={onStartClock}
  on:pause={onStopClock}
  on:ended={onStopClock}
  on:loadedmetadata={onRestoreMediaSeek}
>
  <track
    kind="captions"
    src={asset.exports?.vtt ? `/api/assets/${asset.id}/exports/vtt` : ''}
    srclang="en"
    label="Transcript"
    default
  />
</video>

<div class="playback-controls">
  {#if audioTracks.length > 1}
    <label>
      <span>Audio</span>
      <select value={selectedAudioTrack} on:change={handleAudioTrackChange}>
        <option value="default">Original playback</option>
        <option value="all">All tracks mixed</option>
        {#each audioTracks as track}
          <option value={`${track.audio_index}`}>{audioTrackLabel(track)}</option>
        {/each}
      </select>
    </label>
  {/if}

  <label>
    <span>Speed</span>
    <select value={playbackRate} on:change={handlePlaybackRateChange}>
      <option value={0.75}>0.75x</option>
      <option value={1}>1x</option>
      <option value={1.25}>1.25x</option>
      <option value={1.5}>1.5x</option>
      <option value={2}>2x</option>
    </select>
  </label>
</div>

<SpeakerProgressBar
  bind:this={progressBar}
  segments={asset.transcript_segments ?? []}
  duration={asset.duration}
  {currentTime}
  on:seek={handleTimelineSeek}
/>

<style>
  video {
    width: 100%;
    max-height: 58vh;
    background: #111;
    border-radius: 8px;
  }

  .playback-controls {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 6px;
    color: #666052;
    font-size: 12px;
  }

  .playback-controls label {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }

  .playback-controls select {
    min-width: 0;
    max-width: 100%;
    border: 1px solid #c7c1b4;
    border-radius: 6px;
    background: white;
    color: #151515;
    font: inherit;
    padding: 5px 8px;
  }
</style>
