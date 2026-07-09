<script lang="ts">
  import { clampMediaPaneWidth, PANE_DIVIDER_WIDTH } from '../asset-page.helpers';

  const paneResizeStep = 32;

  let workspaceEl: HTMLElement | null = null;
  let mediaPaneWidth: number | null = null;
  let resizingPanes = false;

  $: workspaceStyle = mediaPaneWidth === null ? '' : `--media-pane-width: ${mediaPaneWidth}px;`;

  function startPaneResize(event: PointerEvent) {
    if (event.button !== 0 || !workspaceEl) return;
    event.preventDefault();
    resizingPanes = true;
    const target = event.currentTarget;
    if (target instanceof HTMLElement) target.setPointerCapture(event.pointerId);
    updateMediaPaneWidth(event.clientX);
  }

  function movePaneResize(event: PointerEvent) {
    if (!resizingPanes) return;
    event.preventDefault();
    updateMediaPaneWidth(event.clientX);
  }

  function endPaneResize(event: PointerEvent) {
    if (!resizingPanes) return;
    resizingPanes = false;
    const target = event.currentTarget;
    if (target instanceof HTMLElement && target.hasPointerCapture(event.pointerId)) {
      target.releasePointerCapture(event.pointerId);
    }
  }

  function handlePaneResizeKeydown(event: KeyboardEvent) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    resizeMediaPaneBy(event.key === 'ArrowLeft' ? -paneResizeStep : paneResizeStep);
  }

  function resizeMediaPaneBy(delta: number) {
    if (!workspaceEl) return;
    const availableWidth = workspaceEl.clientWidth - PANE_DIVIDER_WIDTH;
    const defaultWidth = Math.round(workspaceEl.clientWidth * 0.47);
    const currentWidth = mediaPaneWidth ?? defaultWidth;
    mediaPaneWidth = clampMediaPaneWidth(currentWidth + delta, availableWidth);
  }

  function updateMediaPaneWidth(clientX: number) {
    if (!workspaceEl) return;
    const rect = workspaceEl.getBoundingClientRect();
    const availableWidth = rect.width - PANE_DIVIDER_WIDTH;
    const requestedWidth = clientX - rect.left - PANE_DIVIDER_WIDTH / 2;
    mediaPaneWidth = clampMediaPaneWidth(requestedWidth, availableWidth);
  }
</script>

<section class:resizing={resizingPanes} class="workspace" style={workspaceStyle} bind:this={workspaceEl}>
  <div class="media-pane">
    <slot name="media" />
  </div>

  <button
    type="button"
    class:resizing={resizingPanes}
    class="pane-divider"
    aria-label="Resize media and transcript panes"
    on:pointerdown={startPaneResize}
    on:pointermove={movePaneResize}
    on:pointerup={endPaneResize}
    on:pointercancel={endPaneResize}
    on:keydown={handlePaneResizeKeydown}
  ></button>

  <slot name="transcript" />
</section>

<style>
  .workspace {
    --media-pane-min: 420px;
    --transcript-pane-min: 360px;
    --pane-divider-width: 12px;

    display: grid;
    grid-template-columns:
      minmax(
        var(--media-pane-min),
        min(var(--media-pane-width, 47vw), calc(100% - var(--pane-divider-width) - var(--transcript-pane-min)))
      )
      var(--pane-divider-width)
      minmax(var(--transcript-pane-min), 1fr);
    gap: 0;
    align-items: stretch;
    min-height: 0;
    overflow: hidden;
  }

  .media-pane {
    box-sizing: border-box;
    min-width: 0;
    min-height: 0;
    max-height: 100%;
    overflow-y: auto;
    padding-right: 8px;
  }

  .pane-divider {
    position: relative;
    align-self: stretch;
    min-height: 100%;
    margin: 0;
    padding: 0;
    border: 0;
    background: transparent;
    cursor: col-resize;
    touch-action: none;
  }

  .pane-divider::before {
    content: '';
    position: absolute;
    inset: 0 4px;
    border-radius: 4px;
    background: #c7c1b4;
  }

  .pane-divider:hover::before,
  .pane-divider:focus-visible::before,
  .pane-divider.resizing::before {
    background: #2f6f73;
  }

  .pane-divider:focus-visible {
    outline: 2px solid #2f6f73;
    outline-offset: 2px;
  }

  .workspace.resizing,
  .workspace.resizing :global(*) {
    cursor: col-resize;
    user-select: none;
  }

  @media (max-width: 980px) {
    .workspace {
      grid-template-columns: 1fr;
      grid-template-rows: auto minmax(0, 1fr);
      align-items: stretch;
      overflow: hidden;
    }

    .media-pane {
      max-height: 52vh;
      overflow-y: auto;
      padding-right: 0;
    }

    .pane-divider {
      display: none;
    }
  }
</style>
