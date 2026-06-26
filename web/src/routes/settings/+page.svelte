<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchConfig } from '$lib/api';

  let config: Record<string, unknown> | null = null;
  let error = '';

  onMount(async () => {
    try {
      config = await fetchConfig();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  });
</script>

<main>
  <section class="panel">
    <h1>Settings</h1>
    <p>Runtime settings come from `.env`. Secrets are not shown here.</p>
    {#if error}<p class="error">{error}</p>{/if}
    {#if config}
      <dl>
        {#each Object.entries(config) as [key, value]}
          <dt>{key}</dt>
          <dd>{String(value)}</dd>
        {/each}
      </dl>
    {/if}
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

  h1,
  p {
    margin: 0;
  }

  p {
    color: #666052;
    margin-top: 4px;
  }

  dl {
    display: grid;
    grid-template-columns: 240px 1fr;
    gap: 8px 12px;
    margin-top: 16px;
  }

  dt {
    font-weight: 700;
  }

  dd {
    margin: 0;
  }

  .error {
    color: #9b1c1c;
  }
</style>
