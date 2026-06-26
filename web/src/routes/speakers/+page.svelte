<script lang="ts">
  import { onMount } from 'svelte';
  import {
    deleteSpeaker,
    fetchSpeakers,
    mergeSpeaker,
    recomputeAllSpeakers,
    renameSpeaker,
    type Speaker
  } from '$lib/api';
  import { formatDate } from '$lib/format';

  let speakers: Speaker[] = [];
  let drafts: Record<string, string> = {};
  let mergeTargets: Record<string, string> = {};
  let error = '';
  let message = '';

  onMount(load);

  async function load() {
    try {
      speakers = await fetchSpeakers();
      for (const speaker of speakers) {
        drafts[speaker.id] = drafts[speaker.id] ?? speaker.display_name;
      }
      error = '';
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function save(speaker: Speaker) {
    const displayName = drafts[speaker.id]?.trim();
    if (!displayName) return;
    try {
      const updated = await renameSpeaker(speaker.id, displayName);
      message = `${updated.display_name} saved`;
      await load();
    } catch (err) {
      message = '';
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function remove(speaker: Speaker) {
    if (!confirm(`Delete ${speaker.display_name}?`)) return;
    try {
      await deleteSpeaker(speaker.id);
      delete drafts[speaker.id];
      message = `${speaker.display_name} deleted`;
      await load();
    } catch (err) {
      message = '';
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function merge(source: Speaker) {
    const targetId = mergeTargets[source.id];
    if (!targetId || targetId === source.id) return;
    const target = speakers.find((speaker) => speaker.id === targetId);
    if (!target) return;
    if (!confirm(`Merge ${source.display_name} into ${target.display_name}?`)) return;
    try {
      const updated = await mergeSpeaker(target.id, source.id);
      delete drafts[source.id];
      delete mergeTargets[source.id];
      message = `${source.display_name} merged into ${updated.display_name}`;
      await load();
    } catch (err) {
      message = '';
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function recomputeAll() {
    try {
      const result = await recomputeAllSpeakers();
      message = `${result.assets} assets recomputed`;
      await load();
    } catch (err) {
      message = '';
      error = err instanceof Error ? err.message : String(err);
    }
  }
</script>

<main>
  <section class="panel">
    <header>
      <h1>Speakers</h1>
      <div class="actions">
        <button on:click={recomputeAll}>Recompute all</button>
        <button on:click={load}>Refresh</button>
      </div>
    </header>
    {#if error}<p class="error">{error}</p>{/if}
    {#if message}<p class="message">{message}</p>{/if}

    <div class="speaker-list">
      {#each speakers as speaker}
        <div class="speaker-row">
          <div>
            <strong>{speaker.display_name}</strong>
            <small>{speaker.id} · {speaker.sample_count} samples · {formatDate(speaker.updated_at)}</small>
          </div>
          <input bind:value={drafts[speaker.id]} />
          <button on:click={() => save(speaker)}>Save</button>
          <select bind:value={mergeTargets[speaker.id]}>
            <option value="">Merge into...</option>
            {#each speakers.filter((candidate) => candidate.id !== speaker.id) as candidate}
              <option value={candidate.id}>{candidate.display_name}</option>
            {/each}
          </select>
          <button disabled={!mergeTargets[speaker.id]} on:click={() => merge(speaker)}>Merge</button>
          <button class="danger" on:click={() => remove(speaker)}>Delete</button>
        </div>
      {:else}
        <p class="muted">No speakers saved yet.</p>
      {/each}
    </div>
  </section>
</main>

<style>
  main {
    padding: 16px;
  }

  .panel {
    border: 1px solid #d2cec4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 16px;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  h1,
  p {
    margin: 0;
  }

  .speaker-list {
    display: grid;
    gap: 8px;
    margin-top: 14px;
  }

  .actions {
    display: flex;
    gap: 8px;
  }

  .speaker-row {
    display: grid;
    grid-template-columns: minmax(180px, 1fr) minmax(180px, 260px) auto minmax(150px, 220px) auto auto;
    gap: 8px;
    align-items: center;
    border: 1px solid #d2cec4;
    border-radius: 8px;
    background: white;
    padding: 10px;
  }

  .speaker-row > div {
    display: grid;
    gap: 4px;
  }

  small,
  .muted {
    color: #666052;
    font-size: 12px;
  }

  .error,
  .danger {
    color: #9b1c1c;
  }

  .message {
    margin-top: 12px;
    color: #2f6f73;
  }

  @media (max-width: 820px) {
    .speaker-row {
      grid-template-columns: 1fr;
    }
  }
</style>
