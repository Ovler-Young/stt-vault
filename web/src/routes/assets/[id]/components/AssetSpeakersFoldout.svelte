<script lang="ts">
  import { formatTime } from '$lib/format';
  import type { LocalSpeakerRow, MaybePromise } from '../asset-page.types';
  import FoldoutPanel from './FoldoutPanel.svelte';

  export let rows: LocalSpeakerRow[] = [];
  export let speakerDrafts: Record<string, string> = {};
  export let speakerMessage = '';
  export let onRecompute: () => MaybePromise = () => {};
  export let onSave: (localSpeaker: string) => MaybePromise = () => {};
</script>

<FoldoutPanel summary="Speakers">
  <div class="speaker-controls">
    <button on:click={() => void onRecompute()}>Recompute matches</button>
    {#each rows as speaker}
      <div class="speaker-row">
        <strong>{speaker.localSpeaker}</strong>
        <input bind:value={speakerDrafts[speaker.localSpeaker]} />
        <small>
          {speaker.count} chunks
          {#if speaker.firstStart !== null && speaker.lastEnd !== null}
            · {formatTime(speaker.firstStart)} - {formatTime(speaker.lastEnd)}
          {/if}
          {#if speaker.speakerId} · {speaker.speakerId}{/if}
          {#if speaker.similarity !== null} · match {speaker.similarity.toFixed(3)}{/if}
        </small>
        <button on:click={() => void onSave(speaker.localSpeaker)}>Save</button>
      </div>
    {/each}
    {#if speakerMessage}<p class="message">{speakerMessage}</p>{/if}
  </div>
</FoldoutPanel>

<style>
  .speaker-controls {
    display: grid;
    gap: 6px;
    margin-top: 8px;
  }

  .speaker-row {
    display: grid;
    grid-template-columns: 92px minmax(140px, 1fr) minmax(180px, 1.3fr) auto;
    gap: 6px;
    align-items: center;
  }

  small {
    color: var(--color-text-muted);
    font-size: 11px;
  }

  .message {
    color: var(--color-accent);
    margin: 0;
  }

  @media (max-width: 980px) {
    .speaker-row {
      grid-template-columns: 1fr;
    }
  }
</style>
