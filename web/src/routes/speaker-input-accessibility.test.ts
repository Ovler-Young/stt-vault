import { describe, expect, it } from "vitest";

import assetSpeakersFoldoutSource from "./assets/[id]/components/AssetSpeakersFoldout.svelte?raw";
import speakerEditorPopoverSource from "./assets/[id]/components/SpeakerEditorPopover.svelte?raw";
import speakersPageSource from "./speakers/+page.svelte?raw";

type SpeakerInput = {
  component: string;
  source: string;
  expectedLabel: string;
  renderedLabel: string;
};

const speakerInputs: SpeakerInput[] = [
  {
    component: "speakers page",
    source: speakersPageSource,
    expectedLabel: "Rename speaker ${speaker.display_name}",
    renderedLabel: "Rename speaker Alice",
  },
  {
    component: "asset speakers foldout",
    source: assetSpeakersFoldoutSource,
    expectedLabel: "Rename speaker ${speaker.localSpeaker}",
    renderedLabel: "Rename speaker SPEAKER_00",
  },
  {
    component: "speaker editor popover",
    source: speakerEditorPopoverSource,
    expectedLabel: "Rename speaker ${editor.localSpeaker}",
    renderedLabel: "Rename speaker SPEAKER_00",
  },
];

function inputMarkup(source: string, component: string): string {
  const input = source.match(/<input\b[^>]*\/>/);
  if (!input) throw new Error(`No input found in ${component}`);
  return input[0];
}

describe("speaker name inputs", () => {
  it.each(speakerInputs)(
    "gives $component a programmatic name",
    ({ component, source, expectedLabel, renderedLabel }) => {
      const markup = inputMarkup(source, component);

      expect(markup).toContain(`aria-label={\`${expectedLabel}\`}`);
      document.body.innerHTML = markup
        .replace(/bind:value=\{[^}]+\}/, "")
        .replace(/aria-label=\{`[^`]+`\}/, `aria-label="${renderedLabel}"`);

      const input = document.querySelector("input");
      expect(input).not.toBeNull();
      expect(input?.getAttribute("aria-label")).toBe(renderedLabel);
    },
  );
});
