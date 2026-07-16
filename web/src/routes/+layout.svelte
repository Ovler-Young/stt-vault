<script lang="ts">
  import { page } from '$app/stores';
</script>

<svelte:head>
  <title>STT Vault</title>
</svelte:head>

<div class="shell">
  <nav>
    <a class:active={$page.url.pathname === '/'} href="/" aria-label="Dashboard">
      <strong>D</strong><span>Dashboard</span>
    </a>
    <a class:active={$page.url.pathname.startsWith('/jobs')} href="/jobs" aria-label="Jobs">
      <strong>J</strong><span>Jobs</span>
    </a>
    <a class:active={$page.url.pathname.startsWith('/speakers')} href="/speakers" aria-label="Speakers">
      <strong>S</strong><span>Speakers</span>
    </a>
    <a class:active={$page.url.pathname.startsWith('/settings')} href="/settings" aria-label="Settings">
      <strong>C</strong><span>Settings</span>
    </a>
  </nav>
  <div class="content">
    <slot />
  </div>
</div>

<style>
  :global(:root) {
    color-scheme: light dark;
    --color-page: light-dark(#f5f3ef, #151718);
    --color-page-subtle: light-dark(#f7f7f5, #181b1c);
    --color-surface: light-dark(#fbfaf7, #1d2021);
    --color-surface-strong: light-dark(#ffffff, #25292a);
    --color-surface-muted: light-dark(#f1eee7, #292e2f);
    --color-surface-subtle: light-dark(#efefeb, #191c1d);
    --color-text: light-dark(#151515, #f1f3f2);
    --color-text-muted: light-dark(#666052, #adb5b3);
    --color-border: light-dark(#d2cec4, #3a4140);
    --color-border-muted: light-dark(#b9b2a4, #46504e);
    --color-border-strong: light-dark(#c7c1b4, #505a58);
    --color-accent: light-dark(#2f6f73, #74bcba);
    --color-accent-text: light-dark(#174f52, #a7dcda);
    --color-accent-surface: light-dark(#e4f0ed, #203b3b);
    --color-accent-ring: light-dark(#2f6f7333, #74bcba55);
    --color-danger: light-dark(#9b1c1c, #ff9c9c);
    --color-danger-surface: light-dark(#fff1f1, #3a2224);
    --color-danger-border: light-dark(#efb4b4, #744045);
    --color-warning: light-dark(#a66b00, #e1ad60);
    --color-focus: light-dark(#3b7dd8, #83b4ff);
    --color-timeline: light-dark(#595959, #515958);
    --color-timeline-active: light-dark(#4f4f4f, #46504e);
    --color-media: #111111;
    --color-on-media: #ffffff;
    --shadow-popup: light-dark(rgb(0 0 0 / 16%), rgb(0 0 0 / 45%));
  }

  :global(:root[data-theme='light']) {
    color-scheme: light;
  }

  :global(:root[data-theme='dark']) {
    color-scheme: dark;
  }

  :global(body) {
    margin: 0;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: var(--color-text);
    background: var(--color-page);
  }

  :global(button),
  :global(input),
  :global(select),
  :global(textarea),
  :global(a) {
    border: 1px solid var(--color-border-strong);
    border-radius: 6px;
    background: var(--color-surface-strong);
    color: inherit;
    font: inherit;
    padding: 8px 10px;
    text-decoration: none;
  }

  :global(button) {
    cursor: pointer;
  }

  :global(button:disabled) {
    cursor: default;
    opacity: 0.55;
  }

  .shell {
    --sidebar-width: 71px;

    box-sizing: border-box;
    min-height: 100vh;
    display: grid;
    grid-template-columns: var(--sidebar-width) minmax(0, 1fr);
    transition: grid-template-columns 140ms ease;
  }

  .shell:has(nav:hover),
  .shell:has(nav:focus-within) {
    --sidebar-width: 181px;
  }

  nav {
    box-sizing: border-box;
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 10;
    width: var(--sidebar-width);
    display: grid;
    align-content: start;
    gap: 6px;
    padding: 10px 8px;
    border-right: 1px solid var(--color-border);
    background: var(--color-surface);
    overflow: hidden;
    transition: width 140ms ease;
  }

  nav a {
    box-sizing: border-box;
    display: grid;
    grid-template-columns: 32px 96px;
    gap: 8px;
    align-items: center;
    padding: 6px;
    min-height: 44px;
    white-space: nowrap;
  }

  nav strong {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border-radius: 6px;
    background: var(--color-surface-muted);
    font-size: 13px;
  }

  nav span {
    opacity: 0;
    transition: opacity 120ms ease;
  }

  nav:hover span,
  nav:focus-within span {
    opacity: 1;
  }

  nav a.active {
    border-color: var(--color-accent);
    background: var(--color-accent-surface);
  }

  .content {
    grid-column: 2;
    min-width: 0;
  }

  @media (max-width: 760px) {
    .shell {
      display: block;
      padding-bottom: 64px;
    }

    nav {
      top: auto;
      right: 0;
      width: auto;
      height: 64px;
      grid-auto-flow: column;
      grid-auto-columns: 1fr;
      align-content: center;
      padding: 8px;
      border-right: 0;
      border-top: 1px solid var(--color-border);
    }

    nav:hover,
    nav:focus-within {
      width: auto;
    }

    nav a {
      grid-template-columns: 1fr;
      justify-items: center;
    }

    nav span {
      display: none;
    }
  }
</style>
