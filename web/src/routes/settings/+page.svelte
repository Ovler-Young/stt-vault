<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchConfig } from '$lib/api';
  import { getThemePreference, setThemePreference, type ThemePreference } from '$lib/theme';

  let config: Record<string, unknown> | null = null;
  let error = '';
  let theme: ThemePreference = 'system';

  onMount(async () => {
    theme = getThemePreference();
    try {
      config = await fetchConfig();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  });

  function selectTheme(nextTheme: ThemePreference) {
    theme = nextTheme;
    setThemePreference(nextTheme);
  }
</script>

<main>
  <section class="panel">
    <h1>Settings</h1>
    <p>Runtime settings come from `.env`. Secrets are not shown here.</p>
    <div class="setting-row">
      <div>
        <strong>Appearance</strong>
        <span>Theme</span>
      </div>
      <fieldset aria-label="Theme">
        {#each ['system', 'light', 'dark'] as option}
          <label>
            <input
              type="radio"
              name="theme"
              value={option}
              checked={theme === option}
              on:change={() => selectTheme(option as ThemePreference)}
            />
            {option[0].toUpperCase() + option.slice(1)}
          </label>
        {/each}
      </fieldset>
    </div>
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
    border: 1px solid var(--color-border);
    border-radius: 8px;
    background: var(--color-surface);
    padding: 16px;
  }

  h1,
  p {
    margin: 0;
  }

  p {
    color: var(--color-text-muted);
    margin-top: 4px;
  }

  dl {
    display: grid;
    grid-template-columns: 240px 1fr;
    gap: 8px 12px;
    margin-top: 16px;
  }

  .setting-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-top: 18px;
    padding: 12px 0;
    border-top: 1px solid var(--color-border);
    border-bottom: 1px solid var(--color-border);
  }

  .setting-row > div {
    display: grid;
    gap: 3px;
  }

  .setting-row span {
    color: var(--color-text-muted);
    font-size: 12px;
  }

  fieldset {
    display: grid;
    grid-template-columns: repeat(3, minmax(64px, 1fr));
    margin: 0;
    padding: 3px;
    border: 1px solid var(--color-border-strong);
    border-radius: 7px;
    background: var(--color-surface-muted);
  }

  fieldset label {
    min-width: 0;
    padding: 6px 10px;
    border-radius: 5px;
    text-align: center;
    cursor: pointer;
  }

  fieldset label:has(input:checked) {
    background: var(--color-surface-strong);
    color: var(--color-accent-text);
    box-shadow: 0 0 0 1px var(--color-border);
  }

  fieldset input {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
    padding: 0;
    border: 0;
  }

  fieldset label:has(input:focus-visible) {
    outline: 2px solid var(--color-focus);
    outline-offset: 1px;
  }

  dt {
    font-weight: 700;
  }

  dd {
    margin: 0;
  }

  .error {
    color: var(--color-danger);
  }

  @media (max-width: 560px) {
    .setting-row {
      align-items: stretch;
      flex-direction: column;
    }

    dl {
      grid-template-columns: 1fr;
    }
  }
</style>
