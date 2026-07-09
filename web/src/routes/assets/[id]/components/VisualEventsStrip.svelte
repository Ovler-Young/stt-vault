<script lang="ts">
  import type { VisualEvent } from '$lib/api';
  import { formatTime } from '$lib/format';
  import { activeVisualEventIndex, thumbnailUrl } from '../asset-page.helpers';
  import type { MaybePromise } from '../asset-page.types';

  export let assetId = '';
  export let events: VisualEvent[] = [];
  export let currentTime = 0;
  export let message = '';
  export let onDetect: () => MaybePromise = () => {};
  export let onSeek: (time: number) => void = () => {};

  let stripEl: HTMLDivElement | null = null;
  let dragStartX = 0;
  let dragScrollLeft = 0;
  let thumbDragging = false;
  let thumbMoved = false;
  let currentSlideEventIndex = -1;
  let lastScrolledSlideIndex = -1;

  $: currentSlideEventIndex = activeVisualEventIndex(events, currentTime);
  $: scrollActiveSlideIntoView(currentSlideEventIndex);

  function startThumbDrag(event: MouseEvent) {
    if (!stripEl) return;
    thumbDragging = true;
    thumbMoved = false;
    dragStartX = event.pageX;
    dragScrollLeft = stripEl.scrollLeft;
  }

  function moveThumbDrag(event: MouseEvent) {
    if (!stripEl || !thumbDragging) return;
    const delta = event.pageX - dragStartX;
    if (Math.abs(delta) > 3) thumbMoved = true;
    stripEl.scrollLeft = dragScrollLeft - delta;
  }

  function endThumbDrag() {
    thumbDragging = false;
  }

  function seekVisualEvent(event: MouseEvent, marker: VisualEvent) {
    if (thumbMoved) {
      event.preventDefault();
      thumbMoved = false;
      return;
    }
    onSeek(marker.timestamp);
  }

  function scrollActiveSlideIntoView(index: number) {
    if (!stripEl || thumbDragging || index < 0 || index === lastScrolledSlideIndex) return;
    lastScrolledSlideIndex = index;
    requestAnimationFrame(() => {
      const item = stripEl?.querySelector<HTMLElement>(`[data-slide-index="${index}"]`);
      if (!stripEl || !item) return;
      const target = item.offsetLeft - stripEl.clientWidth / 2 + item.clientWidth / 2;
      stripEl.scrollTo({ left: Math.max(0, target), behavior: 'smooth' });
    });
  }
</script>

<div class="visual-bar">
  <div class="visual-actions">
    <strong>Slides</strong>
    <button on:click={() => void onDetect()}>Detect</button>
    {#if message}<span>{message}</span>{/if}
  </div>
  {#if events.length}
    <div
      class:dragging={thumbDragging}
      class="thumb-strip"
      role="listbox"
      aria-label="Slide thumbnails"
      tabindex="0"
      bind:this={stripEl}
      on:mousedown={startThumbDrag}
      on:mousemove={moveThumbDrag}
      on:mouseup={endThumbDrag}
      on:mouseleave={endThumbDrag}
    >
      {#each events as event}
        <button
          role="option"
          data-slide-index={event.event_index}
          aria-selected={currentSlideEventIndex === event.event_index}
          class:active={currentSlideEventIndex === event.event_index}
          on:click={(clickEvent) => seekVisualEvent(clickEvent, event)}
          title={`${formatTime(event.timestamp)} · ${event.score.toFixed(1)}`}
        >
          <img draggable="false" src={thumbnailUrl(assetId, event)} alt={formatTime(event.timestamp)} />
          <span>{formatTime(event.timestamp)}</span>
        </button>
      {/each}
    </div>
  {:else}
    <p class="muted">No slide frames detected yet.</p>
  {/if}
</div>

<style>
  .visual-bar {
    display: grid;
    gap: 6px;
    margin-top: 6px;
    border: 1px solid #d2cec4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 8px;
  }

  .visual-actions {
    display: flex;
    gap: 6px;
    align-items: center;
    flex-wrap: wrap;
  }

  .visual-actions strong {
    font-size: 13px;
  }

  .visual-actions span {
    color: #2f6f73;
    font-size: 12px;
  }

  .thumb-strip {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    overscroll-behavior-x: contain;
    padding-bottom: 4px;
    cursor: grab;
    user-select: none;
  }

  .thumb-strip.dragging {
    cursor: grabbing;
  }

  .thumb-strip button {
    position: relative;
    flex: 0 0 144px;
    height: 81px;
    overflow: hidden;
    padding: 0;
    border-color: #c7c1b4;
    background: #111;
  }

  .thumb-strip button.active {
    border-color: #2f6f73;
    box-shadow: 0 0 0 2px #2f6f7333;
  }

  .thumb-strip img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
    pointer-events: none;
  }

  .thumb-strip span {
    position: absolute;
    right: 4px;
    bottom: 4px;
    border-radius: 4px;
    background: rgb(0 0 0 / 68%);
    color: white;
    font-size: 11px;
    padding: 2px 4px;
  }

  .muted {
    margin: 0;
    color: #666052;
    font-size: 11px;
  }
</style>
