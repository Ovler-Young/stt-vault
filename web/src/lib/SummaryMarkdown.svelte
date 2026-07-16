<script lang="ts">
  import { onMount } from 'svelte';

  export let markdown = '';
  export let onSeek: (time: number) => void = () => {};

  type RenderMarkdown = (value: string) => string;

  const timestampPattern = /\[(\d{1,2}:\d{2}(?::\d{2})?)\](?!\()/g;
  let renderMarkdown: RenderMarkdown | null = null;
  let summaryElement: HTMLElement;

  $: renderedMarkdown = renderMarkdown ? renderMarkdown(markdown) : '';

  onMount(() => {
    summaryElement.addEventListener('click', handleClick);
    void loadRenderer();
    return () => summaryElement.removeEventListener('click', handleClick);
  });

  async function loadRenderer() {
    const [{ marked }, { default: DOMPurify }] = await Promise.all([
      import('marked'),
      import('dompurify')
    ]);
    renderMarkdown = (value) =>
      DOMPurify.sanitize(marked.parse(linkifyTimestamps(value), { async: false }) as string);
  }

  function linkifyTimestamps(value: string) {
    return value.replace(timestampPattern, (match, timestamp: string) => {
      const seconds = timestampToSeconds(timestamp);
      return seconds === null ? match : `[${timestamp}](#timestamp=${seconds})`;
    });
  }

  function timestampToSeconds(value: string) {
    const parts = value.split(':').map(Number);
    if (parts.some((part) => !Number.isInteger(part) || part < 0)) return null;
    const [hours, minutes, seconds] = parts.length === 3 ? parts : [0, parts[0], parts[1]];
    if (minutes > 59 || seconds > 59) return null;
    return hours * 3600 + minutes * 60 + seconds;
  }

  function handleClick(event: MouseEvent) {
    const link = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="#timestamp="]');
    if (!link || !summaryElement.contains(link)) return;
    const timestamp = Number(link.hash.slice('#timestamp='.length));
    if (!Number.isFinite(timestamp) || timestamp < 0) return;
    event.preventDefault();
    onSeek(timestamp);
  }
</script>

<article class="summary-markdown" bind:this={summaryElement}>
  {@html renderedMarkdown}
</article>

<style>
  .summary-markdown {
    color: #252525;
    line-height: 1.5;
  }

  .summary-markdown :global(h2),
  .summary-markdown :global(h3),
  .summary-markdown :global(p),
  .summary-markdown :global(ul) {
    margin: 0 0 10px;
  }

  .summary-markdown :global(h2) {
    font-size: 15px;
  }

  .summary-markdown :global(h3) {
    font-size: 13px;
  }

  .summary-markdown :global(ul) {
    padding-left: 20px;
  }

  .summary-markdown :global(a[href^='#timestamp=']) {
    color: #1f6266;
    font-weight: 600;
    text-decoration: underline;
  }
</style>
