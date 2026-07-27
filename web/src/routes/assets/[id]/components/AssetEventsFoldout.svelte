<script lang="ts">
  import type { JobEvent } from "$lib/api-types";
  import { formatDate } from "$lib/format";
  import FoldoutPanel from "./FoldoutPanel.svelte";

  export let events: JobEvent[] = [];
  export let eventHistory: JobEvent[] = [];

  let showHistory = false;
  let announcedEventId: number | undefined;
  let announcement = "";

  $: displayedEvents = showHistory ? eventHistory : events;
  $: latestEvent = events.at(-1);
  $: if (latestEvent?.id !== announcedEventId) {
    announcedEventId = latestEvent?.id;
    announcement = latestEvent
      ? `${latestEvent.level}: ${latestEvent.message}`
      : "";
  }
</script>

<FoldoutPanel summary={showHistory ? "Full Log" : "Current Run Log"}>
  <button
    class="log-toggle"
    aria-pressed={showHistory}
    aria-controls="asset-events-log"
    on:click={() => (showHistory = !showHistory)}
  >
    {showHistory ? "Current run" : "All history"}
  </button>
  <p class="sr-only" aria-live="polite" aria-atomic="true">{announcement}</p>
  <div id="asset-events-log" class="events">
    {#each displayedEvents as event}
      <div class={`event ${event.level}`}>
        <small>
          {formatDate(event.created_at)} · run {event.run_attempt ?? 0} · {event.stage ??
            event.level}
        </small>
        <p>{event.message}</p>
      </div>
    {/each}
  </div>
</FoldoutPanel>

<style>
  .log-toggle {
    margin-top: 8px;
  }

  .events {
    display: grid;
    gap: 4px;
    max-height: 280px;
    overflow: auto;
    margin-top: 8px;
  }

  .event {
    border-left: 3px solid var(--color-border-muted);
    padding: 4px 6px;
    background: var(--color-surface-strong);
  }

  .event p {
    margin: 0;
    font-size: 12px;
  }

  .event.warning {
    border-left-color: var(--color-warning);
  }

  .event.error {
    border-left-color: var(--color-danger);
  }

  small {
    color: var(--color-text-muted);
    font-size: 11px;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
</style>
