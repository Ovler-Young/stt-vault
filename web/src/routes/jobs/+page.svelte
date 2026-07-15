<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { fetchJobs, type Job } from '$lib/api';
  import { formatDate, formatTime } from '$lib/format';

  let jobs: Job[] = [];
  let error = '';
  let poll: ReturnType<typeof setInterval> | null = null;

  onMount(async () => {
    await load();
  });

  onDestroy(() => {
    if (poll) clearInterval(poll);
  });

  async function load() {
    try {
      jobs = await fetchJobs();
      updatePolling();
      error = '';
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  function updatePolling() {
    const shouldPoll = jobs.some((job) => job.status === 'queued' || job.status === 'processing');
    if (shouldPoll && !poll) {
      poll = setInterval(load, 3000);
    } else if (!shouldPoll && poll) {
      clearInterval(poll);
      poll = null;
    }
  }
</script>

<main>
  <section class="panel">
    <header>
      <h1>Jobs</h1>
      <button on:click={load}>Refresh</button>
    </header>
    {#if error}<p class="error">{error}</p>{/if}
    <div class="jobs">
      {#each jobs as job}
        <a href={`/assets/${job.asset_id}`}>
          <strong>{job.filename}</strong>
          <span>{job.status} · {job.stage ?? 'queued'} · {formatTime(job.duration)}</span>
          <span>{job.progress_done_chunks}/{job.progress_total_chunks} chunks · retries {job.progress_failed_chunks}</span>
          {#if job.next_retry_at}<span>retry after {formatDate(job.next_retry_at)}</span>{/if}
          {#if job.error}<code>{JSON.stringify(job.error)}</code>{/if}
        </a>
      {/each}
    </div>
  </section>
</main>

<style>
  main {
    padding: 16px;
  }

  .panel {
    border: 1px solid #d2cec4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 16px;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  h1,
  p {
    margin: 0;
  }

  .jobs {
    display: grid;
    gap: 8px;
    margin-top: 14px;
  }

  .jobs a {
    display: grid;
    gap: 4px;
  }

  span,
  code {
    color: #666052;
    font-size: 12px;
  }

  .error {
    margin-top: 12px;
    color: #9b1c1c;
  }
</style>
