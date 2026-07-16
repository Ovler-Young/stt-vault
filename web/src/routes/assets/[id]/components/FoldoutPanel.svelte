<script lang="ts">
  export let summary: string;
  export let open = false;
</script>

<details {open}>
  <summary>{summary}</summary>
  <slot />
</details>

<style>
  details {
    border: 1px solid #d2cec4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 6px 8px;
  }

  summary {
    cursor: pointer;
    font-weight: 700;
    font-size: 13px;
  }
</style>
