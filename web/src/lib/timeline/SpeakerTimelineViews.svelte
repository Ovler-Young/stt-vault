<script lang="ts">
  import type { TranscriptSegment } from "$lib/api/types";
  import SpeakerTimelineRow from "$lib/timeline/SpeakerTimelineRow.svelte";
  import type {
    TimelineHover,
    TimelineWindow,
  } from "$lib/timeline/speakerTimeline";

  export let segments: TranscriptSegment[] = [];
  export let effectiveDuration = 0;
  export let zoomWindow: TimelineWindow;
  export let fullTimelineWindow: TimelineWindow;
  export let currentTime = 0;
  export let zoomProgressPercent = 0;
  export let fullProgressPercent = 0;
  export let zoomProgressInWindow = false;
  export let isZoomed = false;
  export let zoomWindowLeft = 0;
  export let zoomWindowWidth = 100;
  export let zoomWindowLabel = "";
  export let hovered: TimelineHover | null = null;
  export let dragging = false;
  export let onClick: (
    event: MouseEvent,
    window: TimelineWindow,
  ) => void = () => {};
  export let onContextMenu: (
    event: MouseEvent,
    window: TimelineWindow,
  ) => void = () => {};
  export let onKeydown: (event: KeyboardEvent) => void = () => {};
  export let onMouseDown: (event: MouseEvent) => void = () => {};
  export let onMouseMove: (
    event: MouseEvent,
    row: "zoom" | "full",
    window: TimelineWindow,
  ) => void = () => {};
  export let onMouseLeave: () => void = () => {};
  export let onDoubleClick: (
    event: MouseEvent,
    window: TimelineWindow,
  ) => void = () => {};
  export let onWheel: (
    event: WheelEvent,
    window: TimelineWindow,
  ) => void = () => {};
  export let onReset: () => void = () => {};
</script>

<div class="speaker-progress-stack" class:dragging>
  <SpeakerTimelineRow
    row="zoom"
    {segments}
    {effectiveDuration}
    windowStart={zoomWindow.start}
    windowEnd={zoomWindow.end}
    {currentTime}
    progressPercent={zoomProgressPercent}
    showProgress={zoomProgressInWindow}
    ariaLabel="Selected speaker timeline window"
    title={zoomWindowLabel}
    {hovered}
    {dragging}
    onRowClick={(event) => onClick(event, zoomWindow)}
    onRowContextMenu={(event) => onContextMenu(event, zoomWindow)}
    onRowKeydown={onKeydown}
    onRowMouseDown={onMouseDown}
    onRowMouseMove={(event) => onMouseMove(event, "zoom", zoomWindow)}
    onRowMouseLeave={onMouseLeave}
    onRowDoubleClick={(event) => onDoubleClick(event, zoomWindow)}
    onRowWheel={(event) => onWheel(event, zoomWindow)}
  />

  <SpeakerTimelineRow
    row="full"
    {segments}
    {effectiveDuration}
    windowStart={fullTimelineWindow.start}
    windowEnd={fullTimelineWindow.end}
    {currentTime}
    progressPercent={fullProgressPercent}
    ariaLabel="Full speaker timeline"
    {hovered}
    {dragging}
    showZoomWindow={isZoomed}
    {zoomWindowLeft}
    {zoomWindowWidth}
    onRowClick={(event) => onClick(event, fullTimelineWindow)}
    onRowContextMenu={(event) => onContextMenu(event, fullTimelineWindow)}
    onRowKeydown={onKeydown}
    onRowMouseDown={onMouseDown}
    onRowMouseMove={(event) => onMouseMove(event, "full", fullTimelineWindow)}
    onRowMouseLeave={onMouseLeave}
    onRowDoubleClick={(event) => onDoubleClick(event, fullTimelineWindow)}
    onRowWheel={(event) => onWheel(event, fullTimelineWindow)}
  />

  {#if isZoomed}
    <button
      class="zoom-reset"
      type="button"
      title={zoomWindowLabel}
      on:click={onReset}
    >
      Reset
    </button>
  {/if}
</div>

<style>
  .speaker-progress-stack {
    display: grid;
    gap: 5px;
    position: relative;
    width: 100%;
    margin-top: 6px;
    overflow: visible;
    user-select: none;
  }

  .zoom-reset {
    justify-self: end;
    border: 1px solid var(--color-border-muted);
    border-radius: 6px;
    background: var(--color-surface);
    color: var(--color-text);
    padding: 2px 6px;
    font-size: 11px;
    line-height: 1.3;
    cursor: pointer;
  }

  .zoom-reset:hover {
    background: var(--color-surface-muted);
  }
</style>
