<script lang="ts">
  import type { TranscriptSegment } from '$lib/api';
  import { formatTime } from '$lib/format';
  import {
    segmentPosition,
    speakerColor,
    type TimelineHover,
    type TimelineRow
  } from '$lib/speakerTimeline';

  export let row: TimelineRow;
  export let segments: TranscriptSegment[] = [];
  export let effectiveDuration = 0;
  export let windowStart = 0;
  export let windowEnd = 1;
  export let currentTime = 0;
  export let progressPercent = 0;
  export let showProgress = true;
  export let ariaLabel = 'Speaker timeline';
  export let title: string | undefined = undefined;
  export let hovered: TimelineHover | null = null;
  export let dragging = false;
  export let showZoomWindow = false;
  export let zoomWindowLeft = 0;
  export let zoomWindowWidth = 100;
  export let onRowClick: (event: MouseEvent) => void = () => {};
  export let onRowContextMenu: (event: MouseEvent) => void = () => {};
  export let onRowKeydown: (event: KeyboardEvent) => void = () => {};
  export let onRowMouseDown: (event: MouseEvent) => void = () => {};
  export let onRowMouseMove: (event: MouseEvent) => void = () => {};
  export let onRowMouseLeave: (event: MouseEvent) => void = () => {};
  export let onRowDoubleClick: (event: MouseEvent) => void = () => {};
  export let onRowWheel: (event: WheelEvent) => void = () => {};

  $: boundedZoomWindowWidth = Math.min(100 - zoomWindowLeft, zoomWindowWidth);
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<div
  class={`speaker-progress-row ${row}-row`}
  class:dragging
  role="slider"
  tabindex="0"
  aria-label={ariaLabel}
  aria-valuemin="0"
  aria-valuemax={Math.round(effectiveDuration)}
  aria-valuenow={Math.round(currentTime)}
  aria-valuetext={formatTime(currentTime)}
  {title}
  on:click={onRowClick}
  on:contextmenu={onRowContextMenu}
  on:keydown={onRowKeydown}
  on:mousedown={onRowMouseDown}
  on:mousemove={onRowMouseMove}
  on:mouseleave={onRowMouseLeave}
  on:dblclick={onRowDoubleClick}
  on:wheel={onRowWheel}
>
  <svg aria-hidden="true">
    {#each segments as segment}
      {@const position = segmentPosition(segment, effectiveDuration, windowStart, windowEnd)}
      {#if segment.end > segment.start && position}
        <rect
          x={`${position.x}%`}
          y="0"
          width={`${Math.min(100 - position.x, position.width)}%`}
          height="100%"
          fill={speakerColor(segment.speaker_name ?? segment.speaker)}
        />
      {/if}
    {/each}
  </svg>

  {#if showProgress}
    <div class="progress-mask" style={`width:${progressPercent}%`}></div>
    <div class="time-marker" style={`left:${progressPercent}%`}></div>
  {/if}

  {#if showZoomWindow}
    <div class="zoom-window" style={`left:${zoomWindowLeft}%; width:${boundedZoomWindowWidth}%`}></div>
  {/if}

  {#if hovered?.row === row}
    <div class="hover-tip" style={`left:${hovered.x}px`}>
      <strong>{hovered.speaker ?? 'Silence'}</strong>
      <span>{formatTime(hovered.time)}</span>
    </div>
  {/if}
</div>

<style>
  .speaker-progress-row {
    position: relative;
    width: 100%;
    height: 22px;
    overflow: visible;
    border: 1px solid var(--color-border-muted);
    border-radius: 6px;
    background: var(--color-timeline);
    cursor: pointer;
  }

  .speaker-progress-row:focus-visible {
    outline: 2px solid var(--color-focus);
    outline-offset: 2px;
  }

  .speaker-progress-row.dragging {
    cursor: grabbing;
  }

  .zoom-row {
    height: 28px;
    background: var(--color-timeline-active);
  }

  .full-row {
    height: 16px;
  }

  svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    border-radius: 5px;
    overflow: hidden;
  }

  rect {
    transition: opacity 120ms ease;
  }

  .speaker-progress-row:hover rect {
    opacity: 0.88;
  }

  .progress-mask {
    position: absolute;
    inset: 0 auto 0 0;
    border-radius: 5px 0 0 5px;
    background: rgb(255 255 255 / 15%);
    pointer-events: none;
    mix-blend-mode: screen;
  }

  .zoom-window {
    position: absolute;
    top: -3px;
    bottom: -3px;
    border: 2px solid var(--color-text);
    border-radius: 6px;
    box-shadow:
      0 0 0 1px rgb(255 255 255 / 72%),
      inset 0 0 0 1px rgb(255 255 255 / 60%);
    pointer-events: none;
  }

  .time-marker {
    position: absolute;
    top: -3px;
    bottom: -3px;
    width: 2px;
    transform: translateX(-1px);
    background: var(--color-text);
    box-shadow: 0 0 0 1px rgb(255 255 255 / 72%);
    pointer-events: none;
  }

  .hover-tip {
    position: absolute;
    bottom: calc(100% + 6px);
    display: flex;
    gap: 6px;
    align-items: center;
    max-width: min(280px, 85%);
    transform: translateX(-50%);
    border: 1px solid var(--color-border-muted);
    border-radius: 6px;
    background: var(--color-surface);
    color: var(--color-text);
    padding: 4px 6px;
    font-size: 11px;
    white-space: nowrap;
    box-shadow: 0 6px 18px var(--shadow-popup);
    pointer-events: none;
  }

  .hover-tip strong {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .hover-tip span {
    color: var(--color-text-muted);
  }
</style>
