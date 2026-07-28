<script lang="ts">
  import { createEventDispatcher, onDestroy } from "svelte";
  import type { TranscriptSegment } from "$lib/api/types";
  import { formatTime } from "$lib/formatting/date-time";
  import SpeakerTimelineViews from "$lib/timeline/SpeakerTimelineViews.svelte";
  import {
    clampTimelineWindow,
    clampRatio,
    currentTimelineRatio,
    effectiveTimelineDuration,
    eventTimeFromClientX,
    hoverTipX,
    panTimelineWindow,
    type TimelineHover,
    type TimelineRow,
    type TimelineWindow,
    zoomWindowAround,
  } from "$lib/timeline/speakerTimeline";
  import {
    doubleClickTimelineWindow,
    draggedTimelineWindow,
    keyboardTimelineWindow,
    seekTimeForTimelineEvent,
    timelineHover,
    wheelTimelineWindow,
  } from "$lib/timeline/speakerTimeline.interactions";

  export let segments: TranscriptSegment[] = [];
  export let duration: number | null = null;
  export let currentTime = 0;

  const dispatch = createEventDispatcher<{ seek: { time: number } }>();
  const minZoomSize = 0.02;
  const wheelZoomSensitivity = 0.003;
  const wheelPanSensitivity = 0.00125;
  const keyZoomStep = 0.12;
  const keyPanStep = 0.12;
  const fullTimelineWindow: TimelineWindow = { start: 0, end: 1 };

  let hovered: TimelineHover | null = null;
  let zoomStart = 0;
  let zoomEnd = 1;
  let dragStartX = 0;
  let dragStartZoom: TimelineWindow = fullTimelineWindow;
  let dragContainerWidth = 1;
  let dragMoved = false;
  let dragActive = false;

  $: effectiveDuration = effectiveTimelineDuration(duration, segments);
  $: zoomSize = Math.max(0.001, zoomEnd - zoomStart);
  $: currentRatio = currentTimelineRatio(currentTime, effectiveDuration);
  $: zoomProgressPercent = Math.min(
    100,
    Math.max(0, ((currentRatio - zoomStart) / zoomSize) * 100),
  );
  $: fullProgressPercent = currentRatio * 100;
  $: zoomProgressInWindow =
    currentRatio >= zoomStart && currentRatio <= zoomEnd;
  $: isZoomed = zoomStart > 0.0001 || zoomEnd < 0.9999;
  $: zoomWindowLeft = zoomStart * 100;
  $: zoomWindowWidth = zoomSize * 100;
  $: zoomWindowLabel = `${formatTime(zoomStart * effectiveDuration)} - ${formatTime(
    zoomEnd * effectiveDuration,
  )}`;

  onDestroy(() => {
    stopDrag();
  });

  function eventTime(
    event: MouseEvent,
    windowStart: number,
    windowEnd: number,
  ) {
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    return eventTimeFromClientX(
      event.clientX,
      rect.left,
      rect.width,
      windowStart,
      windowEnd,
      effectiveDuration,
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

  export function centerOnTime(time: number) {
    if (!effectiveDuration) return;
    const currentSize = zoomEnd - zoomStart;
    const center = clampRatio(time / effectiveDuration);
    setZoomWindow(center - currentSize * 0.1, center + currentSize * 0.9);
  }

  export function zoomAtTime(time: number, scale: number) {
    if (!effectiveDuration) return;
    setTimelineWindow(
      zoomWindowAround(
        { start: zoomStart, end: zoomEnd },
        time / effectiveDuration,
        scale,
        minZoomSize,
      ),
    );
  }

  export function panByWindow(delta: number) {
    setTimelineWindow(
      panTimelineWindow(
        { start: zoomStart, end: zoomEnd },
        delta * zoomSize,
        minZoomSize,
      ),
    );
  }

  function setTimelineWindow(window: TimelineWindow) {
    zoomStart = window.start;
    zoomEnd = window.end;
  }

  function handleClick(
    event: MouseEvent,
    windowStart: number,
    windowEnd: number,
  ) {
    if (dragMoved) {
      dragMoved = false;
      return;
    }
    const time = eventTime(event, windowStart, windowEnd);
    dispatch("seek", { time: seekTimeForTimelineEvent(segments, time) });
  }

  function handleContextMenu(
    event: MouseEvent,
    windowStart: number,
    windowEnd: number,
  ) {
    event.preventDefault();
    dispatch("seek", { time: eventTime(event, windowStart, windowEnd) });
  }

  function handleDoubleClick(
    event: MouseEvent,
    windowStart: number,
    windowEnd: number,
  ) {
    if (!effectiveDuration) return;
    const time = eventTime(event, windowStart, windowEnd);
    setTimelineWindow(
      doubleClickTimelineWindow(
        segments,
        time,
        effectiveDuration,
        { start: zoomStart, end: zoomEnd },
        minZoomSize,
      ),
    );
  }

  function handleWheel(
    event: WheelEvent,
    windowStart: number,
    windowEnd: number,
  ) {
    if (!effectiveDuration) return;
    event.preventDefault();
    setTimelineWindow(
      wheelTimelineWindow(
        { start: zoomStart, end: zoomEnd },
        eventTime(event, windowStart, windowEnd) / effectiveDuration,
        event.deltaX,
        event.deltaY,
        isZoomed,
        minZoomSize,
        wheelZoomSensitivity,
        wheelPanSensitivity,
      ),
    );
  }

  function handleKeydown(event: KeyboardEvent) {
    if (!effectiveDuration) return;
    const nextWindow = keyboardTimelineWindow(
      event.code,
      currentTime,
      effectiveDuration,
      { start: zoomStart, end: zoomEnd },
      minZoomSize,
      keyZoomStep,
      keyPanStep,
    );
    if (nextWindow) {
      event.preventDefault();
      event.stopPropagation();
      setTimelineWindow(nextWindow);
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
    if (typeof document !== "undefined") {
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", handleDocumentMouseMove);
      document.addEventListener("mouseup", handleDocumentMouseUp);
    }
  }

  function handleDocumentMouseMove(event: MouseEvent) {
    if (!dragActive) return;
    const dx = event.clientX - dragStartX;
    if (Math.abs(dx) > 3) dragMoved = true;
    setTimelineWindow(
      draggedTimelineWindow(dragStartZoom, dx, dragContainerWidth, minZoomSize),
    );
  }

  function handleDocumentMouseUp() {
    stopDrag();
  }

  function stopDrag() {
    dragActive = false;
    if (typeof document !== "undefined") {
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", handleDocumentMouseMove);
      document.removeEventListener("mouseup", handleDocumentMouseUp);
    }
  }

  function handleMouseMove(
    event: MouseEvent,
    row: TimelineRow,
    windowStart: number,
    windowEnd: number,
  ) {
    const time = eventTime(event, windowStart, windowEnd);
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    hovered = timelineHover(
      row,
      hoverTipX(event.clientX, rect.left, rect.width),
      time,
      segments,
    );
  }
</script>

{#if effectiveDuration > 0 && segments.length}
  <SpeakerTimelineViews
    {segments}
    {effectiveDuration}
    zoomWindow={{ start: zoomStart, end: zoomEnd }}
    {fullTimelineWindow}
    {currentTime}
    {zoomProgressPercent}
    {fullProgressPercent}
    {zoomProgressInWindow}
    {isZoomed}
    {zoomWindowLeft}
    {zoomWindowWidth}
    {zoomWindowLabel}
    {hovered}
    dragging={dragActive}
    onClick={(event, window) => handleClick(event, window.start, window.end)}
    onContextMenu={(event, window) =>
      handleContextMenu(event, window.start, window.end)}
    onKeydown={handleKeydown}
    onMouseDown={handleMouseDown}
    onMouseMove={(event, row, window) =>
      handleMouseMove(event, row, window.start, window.end)}
    onMouseLeave={() => (hovered = null)}
    onDoubleClick={(event, window) =>
      handleDoubleClick(event, window.start, window.end)}
    onWheel={(event, window) => handleWheel(event, window.start, window.end)}
    onReset={resetZoom}
  />
{/if}
