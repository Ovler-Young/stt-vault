<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { TranscriptSegment } from '$lib/api';
  import { formatTime } from '$lib/format';

  export let segments: TranscriptSegment[] = [];
  export let duration: number | null = null;
  export let currentTime = 0;

  const dispatch = createEventDispatcher<{ seek: { time: number } }>();

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

  function handleClick(event: MouseEvent) {
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
    zoomStart = Math.max(0, Math.min(1 - nextWidth, center - nextWidth / 2));
    zoomEnd = Math.min(1, zoomStart + nextWidth);
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
    on:click={handleClick}
    on:dblclick={handleDoubleClick}
    on:contextmenu={handleContextMenu}
    on:mousemove={handleMouseMove}
    on:mouseleave={() => (hovered = null)}
    title="Click to jump to a speaker segment. Double click to zoom. Right click to seek exactly."
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
</style>
