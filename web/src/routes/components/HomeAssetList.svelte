<script lang="ts">
  import type { AssetSummary } from "$lib/api-types";
  import { formatDate, formatRecordedAt, formatTime } from "$lib/format";
  import type { FlatFolder } from "../home-page.helpers";

  let {
    assets,
    folders,
    assetTargets,
    busy,
    onTargetChange,
    onMove,
    onDelete,
  }: {
    assets: AssetSummary[];
    folders: FlatFolder[];
    assetTargets: Record<string, string>;
    busy: boolean;
    onTargetChange: (assetId: string, targetId: string) => void;
    onMove: (asset: AssetSummary) => void;
    onDelete: (asset: AssetSummary) => void;
  } = $props();
</script>

<div class="asset-list">
  {#each assets as asset}
    <article>
      <a class="asset-link" href={`/assets/${asset.id}`}>
        <strong>{asset.title || asset.filename}</strong>
        <span>{asset.filename}</span>
        <small>
          {asset.status} · {formatTime(asset.duration)} · {asset.recorded_at
            ? formatRecordedAt(asset.recorded_at)
            : formatDate(asset.updated_at)}
        </small>
      </a>
      <select
        aria-label={`Move ${asset.filename}`}
        value={assetTargets[asset.id] ?? asset.parent_folder_id ?? ""}
        onchange={(event) =>
          onTargetChange(asset.id, event.currentTarget.value)}
      >
        <option value="">Root</option>
        {#each folders as item}
          <option value={item.folder.id}
            >{"  ".repeat(item.depth)}{item.folder.name}</option
          >
        {/each}
      </select>
      <button
        disabled={(assetTargets[asset.id] ?? asset.parent_folder_id ?? "") ===
          (asset.parent_folder_id ?? "") || busy}
        onclick={() => onMove(asset)}>Move</button
      >
      <button class="danger" disabled={busy} onclick={() => onDelete(asset)}
        >Delete</button
      >
    </article>
  {:else}
    <p class="empty">No assets in this folder.</p>
  {/each}
</div>

<style>
  .asset-list {
    display: grid;
    margin-top: 12px;
    border-top: 1px solid var(--color-border);
  }
  article {
    display: grid;
    grid-template-columns: minmax(240px, 1fr) minmax(150px, 220px) auto auto;
    gap: 8px;
    align-items: center;
    min-height: 64px;
    padding: 8px 0;
    border-bottom: 1px solid var(--color-border);
  }
  .asset-link {
    display: grid;
    min-width: 0;
    gap: 2px;
    border: 0;
    background: transparent;
    padding: 2px 4px;
  }
  .asset-link strong,
  .asset-link span,
  .asset-link small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .asset-link span,
  .asset-link small,
  .empty {
    color: var(--color-text-muted);
    font-size: 12px;
  }
  .empty {
    padding: 24px 4px;
  }
  .danger {
    color: var(--color-danger);
  }

  @media (max-width: 900px) {
    article {
      grid-template-columns: minmax(0, 1fr) auto auto;
    }
    article select {
      grid-column: 1 / -1;
      grid-row: 2;
    }
  }
  @media (max-width: 640px) {
    article {
      grid-template-columns: minmax(0, 1fr) auto;
    }
    article select {
      grid-column: 1 / -1;
    }
  }
</style>
