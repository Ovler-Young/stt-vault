<script lang="ts">
  import type { AssetDetail } from '$lib/api';
  import { formatTime } from '$lib/format';
  import { progressText } from '../asset-page.helpers';
  import type { MaybePromise } from '../asset-page.types';

  export let asset: AssetDetail;
  export let onRetry: () => MaybePromise = () => {};
  export let onRemove: () => MaybePromise = () => {};
</script>

<section class="asset-head">
  <div class="title">
    <h1>{asset.filename}</h1>
    <p>{asset.status} · {formatTime(asset.duration)} {#if progressText(asset)}· {progressText(asset)} chunks{/if}</p>
  </div>
  <div class="actions">
    {#if asset.status === 'failed' || asset.status === 'partial'}
      <button on:click={() => void onRetry()}>Retry</button>
    {/if}
    <button class="danger" on:click={() => void onRemove()}>Delete</button>
  </div>
</section>

<style>
  .asset-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-width: 0;
    border: 1px solid #d2cec4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 8px 10px;
  }

  .title {
    min-width: 0;
  }

  h1,
  p {
    margin: 0;
  }

  h1 {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 16px;
  }

  .title p {
    color: #666052;
    font-size: 11px;
  }

  .actions {
    display: flex;
    gap: 6px;
    align-items: center;
    flex-wrap: wrap;
  }

  .danger {
    color: #9b1c1c;
  }
</style>
