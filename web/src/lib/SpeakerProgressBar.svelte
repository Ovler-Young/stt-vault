<script lang="ts">
  import { createEventDispatcher, onDestroy } from 'svelte';
  import type { TranscriptSegment } from '$lib/api';
  import { formatTime } from '$lib/format';
  import SpeakerTimelineRow from '$lib/SpeakerTimelineRow.svelte';
  import {
    clampTimelineWindow,
    currentTimelineRatio,
    effectiveTimelineDuration,
    eventTimeFromClientX,
    hoverTipX,
    panTimelineWindow,
    segmentAt,
    zoomWindowAround,
    type TimelineHover,
    type TimelineRow,
    type TimelineWindow
  } from '$lib/speakerTimeline';

  export let segments: TranscriptSegment[] = [];
  export let duration: number | null = null;
  export let currentTime = 0;

  const dispatch = createEventDispatcher<{ seek: { time: number } }>();
  const minZoomSize = 0.02;
  const wheelZoomSensitivity = 0.003;
  const wheelPanSensitivity = 0.00125;
  const keyZoomStep = 0.12;
  const keyPanStep = 0.12;

  let hovered: TimelineHover | null = null;
  let zoomStart = 0;
  let zoomEnd = 1;
  let dragStartX = 0;
  let dragStartZoom: TimelineWindow = { start: 0, end: 1 };
  let dragContainerWidth = 1;
  let dragMoved = false;
  let dragActive = false;

  $: effectiveDuration = effectiveTimelineDuration(duration, segments);
  $: zoomSize = Math.max(0.001, zoomEnd - zoomStart);
  $: currentRatio = currentTimelineRatio(currentTime, effectiveDuration);
  $: zoomProgressPercent = Math.min(100, Math.max(0, ((currentRatio - zoomStart) / zoomSize) * 100));
  $: fullProgressPercent = currentRatio * 100;
  $: zoomProgressInWindow = currentRatio >= zoomStart && currentRatio <= zoomEnd;
  $: isZoomed = zoomStart > 0.0001 || zoomEnd < 0.9999;
  $: zoomWindowLeft = zoomStart * 100;
  $: zoomWindowWidth = zoomSize * 100;
  $: zoomWindowLabel = `${formatTime(zoomStart * effectiveDuration)} - ${formatTime(
    zoomEnd * effectiveDuration
  )}`;

  onDestroy(() => {
    stopDrag();
  });

  function eventTime(event: MouseEvent, windowStart: number, windowEnd: number) {
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    return eventTimeFromClientX(
      event.clientX,
      rect.left,
      rect.width,
      windowStart,
      windowEnd,
      effectiveDuration
    );
  }

  function setZoomWindow(start: number, end: number) {
    const nextWindow = clampTimelineWindow(start, end, minZoomSize);
    zoomStart = nextWindow.start;
    zoomEnd = nextWindow.end;
  }

  function resetZoom() {
    zoomStart = 0;
    zoomEnd = 1;
  }

  function zoomAround(center: number, scale: number) {
    const nextWindow = zoomWindowAround({ start: zoomStart, end: zoomEnd }, center, scale, minZoomSize);
    zoomStart = nextWindow.start;
    zoomEnd = nextWindow.end;
  }

  function panWindow(delta: number) {
    const nextWindow = panTimelineWindow({ start: zoomStart, end: zoomEnd }, delta, minZoomSize);
    zoomStart = nextWindow.start;
    zoomEnd = nextWindow.end;
  }

  export function centerOnTime(time: number) {
    if (!effectiveDuration) return;
    const currentSize = zoomEnd - zoomStart;
    const center = Math.min(1, Math.max(0, time / effectiveDuration));
    setZoomWindow(center - currentSize * 0.1, center + currentSize * 0.9);
  }

  export function zoomAtTime(time: number, scale: number) {
    if (!effectiveDuration) return;
    zoomAround(time / effectiveDuration, scale);
  }

  export function panByWindow(delta: number) {
    panWindow(delta * zoomSize);
  }

  function handleClick(event: MouseEvent, windowStart: number, windowEnd: number) {
    if (dragMoved) {
      dragMoved = false;
      return;
    }
    const time = eventTime(event, windowStart, windowEnd);
    const segment = segmentAt(segments, time);
    dispatch('seek', { time: segment?.start ?? time });
  }

  function handleContextMenu(event: MouseEvent, windowStart: number, windowEnd: number) {
    event.preventDefault();
    dispatch('seek', { time: eventTime(event, windowStart, windowEnd) });
  }

  function handleDoubleClick(event: MouseEvent, windowStart: number, windowEnd: number) {
    if (!effectiveDuration) return;
    const time = eventTime(event, windowStart, windowEnd);
    const segment = segmentAt(segments, time);
    const fullStart = segment ? segment.start / effectiveDuration : time / effectiveDuration;
    const fullEnd = segment ? segment.end / effectiveDuration : fullStart;
    const center = (fullStart + fullEnd) / 2;
    const segmentWidth = Math.max(fullEnd - fullStart, 0.015);
    const nextWidth = Math.max(0.02, Math.min(0.35, segmentWidth * 4, zoomSize * 0.55));
    setZoomWindow(center - nextWidth / 2, center + nextWidth / 2);
  }

  function handleWheel(event: WheelEvent, windowStart: number, windowEnd: number) {
    if (!effectiveDuration) return;
    event.preventDefault();
    const ratio = eventTime(event, windowStart, windowEnd) / effectiveDuration;
    if (Math.abs(event.deltaX) > Math.abs(event.deltaY) && isZoomed) {
      panWindow(event.deltaX * wheelPanSensitivity * zoomSize);
      return;
    }
    zoomAround(ratio, 1 + event.deltaY * wheelZoomSensitivity);
  }

  function handleKeydown(event: KeyboardEvent) {
    if (!effectiveDuration) return;
    const currentRatio = Math.min(1, Math.max(0, currentTime / effectiveDuration));
    if (event.code === 'KeyW') {
      event.preventDefault();
      event.stopPropagation();
      zoomAround(currentRatio, 1 - keyZoomStep);
    } else if (event.code === 'KeyS') {
      event.preventDefault();
      event.stopPropagation();
      zoomAround(currentRatio, 1 + keyZoomStep);
    } else if (event.code === 'KeyA') {
      event.preventDefault();
      event.stopPropagation();
      panWindow(-keyPanStep * zoomSize);
    } else if (event.code === 'KeyD') {
      event.preventDefault();
      event.stopPropagation();
      panWindow(keyPanStep * zoomSize);
    } else if (event.code === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      resetZoom();
    }
  }

  function handleMouseDown(event: MouseEvent) {
    if (event.button !== 0 || !isZoomed) return;
    event.preventDefault();
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    dragStartX = event.clientX;
    dragStartZoom = { start: zoomStart, end: zoomEnd };
    dragContainerWidth = Math.max(1, rect.width);
    dragMoved = false;
    dragActive = true;
    if (typeof document !== 'undefined') {
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', handleDocumentMouseMove);
      document.addEventListener('mouseup', handleDocumentMouseUp);
    }
  }

  function handleDocumentMouseMove(event: MouseEvent) {
    if (!dragActive) return;
    const dx = event.clientX - dragStartX;
    if (Math.abs(dx) > 3) dragMoved = true;
    const currentSize = dragStartZoom.end - dragStartZoom.start;
    const delta = -(dx / dragContainerWidth) * currentSize;
    setZoomWindow(dragStartZoom.start + delta, dragStartZoom.end + delta);
  }

  function handleDocumentMouseUp() {
    stopDrag();
  }

  function stopDrag() {
    dragActive = false;
    if (typeof document !== 'undefined') {
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', handleDocumentMouseMove);
      document.removeEventListener('mouseup', handleDocumentMouseUp);
    }
  }

  function handleMouseMove(event: MouseEvent, row: TimelineRow, windowStart: number, windowEnd: number) {
    const time = eventTime(event, windowStart, windowEnd);
    const segment = segmentAt(segments, time);
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    hovered = {
      row,
      x: hoverTipX(event.clientX, rect.left, rect.width),
      time,
      speaker: segment?.speaker_name ?? segment?.speaker ?? null
    };
  }
</script>

{#if effectiveDuration > 0 && segments.length}
  <div class="speaker-progress-stack" class:dragging={dragActive}>
    <SpeakerTimelineRow
      row="zoom"
      {segments}
      {effectiveDuration}
      windowStart={zoomStart}
      windowEnd={zoomEnd}
      {currentTime}
      progressPercent={zoomProgressPercent}
      showProgress={zoomProgressInWindow}
      ariaLabel="Selected speaker timeline window"
      title={zoomWindowLabel}
      {hovered}
      dragging={dragActive}
      onRowClick={(event) => handleClick(event, zoomStart, zoomEnd)}
      onRowContextMenu={(event) => handleContextMenu(event, zoomStart, zoomEnd)}
      onRowKeydown={handleKeydown}
      onRowMouseDown={handleMouseDown}
      onRowMouseMove={(event) => handleMouseMove(event, 'zoom', zoomStart, zoomEnd)}
      onRowMouseLeave={() => (hovered = null)}
      onRowDoubleClick={(event) => handleDoubleClick(event, zoomStart, zoomEnd)}
      onRowWheel={(event) => handleWheel(event, zoomStart, zoomEnd)}
    />

    <SpeakerTimelineRow
      row="full"
      {segments}
      {effectiveDuration}
      windowStart={0}
      windowEnd={1}
      {currentTime}
      progressPercent={fullProgressPercent}
      ariaLabel="Full speaker timeline"
      {hovered}
      dragging={dragActive}
      showZoomWindow={isZoomed}
      {zoomWindowLeft}
      {zoomWindowWidth}
      onRowClick={(event) => handleClick(event, 0, 1)}
      onRowContextMenu={(event) => handleContextMenu(event, 0, 1)}
      onRowKeydown={handleKeydown}
      onRowMouseDown={handleMouseDown}
      onRowMouseMove={(event) => handleMouseMove(event, 'full', 0, 1)}
      onRowMouseLeave={() => (hovered = null)}
      onRowDoubleClick={(event) => handleDoubleClick(event, 0, 1)}
      onRowWheel={(event) => handleWheel(event, 0, 1)}
    />

    {#if isZoomed}
      <button class="zoom-reset" type="button" title={zoomWindowLabel} on:click={resetZoom}>Reset</button>
    {/if}
  </div>
{/if}

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
    border: 1px solid #b9b2a4;
    border-radius: 6px;
    background: #fbfaf7;
    color: #151515;
    padding: 2px 6px;
    font-size: 11px;
    line-height: 1.3;
    cursor: pointer;
  }

  .zoom-reset:hover {
    background: #f0ede5;
  }
</style>
