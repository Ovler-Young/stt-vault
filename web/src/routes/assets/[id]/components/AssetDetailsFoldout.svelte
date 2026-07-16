<script lang="ts">
  import type { AssetDetail } from '$lib/api';
  import { formatDate } from '$lib/format';
  import { formatStatValue } from '../asset-page.helpers';
  import FoldoutPanel from './FoldoutPanel.svelte';

  export let asset: AssetDetail;
</script>

<FoldoutPanel summary="Details">
  {#if asset.job}
    <div class="kv">
      <span>Stage</span><strong>{asset.job.stage ?? asset.status}</strong>
      <span>Done</span><strong>{asset.job.progress_done_chunks}</strong>
      <span>Total</span><strong>{asset.job.progress_total_chunks}</strong>
      <span>Retries</span><strong>{asset.job.progress_failed_chunks}</strong>
      {#if asset.job.next_retry_at}
        <span>Retry after</span><strong>{formatDate(asset.job.next_retry_at)}</strong>
      {/if}
    </div>
  {/if}
  {#if asset.diarization_stats}
    <div class="stats">
      {#each Object.entries(asset.diarization_stats) as [key, value]}
        <span>{key}: {formatStatValue(value)}</span>
      {/each}
    </div>
  {/if}
  {#if asset.error}
    <pre class="error-box">{JSON.stringify(asset.error, null, 2)}</pre>
  {/if}
</FoldoutPanel>

<style>
  .kv {
    display: grid;
    grid-template-columns: 88px minmax(0, 1fr);
    gap: 4px 8px;
    margin-top: 8px;
    font-size: 12px;
  }

  .kv span {
    color: var(--color-text-muted);
  }

  .stats {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
    margin-top: 8px;
  }

  .stats span {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    border: 1px solid var(--color-border);
    border-radius: 6px;
    padding: 4px 6px;
    background: var(--color-surface-strong);
    font-size: 11px;
  }

  .error-box {
    margin-top: 8px;
    white-space: pre-wrap;
    background: var(--color-danger-surface);
    border: 1px solid var(--color-danger-border);
    border-radius: 6px;
    padding: 8px;
  }
</style>
