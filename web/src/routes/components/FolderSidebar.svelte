<script lang="ts">
  import type { FlatFolder } from "../home-page.helpers";

  let {
    folders,
    selectedFolderId,
    onSelect,
    onAdd,
  }: {
    folders: FlatFolder[];
    selectedFolderId: string | null;
    onSelect: (folderId: string | null) => void;
    onAdd: () => void;
  } = $props();
</script>

<aside>
  <div class="aside-head">
    <strong>Folders</strong><button onclick={onAdd}>New</button>
  </div>
  <button
    class:active={selectedFolderId === null}
    class="folder root"
    onclick={() => onSelect(null)}>Root</button
  >
  {#each folders as item}
    <button
      class:active={selectedFolderId === item.folder.id}
      class="folder"
      style={`padding-left: ${12 + item.depth * 18}px`}
      onclick={() => onSelect(item.folder.id)}>{item.folder.name}</button
    >
  {/each}
</aside>

<style>
  aside {
    padding: 12px 8px;
    border-right: 1px solid var(--color-border);
    background: var(--color-surface-subtle);
  }
  .aside-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 0 4px 8px;
  }
  .folder {
    width: 100%;
    min-height: 36px;
    border: 0;
    background: transparent;
    text-align: left;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .folder.active {
    background: var(--color-accent-surface);
    color: var(--color-accent-text);
  }
  @media (max-width: 640px) {
    aside {
      max-height: 190px;
      overflow: auto;
      border-right: 0;
      border-bottom: 1px solid var(--color-border);
    }
  }
</style>
