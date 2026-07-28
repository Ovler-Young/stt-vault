<script lang="ts">
  import type { AssetDetail } from "$lib/api/types";
  import { summarizeAsset } from "$lib/api/endpoints";
  import SummaryMarkdown from "$lib/ui/SummaryMarkdown.svelte";
  import FoldoutPanel from "./FoldoutPanel.svelte";

  let {
    asset,
    onUpdated,
    onSeek,
  }: {
    asset: AssetDetail;
    onUpdated: () => Promise<void>;
    onSeek: (time: number) => void;
  } = $props();

  let message = $state("");

  async function summarize() {
    message = "Generating summary";
    try {
      await summarizeAsset(asset.id);
      await onUpdated();
      message = "";
    } catch (error) {
      await onUpdated();
      message = asset.summary_error
        ? ""
        : error instanceof Error
          ? error.message
          : String(error);
    }
  }
</script>

<FoldoutPanel summary="Summary" open>
  <button onclick={summarize}>Generate summary</button>
  {#if message}<p aria-live="polite">{message}</p>{/if}
  {#if asset.summary_text}<SummaryMarkdown
      markdown={asset.summary_text}
      {onSeek}
    />{/if}
  {#if asset.summary_error}<p class="error">{asset.summary_error}</p>{/if}
</FoldoutPanel>

<style>
  .error {
    color: var(--color-danger);
    margin: 0;
  }
</style>
