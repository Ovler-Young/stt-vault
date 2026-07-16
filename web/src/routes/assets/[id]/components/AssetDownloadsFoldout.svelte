<script lang="ts">
  import { exportHref } from '../asset-page.helpers';
  import FoldoutPanel from './FoldoutPanel.svelte';

  export let assetId = '';
  export let assetExports: Record<string, string> = {};

  let downloadMessage = '';

  async function copyText(text: string) {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch {
        // Continue to the browser fallback when clipboard permissions are unavailable.
      }
    }

    if (typeof document === 'undefined') return false;
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.append(textarea);
    textarea.select();
    try {
      return document.execCommand('copy');
    } finally {
      textarea.remove();
    }
  }

  async function copyExportHref(event: MouseEvent, href: string) {
    event.preventDefault();
    try {
      if (!(await copyText(href))) throw new Error('Clipboard access is unavailable');
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
