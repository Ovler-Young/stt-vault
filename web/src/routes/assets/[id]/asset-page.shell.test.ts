import { describe, expect, it } from "vitest";

import pageSource from "./+page.svelte?raw";
import shellSource from "./components/AssetPageShell.svelte?raw";

describe("asset page shell boundary", () => {
  it("keeps page lifecycle in the route and layout composition in the scoped shell", () => {
    expect(pageSource).toContain("<AssetPageShell");
    expect(pageSource).not.toContain("<ResizableAssetWorkspace>");
    expect(pageSource).not.toContain("<style>");

    expect(shellSource).toContain("<ResizableAssetWorkspace>");
    expect(shellSource).toContain("<AssetMediaPane");
    expect(shellSource).toContain("<TranscriptPane");
    expect(shellSource).toContain('<p class="error" aria-live="polite">');
    expect(shellSource).toContain("<style>");
  });
});
