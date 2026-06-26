<script lang="ts">
  import { page } from '$app/stores';
</script>

<svelte:head>
  <title>STT Vault</title>
</svelte:head>

<div class="shell">
  <nav>
    <a class:active={$page.url.pathname === '/'} href="/">Dashboard</a>
    <a class:active={$page.url.pathname.startsWith('/jobs')} href="/jobs">Jobs</a>
    <a class:active={$page.url.pathname.startsWith('/speakers')} href="/speakers">Speakers</a>
    <a class:active={$page.url.pathname.startsWith('/settings')} href="/settings">Settings</a>
  </nav>
  <slot />
</div>

<style>
  :global(body) {
    margin: 0;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #151515;
    background: #f5f3ef;
  }

  :global(button),
  :global(input),
  :global(a) {
    border: 1px solid #c7c1b4;
    border-radius: 6px;
    background: white;
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
    min-height: 100vh;
  }

  nav {
    position: sticky;
    top: 0;
    z-index: 10;
    display: flex;
    gap: 8px;
    padding: 10px 16px;
    border-bottom: 1px solid #d2cec4;
    background: #fbfaf7;
  }

  nav a.active {
    border-color: #2f6f73;
    background: #e4f0ed;
  }
</style>
