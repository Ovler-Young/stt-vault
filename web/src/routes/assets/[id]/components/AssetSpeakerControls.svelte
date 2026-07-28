<script lang="ts">
  import type { AssetDetail, TranscriptSegment } from "$lib/api/types";
  import { recomputeAssetSpeakers, saveAssetSpeaker } from "$lib/api/endpoints";
  import { localSpeakerRows } from "../asset-page.helpers";
  import type { SpeakerEditor } from "../asset-page.types";
  import SpeakerEditorPopover from "./SpeakerEditorPopover.svelte";
  import AssetSpeakersFoldout from "./AssetSpeakersFoldout.svelte";

  export let asset: AssetDetail;
  export let initialMessage = "";
  export let onReload: () => Promise<void> = async () => {};
  export let onError: (message: string) => void = () => {};

  let speakerDrafts: Record<string, string> = {};
  let speakerMessage = "";
  let speakerEditor: SpeakerEditor | null = null;
  let editorName = "";

  $: speakerRows = localSpeakerRows(asset);
  $: syncSpeakerDrafts(asset);

  function syncSpeakerDrafts(currentAsset: AssetDetail) {
    for (const row of localSpeakerRows(currentAsset)) {
      if (!(row.localSpeaker in speakerDrafts)) {
        speakerDrafts[row.localSpeaker] = row.displayName;
      }
    }
  }

  async function saveSpeakerName(localSpeaker: string, displayName?: string) {
    const nextName = (displayName ?? speakerDrafts[localSpeaker])?.trim();
    if (!nextName) return;
    try {
      const speaker = await saveAssetSpeaker(asset.id, localSpeaker, nextName);
      speakerDrafts[localSpeaker] = speaker.display_name;
      speakerMessage = `${localSpeaker} saved as ${speaker.display_name}`;
      speakerEditor = null;
      await onReload();
    } catch (error) {
      speakerMessage = "";
      onError(error instanceof Error ? error.message : String(error));
    }
  }

  async function recomputeSpeakers() {
    try {
      const result = await recomputeAssetSpeakers(asset.id);
      speakerMessage = `${result.assets} asset recomputed`;
      await onReload();
    } catch (error) {
      speakerMessage = "";
      onError(error instanceof Error ? error.message : String(error));
    }
  }

  export function editSpeaker(event: MouseEvent, segment: TranscriptSegment) {
    event.preventDefault();
    const displayName = segment.speaker_name ?? segment.speaker;
    speakerDrafts[segment.speaker] =
      speakerDrafts[segment.speaker] ?? displayName;
    editorName = speakerDrafts[segment.speaker];
    speakerEditor = {
      localSpeaker: segment.speaker,
      displayName,
      x: Math.min(event.clientX, window.innerWidth - 280),
      y: Math.min(event.clientY, window.innerHeight - 150),
    };
  }
</script>

{#if Object.keys(asset.speaker_centroids ?? {}).length}
  <AssetSpeakersFoldout
    rows={speakerRows}
    bind:speakerDrafts
    speakerMessage={speakerMessage || initialMessage}
    onRecompute={recomputeSpeakers}
    onSave={saveSpeakerName}
  />
{/if}

{#if speakerEditor}
  <SpeakerEditorPopover
    editor={speakerEditor}
    bind:editorName
    onSave={saveSpeakerName}
    onCancel={() => (speakerEditor = null)}
  />
{/if}
