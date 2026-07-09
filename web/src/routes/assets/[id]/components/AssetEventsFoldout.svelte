<script lang="ts">
  import type { JobEvent } from '$lib/api';
  import { formatDate } from '$lib/format';
  import FoldoutPanel from './FoldoutPanel.svelte';

  export let events: JobEvent[] = [];
  export let eventHistory: JobEvent[] = [];

  let showHistory = false;

  $: displayedEvents = showHistory ? eventHistory : events;
</script>

<FoldoutPanel summary={showHistory ? 'Full Log' : 'Current Run Log'}>
  <button class="log-toggle" on:click={() => (showHistory = !showHistory)}>
    {showHistory ? 'Current run' : 'All history'}
  </button>
  <div class="events">
    {#each displayedEvents as event}
      <div class={`event ${event.level}`}>
        <small>
          {formatDate(event.created_at)} · run {event.run_attempt ?? 0} · {event.stage ?? event.level}
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
    border-left: 3px solid #b9b2a4;
    padding: 4px 6px;
    background: white;
  }

  .event p {
    margin: 0;
    font-size: 12px;
  }

  .event.warning {
    border-left-color: #a66b00;
  }

  .event.error {
    border-left-color: #9b1c1c;
  }

  small {
    color: #666052;
    font-size: 11px;
  }
</style>
