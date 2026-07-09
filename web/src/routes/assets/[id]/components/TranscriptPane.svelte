<script lang="ts">
  import type { TranscriptSegment } from '$lib/api';
  import { formatTime } from '$lib/format';
  import { activeTranscriptSegmentIndex, segmentMediaEnd, segmentMediaStart } from '../asset-page.helpers';

  export let segments: TranscriptSegment[] = [];
  export let currentTime = 0;
  export let onSeek: (segment: TranscriptSegment) => void = () => {};
  export let onEditSpeaker: (event: MouseEvent, segment: TranscriptSegment) => void = () => {};

  let transcriptEl: HTMLElement | null = null;
  let lastScrolledTranscriptIndex = -1;

  $: activeSegmentIndex = activeTranscriptSegmentIndex(segments, currentTime);
  $: scrollActiveTranscriptIntoView(activeSegmentIndex);

  function isActive(segment: TranscriptSegment) {
    return currentTime >= segmentMediaStart(segment) && currentTime < segmentMediaEnd(segment);
  }

  function scrollActiveTranscriptIntoView(index: number) {
    if (!transcriptEl || index < 0 || index === lastScrolledTranscriptIndex) return;
    lastScrolledTranscriptIndex = index;
    requestAnimationFrame(() => {
      const item = transcriptEl?.querySelector<HTMLElement>(`[data-segment-index="${index}"]`);
      if (!transcriptEl || !item) return;
      const itemTop = item.offsetTop;
      const itemBottom = itemTop + item.offsetHeight;
      const viewTop = transcriptEl.scrollTop;
      const viewBottom = viewTop + transcriptEl.clientHeight;
      if (itemTop >= viewTop + 24 && itemBottom <= viewBottom - 24) return;
      transcriptEl.scrollTo({
        top: Math.max(0, itemTop - transcriptEl.clientHeight * 0.35),
        behavior: 'smooth'
      });
    });
  }
</script>

<article class="transcript" bind:this={transcriptEl}>
  {#if segments.length}
    {#each segments as segment, index}
      <button
        data-segment-index={index}
        class:active={isActive(segment)}
        on:click={() => onSeek(segment)}
        on:contextmenu={(event) => onEditSpeaker(event, segment)}
      >
        <span class="row-head">
          <strong>{segment.speaker_name ?? segment.speaker}</strong>
          <small>{formatTime(segmentMediaStart(segment))} - {formatTime(segmentMediaEnd(segment))}</small>
        </span>
        <span class="text">{segment.text}</span>
      </button>
    {/each}
  {:else}
    <p class="muted">Completed chunks will appear here during processing.</p>
  {/if}
</article>

<style>
  .transcript {
    box-sizing: border-box;
    display: grid;
    align-content: start;
    gap: 4px;
    min-width: 0;
    min-height: 0;
    max-height: 100%;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding-left: 8px;
    padding-right: 4px;
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

  .row-head small,
  .muted {
    color: #666052;
    font-size: 11px;
  }

  .row-head small {
    white-space: nowrap;
  }

  .text {
    line-height: 1.35;
    font-size: 13px;
  }

  .muted {
    margin: 0;
  }

  @media (max-width: 980px) {
    .transcript {
      max-height: 100%;
      overflow-y: auto;
      padding-left: 0;
    }
  }
</style>
