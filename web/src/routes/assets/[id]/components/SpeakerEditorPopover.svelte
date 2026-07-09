<script lang="ts">
  import type { MaybePromise, SpeakerEditor } from '../asset-page.types';

  export let editor: SpeakerEditor;
  export let editorName = '';
  export let onSave: (localSpeaker: string, displayName: string) => MaybePromise = () => {};
  export let onCancel: () => void = () => {};

  function save() {
    void onSave(editor.localSpeaker, editorName);
  }
</script>

<form
  class="speaker-editor"
  style={`left:${editor.x}px; top:${editor.y}px;`}
  on:submit|preventDefault={save}
>
  <small>{editor.localSpeaker}</small>
  <input bind:value={editorName} />
  <div>
    <button type="submit">Save</button>
    <button type="button" on:click={onCancel}>Cancel</button>
  </div>
</form>

<style>
  .speaker-editor {
    position: fixed;
    z-index: 30;
    display: grid;
    gap: 6px;
    width: 260px;
    border: 1px solid #c7c1b4;
    border-radius: 8px;
    background: #fbfaf7;
    padding: 8px;
    box-shadow: 0 12px 32px rgb(0 0 0 / 18%);
  }

  .speaker-editor div {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
  }

  small {
    color: #666052;
    font-size: 11px;
  }
</style>
