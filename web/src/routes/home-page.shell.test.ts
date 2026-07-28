import { describe, expect, it } from "vitest";

import pageSource from "./+page.svelte?raw";
import shellSource from "./components/HomePageShell.svelte?raw";
import workspaceSource from "./components/HomeWorkspace.svelte?raw";

describe("home page shell boundary", () => {
  it("keeps route state in the page and home presentation in the scoped shell", () => {
    expect(pageSource).toContain("<HomePageShell");
    expect(pageSource).not.toContain("<style>");
    expect(workspaceSource).toContain(
      '<nav class="breadcrumbs" aria-label="Current folder">',
    );
    expect(workspaceSource).toContain(
      '<p class="error" aria-live="polite">{error}</p>',
    );
    expect(shellSource).toContain("<style>");
  });
});
