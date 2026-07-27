<script lang="ts">
  import type {
    AudioTrack,
    AssetDetail,
    JobEvent,
    TranscriptSegment,
  } from "$lib/api-types";
  import type {
    MaybePromise,
    SpeakerControlsHandle,
    SpeakerProgressBarHandle,
  } from "../asset-page.types";
  import AssetDetailsFoldout from "./AssetDetailsFoldout.svelte";
  import AssetDownloadsFoldout from "./AssetDownloadsFoldout.svelte";
  import AssetEventsFoldout from "./AssetEventsFoldout.svelte";
  import AssetFoldoutGroup from "./AssetFoldoutGroup.svelte";
  import AssetHeader from "./AssetHeader.svelte";
  import AssetMediaPane from "./AssetMediaPane.svelte";
  import AssetSpeakerControls from "./AssetSpeakerControls.svelte";
  import ResizableAssetWorkspace from "./ResizableAssetWorkspace.svelte";
  import AssetSummaryFoldout from "./AssetSummaryFoldout.svelte";
  import TranscriptPane from "./TranscriptPane.svelte";
  import VisualEventsStrip from "./VisualEventsStrip.svelte";

  type AssetPageShellProps = {
    asset: AssetDetail | null;
    eventHistory?: JobEvent[];
    error: string;
    audioTracks: AudioTrack[];
    selectedAudioTrack: string;
    playbackRate: number;
    currentTime: number;
    visualMessage: string;
    speakerMatchMessage: string;
    mediaElement?: HTMLMediaElement | null;
    progressBar?: SpeakerProgressBarHandle | null;
    speakerControls?: SpeakerControlsHandle | null;
    onRetry: () => MaybePromise;
    onRemove: () => MaybePromise;
    onTimeUpdate: () => void;
    onStartClock: () => void;
    onStopClock: () => void;
    onRestoreMediaSeek: () => void;
    onAudioTrackChange: (track: string) => MaybePromise;
    onPlaybackRateChange: (rate: number) => void;
    onTimelineSeek: (time: number) => void;
    onDetectVisualEvents: () => MaybePromise;
    onLoad: () => Promise<void>;
    onError: (message: string) => void;
    onTranscriptSeek: (segment: TranscriptSegment) => void;
    onEditSpeaker: (event: MouseEvent, segment: TranscriptSegment) => void;
  };

  let {
    asset,
    eventHistory = [],
    error,
    audioTracks,
    selectedAudioTrack,
    playbackRate,
    currentTime,
    visualMessage,
    speakerMatchMessage,
    mediaElement = $bindable(null),
    progressBar = $bindable(null),
    speakerControls = $bindable(null),
    onRetry,
    onRemove,
    onTimeUpdate,
    onStartClock,
    onStopClock,
    onRestoreMediaSeek,
    onAudioTrackChange,
    onPlaybackRateChange,
    onTimelineSeek,
    onDetectVisualEvents,
    onLoad,
    onError,
    onTranscriptSeek,
    onEditSpeaker,
  }: AssetPageShellProps = $props();
</script>

<main>
  {#if error}<p class="error" aria-live="polite">{error}</p>{/if}

  {#if asset}
    <AssetHeader {asset} {onRetry} {onRemove} />

    <ResizableAssetWorkspace>
      <svelte:fragment slot="media">
        <AssetMediaPane
          {asset}
          {audioTracks}
          {selectedAudioTrack}
          {playbackRate}
          {currentTime}
          bind:mediaElement
          bind:progressBar
          {onTimeUpdate}
          {onStartClock}
          {onStopClock}
          {onRestoreMediaSeek}
          {onAudioTrackChange}
          {onPlaybackRateChange}
          {onTimelineSeek}
        />

        {#if asset.media_type === "video"}
          <VisualEventsStrip
            assetId={asset.id}
            events={asset.visual_events ?? []}
            {currentTime}
            message={visualMessage}
            onDetect={onDetectVisualEvents}
            onSeek={onTimelineSeek}
          />
        {/if}

        <AssetFoldoutGroup>
          {#if asset.status === "success"}
            <AssetSummaryFoldout
              {asset}
              onUpdated={onLoad}
              onSeek={onTimelineSeek}
            />
          {/if}
          <AssetDetailsFoldout {asset} />
          {#if asset.exports}
            <AssetDownloadsFoldout
              assetId={asset.id}
              assetExports={asset.exports}
            />
          {/if}
          <AssetSpeakerControls
            bind:this={speakerControls}
            {asset}
            initialMessage={speakerMatchMessage}
            onReload={onLoad}
            {onError}
          />
          <AssetEventsFoldout events={asset.events ?? []} {eventHistory} />
        </AssetFoldoutGroup>
      </svelte:fragment>

      <TranscriptPane
        slot="transcript"
        segments={asset.transcript_segments ?? []}
        {currentTime}
        onSeek={onTranscriptSeek}
        {onEditSpeaker}
      />
    </ResizableAssetWorkspace>
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
    color: var(--color-danger);
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
