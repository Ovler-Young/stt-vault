<script lang="ts">
  import { createEventDispatcher, onDestroy } from 'svelte';
  import type { TranscriptSegment } from '$lib/api';
  import { formatTime } from '$lib/format';

  export let segments: TranscriptSegment[] = [];
  export let duration: number | null = null;
  export let currentTime = 0;

  const dispatch = createEventDispatcher<{ seek: { time: number } }>();
  const minZoomSize = 0.02;
  const wheelZoomSensitivity = 0.003;
  const wheelPanSensitivity = 0.00125;
  const keyZoomStep = 0.12;
  const keyPanStep = 0.12;

  const speakerColors = [
    '#bcab4d',
    '#5f9ea0',
    '#c46f5a',
    '#7f8ccf',
    '#7aa36f',
    '#c17db4',
    '#d18a3a',
    '#5b9f78',
    '#b86464',
    '#6f9bc2',
    '#a68a53',
    '#8f7ac0'
  ];

  type TimelineRow = 'zoom' | 'full';

  let hovered: { row: TimelineRow; x: number; time: number; speaker: string | null } | null = null;
  let zoomStart = 0;
  let zoomEnd = 1;
  let dragStartX = 0;
  let dragStartZoom = { start: 0, end: 1 };
  let dragContainerWidth = 1;
  let dragMoved = false;
  let dragActive = false;

  $: effectiveDuration =
    duration && duration > 0
      ? duration
      : Math.max(0, ...segments.map((segment) => segment.end));
  $: zoomSize = Math.max(0.001, zoomEnd - zoomStart);
  $: currentRatio = effectiveDuration ? Math.min(1, Math.max(0, currentTime / effectiveDuration)) : 0;
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

  function hashSpeaker(speaker: string) {
    let hash = 0;
    for (const char of speaker) {
      hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
    }
    return hash;
  }

  function speakerColor(speaker: string) {
    return speakerColors[hashSpeaker(speaker) % speakerColors.length];
  }

  function eventTime(event: MouseEvent, windowStart: number, windowEnd: number) {
    if (!effectiveDuration) return 0;
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const windowSize = Math.max(0.001, windowEnd - windowStart);
    return (windowStart + ratio * windowSize) * effectiveDuration;
  }

  function segmentAt(time: number) {
    return segments.find((segment) => time >= segment.start && time < segment.end) ?? null;
  }

  function segmentPosition(segment: TranscriptSegment, windowStart: number, windowEnd: number) {
    if (!effectiveDuration) return null;
    const windowSize = Math.max(0.001, windowEnd - windowStart);
    const start = segment.start / effectiveDuration;
    const end = segment.end / effectiveDuration;
    const visibleStart = Math.max(start, windowStart);
    const visibleEnd = Math.min(end, windowEnd);
    if (visibleStart >= visibleEnd) return null;
    return {
      x: ((visibleStart - windowStart) / windowSize) * 100,
      width: Math.max(0.15, ((visibleEnd - visibleStart) / windowSize) * 100)
    };
  }

  function setZoomWindow(start: number, end: number) {
    const nextSize = Math.min(1, Math.max(minZoomSize, end - start));
    let nextStart = Math.min(Math.max(0, start), 1 - nextSize);
    let nextEnd = nextStart + nextSize;
    if (nextEnd > 1) {
      nextEnd = 1;
      nextStart = Math.max(0, 1 - nextSize);
    }
    zoomStart = nextStart;
    zoomEnd = nextEnd;
  }

  function resetZoom() {
    zoomStart = 0;
    zoomEnd = 1;
  }

  function zoomAround(center: number, scale: number) {
    const clampedCenter = Math.min(1, Math.max(0, center));
    const currentSize = zoomEnd - zoomStart;
    const nextSize = Math.min(1, Math.max(minZoomSize, currentSize * scale));
    const centerOffset = currentSize > 0 ? (clampedCenter - zoomStart) / currentSize : 0.5;
    const nextStart = clampedCenter - nextSize * Math.min(1, Math.max(0, centerOffset));
    setZoomWindow(nextStart, nextStart + nextSize);
  }

  function panWindow(delta: number) {
    const currentSize = zoomEnd - zoomStart;
    if (currentSize >= 0.9999) return;
    setZoomWindow(zoomStart + delta, zoomEnd + delta);
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
    const segment = segmentAt(time);
    dispatch('seek', { time: segment?.start ?? time });
  }

  function handleContextMenu(event: MouseEvent, windowStart: number, windowEnd: number) {
    event.preventDefault();
    dispatch('seek', { time: eventTime(event, windowStart, windowEnd) });
  }

  function handleDoubleClick(event: MouseEvent) {
    if (!effectiveDuration) return;
    const time = eventTime(event, 0, 1);
    const segment = segmentAt(time);
    const fullStart = segment ? segment.start / effectiveDuration : time / effectiveDuration;
    const fullEnd = segment ? segment.end / effectiveDuration : fullStart;
    const center = (fullStart + fullEnd) / 2;
    const segmentWidth = Math.max(fullEnd - fullStart, 0.015);
    const nextWidth = Math.max(0.02, Math.min(0.35, segmentWidth * 4, zoomSize * 0.55));
    setZoomWindow(center - nextWidth / 2, center + nextWidth / 2);
  }

  function handleWheel(event: WheelEvent) {
    if (!effectiveDuration) return;
    event.preventDefault();
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
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
    const segment = segmentAt(time);
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    hovered = {
      row,
      x: Math.min(rect.width - 44, Math.max(44, event.clientX - rect.left)),
      time,
      speaker: segment?.speaker_name ?? segment?.speaker ?? null
    };
  }
</script>

{#if effectiveDuration > 0 && segments.length}
  <div class="speaker-progress-stack" class:dragging={dragActive}>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div
      class="speaker-progress-row zoom-row"
      role="slider"
      tabindex="0"
      aria-label="Selected speaker timeline window"
      aria-valuemin="0"
      aria-valuemax={Math.round(effectiveDuration)}
      aria-valuenow={Math.round(currentTime)}
      aria-valuetext={formatTime(currentTime)}
      on:click={(event) => handleClick(event, zoomStart, zoomEnd)}
      on:contextmenu={(event) => handleContextMenu(event, zoomStart, zoomEnd)}
      on:keydown={handleKeydown}
      on:mousedown={handleMouseDown}
      on:mousemove={(event) => handleMouseMove(event, 'zoom', zoomStart, zoomEnd)}
      on:mouseleave={() => (hovered = null)}
      title={zoomWindowLabel}
    >
      <svg aria-hidden="true">
        {#each segments as segment}
          {@const position = segmentPosition(segment, zoomStart, zoomEnd)}
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

      {#if zoomProgressInWindow}
        <div class="progress-mask" style={`width:${zoomProgressPercent}%`}></div>
        <div class="time-marker" style={`left:${zoomProgressPercent}%`}></div>
      {/if}

      {#if hovered?.row === 'zoom'}
        <div class="hover-tip" style={`left:${hovered.x}px`}>
          <strong>{hovered.speaker ?? 'Silence'}</strong>
          <span>{formatTime(hovered.time)}</span>
        </div>
      {/if}
    </div>

    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div
      class="speaker-progress-row full-row"
      role="slider"
      tabindex="0"
      aria-label="Full speaker timeline"
      aria-valuemin="0"
      aria-valuemax={Math.round(effectiveDuration)}
      aria-valuenow={Math.round(currentTime)}
      aria-valuetext={formatTime(currentTime)}
      on:click={(event) => handleClick(event, 0, 1)}
      on:dblclick={handleDoubleClick}
      on:contextmenu={(event) => handleContextMenu(event, 0, 1)}
      on:keydown={handleKeydown}
      on:mousedown={handleMouseDown}
      on:mousemove={(event) => handleMouseMove(event, 'full', 0, 1)}
      on:mouseleave={() => (hovered = null)}
      on:wheel={handleWheel}
    >
      <svg aria-hidden="true">
        {#each segments as segment}
          {@const position = segmentPosition(segment, 0, 1)}
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

      <div class="progress-mask" style={`width:${fullProgressPercent}%`}></div>
      <div class="time-marker" style={`left:${fullProgressPercent}%`}></div>

      {#if isZoomed}
        <div
          class="zoom-window"
          style={`left:${zoomWindowLeft}%; width:${Math.min(100 - zoomWindowLeft, zoomWindowWidth)}%`}
        ></div>
      {/if}

      {#if hovered?.row === 'full'}
        <div class="hover-tip" style={`left:${hovered.x}px`}>
          <strong>{hovered.speaker ?? 'Silence'}</strong>
          <span>{formatTime(hovered.time)}</span>
        </div>
      {/if}
    </div>

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

  .speaker-progress-row {
    position: relative;
    width: 100%;
    height: 22px;
    overflow: visible;
    border: 1px solid #b9b2a4;
    border-radius: 6px;
    background: #595959;
    cursor: pointer;
  }

  .speaker-progress-row:focus-visible {
    outline: 2px solid #3b7dd8;
    outline-offset: 2px;
  }

  .speaker-progress-stack.dragging .speaker-progress-row {
    cursor: grabbing;
  }

  .zoom-row {
    height: 28px;
    background: #4f4f4f;
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
    border: 2px solid #151515;
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
    background: #151515;
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
    border: 1px solid #b9b2a4;
    border-radius: 6px;
    background: #fbfaf7;
    color: #151515;
    padding: 4px 6px;
    font-size: 11px;
    white-space: nowrap;
    box-shadow: 0 6px 18px rgb(0 0 0 / 16%);
    pointer-events: none;
  }

  .hover-tip strong {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .hover-tip span {
    color: #666052;
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
