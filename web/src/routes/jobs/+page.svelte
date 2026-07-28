<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import type { Job } from "$lib/api/types";
  import { fetchJobs } from "$lib/api/endpoints";
  import { formatDate, formatTime } from "$lib/formatting/date-time";
  import { hasActivePolling } from "$lib/state/polling";

  let jobs: Job[] = [];
  let error = "";
  let poll: ReturnType<typeof setInterval> | null = null;
  let isLoading = false;
  let announcement = "";
  let jobStateSignature = "";

  onMount(async () => {
    await load();
  });

  onDestroy(() => {
    if (poll) clearInterval(poll);
  });

  async function load() {
    isLoading = true;
    try {
      jobs = await fetchJobs();
      const nextSignature = jobs
        .map((job) => `${job.asset_id}:${job.status}:${job.stage ?? ""}`)
        .join("|");
      if (nextSignature !== jobStateSignature) {
        announcement = `${jobs.filter((job) => job.status === "processing").length} jobs processing.`;
        jobStateSignature = nextSignature;
      }
      updatePolling();
      error = "";
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
      announcement = "Job refresh failed.";
    } finally {
      isLoading = false;
    }
  }

  function updatePolling() {
    const shouldPoll = hasActivePolling(jobs);
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
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    <p class="sr-only" aria-live="polite" aria-atomic="true">
      {announcement}
    </p>
    <div class="jobs" aria-busy={isLoading}>
      {#each jobs as job}
        <a href={`/assets/${job.asset_id}`}>
          <strong>{job.filename}</strong>
          <span
            >{job.status} · {job.stage ?? "queued"} · {formatTime(
              job.duration,
            )}</span
          >
          <span
            >{job.progress_done_chunks}/{job.progress_total_chunks} chunks · retries
            {job.progress_failed_chunks}</span
          >
          {#if job.next_retry_at}<span
              >retry after {formatDate(job.next_retry_at)}</span
            >{/if}
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
    border: 1px solid var(--color-border);
    border-radius: 8px;
    background: var(--color-surface);
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
    color: var(--color-text-muted);
    font-size: 12px;
  }

  .error {
    margin-top: 12px;
    color: var(--color-danger);
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
</style>
