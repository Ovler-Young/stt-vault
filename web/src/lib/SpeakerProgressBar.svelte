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

  let hovered: { x: number; time: number; speaker: string | null } | null = null;
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
  $: progressPercent = effectiveDuration
    ? Math.min(100, Math.max(0, ((currentTime / effectiveDuration - zoomStart) / zoomSize) * 100))
    : 0;
  $: progressInWindow = effectiveDuration
    ? currentTime / effectiveDuration >= zoomStart && currentTime / effectiveDuration <= zoomEnd
    : false;
  $: isZoomed = zoomStart > 0.0001 || zoomEnd < 0.9999;

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

  function eventTime(event: MouseEvent) {
    if (!effectiveDuration) return 0;
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    return (zoomStart + ratio * zoomSize) * effectiveDuration;
  }

  function segmentAt(time: number) {
    return segments.find((segment) => time >= segment.start && time < segment.end) ?? null;
  }

  function segmentPosition(segment: TranscriptSegment) {
    if (!effectiveDuration) return null;
    const start = segment.start / effectiveDuration;
    const end = segment.end / effectiveDuration;
    const visibleStart = Math.max(start, zoomStart);
    const visibleEnd = Math.min(end, zoomEnd);
    if (visibleStart >= visibleEnd) return null;
    return {
      x: ((visibleStart - zoomStart) / zoomSize) * 100,
      width: Math.max(0.15, ((visibleEnd - visibleStart) / zoomSize) * 100)
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

  function handleClick(event: MouseEvent) {
    if (dragMoved) {
      dragMoved = false;
      return;
    }
    const time = eventTime(event);
    const segment = segmentAt(time);
    dispatch('seek', { time: segment?.start ?? time });
  }

  function handleContextMenu(event: MouseEvent) {
    event.preventDefault();
    dispatch('seek', { time: eventTime(event) });
  }

  function handleDoubleClick(event: MouseEvent) {
    const time = eventTime(event);
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
    zoomAround(zoomStart + ratio * zoomSize, 1 + event.deltaY * wheelZoomSensitivity);
  }

  function handleKeydown(event: KeyboardEvent) {
    if (!effectiveDuration) return;
    const currentRatio = Math.min(1, Math.max(0, currentTime / effectiveDuration));
    if (event.code === 'KeyW') {
      event.preventDefault();
      zoomAround(currentRatio, 1 - keyZoomStep);
    } else if (event.code === 'KeyS') {
      event.preventDefault();
      zoomAround(currentRatio, 1 + keyZoomStep);
    } else if (event.code === 'KeyA') {
      event.preventDefault();
      panWindow(-keyPanStep * zoomSize);
    } else if (event.code === 'KeyD') {
      event.preventDefault();
      panWindow(keyPanStep * zoomSize);
    } else if (event.code === 'Escape') {
      event.preventDefault();
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

  function handleMouseMove(event: MouseEvent) {
    const time = eventTime(event);
    const segment = segmentAt(time);
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    hovered = {
      x: Math.min(rect.width - 44, Math.max(44, event.clientX - rect.left)),
      time,
      speaker: segment?.speaker_name ?? segment?.speaker ?? null
    };
  }
</script>

{#if effectiveDuration > 0 && segments.length}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    class="speaker-progress"
    class:dragging={dragActive}
    role="slider"
    tabindex="0"
    aria-label="Speaker timeline"
    aria-valuemin="0"
    aria-valuemax={Math.round(effectiveDuration)}
    aria-valuenow={Math.round(currentTime)}
    aria-valuetext={formatTime(currentTime)}
    on:click={handleClick}
    on:dblclick={handleDoubleClick}
    on:contextmenu={handleContextMenu}
    on:keydown={handleKeydown}
    on:mousedown={handleMouseDown}
    on:mousemove={handleMouseMove}
    on:mouseleave={() => (hovered = null)}
    on:wheel={handleWheel}
    title="Click to jump to a speaker segment. Double click to zoom. Right click to seek exactly. Wheel zooms. Drag pans when zoomed."
  >
    <svg aria-hidden="true">
      {#each segments as segment}
        {@const position = segmentPosition(segment)}
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

    {#if progressInWindow}
      <div class="progress-mask" style={`width:${progressPercent}%`}></div>
      <div class="time-marker" style={`left:${progressPercent}%`}></div>
    {/if}

    {#if hovered}
      <div class="hover-tip" style={`left:${hovered.x}px`}>
        <strong>{hovered.speaker ?? 'Silence'}</strong>
        <span>{formatTime(hovered.time)}</span>
      </div>
    {/if}

    {#if isZoomed}
      <button
        class="zoom-reset"
        type="button"
        title={`${formatTime(zoomStart * effectiveDuration)} - ${formatTime(zoomEnd * effectiveDuration)}`}
        on:click|stopPropagation={resetZoom}
      >
        Reset
      </button>
    {/if}
  </div>
{/if}

<style>
  .speaker-progress {
    position: relative;
    width: 100%;
    height: 22px;
    margin-top: 6px;
    overflow: visible;
    border: 1px solid #b9b2a4;
    border-radius: 6px;
    background: #595959;
    cursor: pointer;
    user-select: none;
  }

  .speaker-progress:focus-visible {
    outline: 2px solid #3b7dd8;
    outline-offset: 2px;
  }

  .speaker-progress.dragging {
    cursor: grabbing;
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

  .speaker-progress:hover rect {
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
    position: absolute;
    top: calc(100% + 5px);
    right: 0;
    z-index: 2;
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
