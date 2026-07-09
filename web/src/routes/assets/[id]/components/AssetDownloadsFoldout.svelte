<script lang="ts">
  import { exportHref } from '../asset-page.helpers';
  import FoldoutPanel from './FoldoutPanel.svelte';

  export let assetId = '';
  export let assetExports: Record<string, string> = {};

  let downloadMessage = '';

  async function copyExportHref(event: MouseEvent, href: string) {
    event.preventDefault();
    try {
      await navigator.clipboard.writeText(href);
      downloadMessage = 'Download link copied';
    } catch (err) {
      downloadMessage = err instanceof Error ? `Copy failed: ${err.message}` : 'Copy failed';
    }
  }
</script>

<FoldoutPanel summary="Downloads">
  <div class="exports">
    {#each Object.keys(assetExports) as format}
      {@const href = exportHref(assetId, format)}
      <a href={href} download on:contextmenu={(event) => copyExportHref(event, href)}>{format}</a>
    {/each}
    {#if downloadMessage}<span class="download-message" aria-live="polite">{downloadMessage}</span>{/if}
  </div>
</FoldoutPanel>

<style>
  .exports {
    display: flex;
    gap: 6px;
    align-items: center;
    flex-wrap: wrap;
    margin-top: 8px;
  }

  .download-message {
    color: #2f6f73;
    font-size: 12px;
  }
</style>
