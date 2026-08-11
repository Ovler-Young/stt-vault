<script lang="ts">
  import type { TimedTranscriptUnit, TranscriptSegment } from "$lib/api/types";
  import { formatTime } from "$lib/formatting/date-time";
  import {
    activeTranscriptSegmentIndex,
    segmentMediaEnd,
    segmentMediaStart,
  } from "../asset-page.helpers";
  import { activeTimedTranscriptUnitIndex } from "../asset-playback.controller";

  export let segments: TranscriptSegment[] = [];
  export let currentTime = 0;
  export let playbackEnded = false;
  export let onSeek: (segment: TranscriptSegment) => void = () => {};
  export let onTimedUnitSeek: (unit: TimedTranscriptUnit) => void = () => {};
  export let onEditSpeaker: (
    event: MouseEvent,
    segment: TranscriptSegment,
  ) => void = () => {};

  let transcriptEl: HTMLElement | null = null;
  let lastScrolledTranscriptIndex = -1;

  $: activeSegmentIndex = activeTranscriptSegmentIndex(segments, currentTime);
  $: scrollActiveTranscriptIntoView(activeSegmentIndex);

  function isActive(segment: TranscriptSegment) {
    return (
      currentTime >= segmentMediaStart(segment) &&
      currentTime < segmentMediaEnd(segment)
    );
  }

  function activeTimedUnitIndex(
    units: TimedTranscriptUnit[],
    time: number,
    ended: boolean,
  ) {
    return activeTimedTranscriptUnitIndex(units, time, ended);
  }

  function scrollActiveTranscriptIntoView(index: number) {
    if (!transcriptEl || index < 0 || index === lastScrolledTranscriptIndex)
      return;
    lastScrolledTranscriptIndex = index;
    requestAnimationFrame(() => {
      const item = transcriptEl?.querySelector<HTMLElement>(
        `[data-segment-index="${index}"]`,
      );
      if (!transcriptEl || !item) return;
      const itemTop = item.offsetTop;
      const itemBottom = itemTop + item.offsetHeight;
      const viewTop = transcriptEl.scrollTop;
      const viewBottom = viewTop + transcriptEl.clientHeight;
      if (itemTop >= viewTop + 24 && itemBottom <= viewBottom - 24) return;
      transcriptEl.scrollTo({
        top: Math.max(0, itemTop - transcriptEl.clientHeight * 0.35),
        behavior: "smooth",
      });
    });
  }
</script>

<article class="transcript" bind:this={transcriptEl}>
  {#if segments.length}
    {#each segments as segment, index}
      {#if segment.timed_units?.length}
        {@const activeUnitIndex = activeTimedUnitIndex(
          segment.timed_units,
          currentTime,
          playbackEnded,
        )}
        <section data-segment-index={index} class="timed-segment">
          <span class="row-head">
            <strong>{segment.speaker_name ?? segment.speaker}</strong>
            <small
              >{formatTime(segmentMediaStart(segment))} - {formatTime(
                segmentMediaEnd(segment),
              )}</small
            >
          </span>
          <span class="timed-units" data-timed-unit-controls>
            {#each segment.timed_units as unit, unitPosition}
              <button
                data-timed-unit-control
                data-unit-index={unit.unit_index}
                class:active={unitPosition === activeUnitIndex}
                aria-label={`Seek to ${formatTime(unit.start_ms / 1000)}: ${unit.text}`}
                on:click={() => onTimedUnitSeek(unit)}>{unit.text}</button
              >
            {/each}
          </span>
        </section>
      {:else}
        <button
          data-segment-index={index}
          class:active={isActive(segment)}
          on:click={() => onSeek(segment)}
          on:contextmenu={(event) => onEditSpeaker(event, segment)}
        >
          <span class="row-head">
            <strong>{segment.speaker_name ?? segment.speaker}</strong>
            <small
              >{formatTime(segmentMediaStart(segment))} - {formatTime(
                segmentMediaEnd(segment),
              )}</small
            >
          </span>
          <span class="text">{segment.text}</span>
        </button>
      {/if}
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
    background: var(--color-surface-strong);
  }

  .timed-segment {
    display: grid;
    gap: 3px;
    width: 100%;
    min-width: 0;
    padding: 6px 8px;
    border: 1px solid var(--color-border-strong);
    border-radius: 6px;
    background: var(--color-surface-strong);
  }

  .timed-units {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0;
    min-width: 0;
    max-width: 100%;
  }

  .transcript .timed-units button {
    display: inline;
    width: auto;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    line-height: 1.35;
    min-width: 0;
    overflow-wrap: anywhere;
    word-break: break-word;
  }

  .transcript .timed-units button.active {
    background: var(--color-accent-surface);
    color: var(--color-accent);
  }

  .transcript button.active {
    border-color: var(--color-accent);
    background: var(--color-accent-surface);
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
    color: var(--color-text-muted);
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
