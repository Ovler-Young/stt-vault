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
    box-sizing: border-box;
    min-height: 100vh;
    display: grid;
    grid-template-columns: 54px minmax(0, 1fr);
  }

  nav {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 10;
    width: 54px;
    display: grid;
    align-content: start;
    gap: 6px;
    padding: 10px 8px;
    border-right: 1px solid #d2cec4;
    background: #fbfaf7;
    overflow: hidden;
    transition: width 140ms ease;
  }

  nav:hover,
  nav:focus-within {
    width: 164px;
  }

  nav a {
    display: grid;
    grid-template-columns: 32px 96px;
    gap: 8px;
    align-items: center;
    padding: 6px;
    min-height: 34px;
    white-space: nowrap;
  }

  nav strong {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border-radius: 6px;
    background: #f1eee7;
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
    border-color: #2f6f73;
    background: #e4f0ed;
  }

  .content {
    grid-column: 2;
    min-width: 0;
  }

  @media (max-width: 760px) {
    .shell {
      display: block;
      padding-bottom: 54px;
    }

    nav {
      top: auto;
      right: 0;
      width: auto;
      height: 54px;
      grid-auto-flow: column;
      grid-auto-columns: 1fr;
      align-content: center;
      padding: 7px;
      border-right: 0;
      border-top: 1px solid #d2cec4;
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
