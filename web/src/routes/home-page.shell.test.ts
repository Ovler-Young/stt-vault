import { flushSync, mount, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import pageSource from "./+page.svelte?raw";
import shellSource from "./components/HomePageShell.svelte?raw";
import workspaceSource from "./components/HomeWorkspace.svelte?raw";
import HomePageShell from "./components/HomePageShell.svelte";
import type { HomePageShellProps } from "./home-page.types";

let mountedShell: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement | undefined;

afterEach(() => {
  if (mountedShell) unmount(mountedShell);
  target?.remove();
  mountedShell = undefined;
  target = undefined;
});

function renderShell(
  auth: Pick<HomePageShellProps, "authenticated" | "authPending">,
) {
  if (mountedShell) unmount(mountedShell);
  target?.remove();
  target = document.createElement("div");
  document.body.append(target);
  mountedShell = mount(HomePageShell, {
    target,
    props: {
      allAssetCount: 0,
      folderCount: 0,
      authRequired: true,
      ...auth,
      busy: false,
      adminPassword: "",
      selectedFolderId: null,
      flatFolders: [],
      breadcrumbs: [],
      visibleAssets: [],
      currentFolder: null,
      folderMoveOptions: [],
      folderMoveTarget: "",
      uploadEntryCount: 0,
      uploadProgress: null,
      error: "",
      batchResults: [],
      assetTargets: {},
      onRefresh: vi.fn(),
      onSignOut: vi.fn(),
      onAdminPasswordChange: vi.fn(),
      onLogin: vi.fn(),
      onSelectFolder: vi.fn(),
      onAddFolder: vi.fn(),
      onFileChange: vi.fn(),
      onDirectoryChange: vi.fn(),
      onUpload: vi.fn(),
      onRenameFolder: vi.fn(),
      onFolderMoveTargetChange: vi.fn(),
      onMoveFolder: vi.fn(),
      onDeleteFolder: vi.fn(),
      onAssetTargetChange: vi.fn(),
      onMoveAsset: vi.fn(),
      onDeleteAsset: vi.fn(),
    },
  });
  flushSync();
}

describe("home page shell boundary", () => {
  it("keeps route state in the page and home presentation in the scoped shell", () => {
    expect(pageSource).toContain("<HomePageShell");
    expect(pageSource).not.toContain("<style>");
    expect(pageSource).not.toContain("createFolder");
    expect(pageSource).not.toContain("setInterval(");
    expect(pageSource.split("\n").length).toBeLessThan(220);
    expect(workspaceSource).toContain(
      '<nav class="breadcrumbs" aria-label="Current folder">',
    );
    expect(workspaceSource).toContain(
      '<p class="error" aria-live="polite">{error}</p>',
    );
    expect(workspaceSource).toContain('accept="audio/*,video/*"');
    expect(workspaceSource).toContain("multiple");
    expect(workspaceSource).toContain(
      "onFileChange(event.currentTarget.files)",
    );
    expect(pageSource).toContain("file.webkitRelativePath || file.name");
    expect(pageSource).toContain(
      "getSingleUploadAssetId(source, result.results)",
    );
    expect(pageSource).toContain("await goto(`/assets/${assetId}`)");
    expect(pageSource).toContain('selectFiles(files, "directory")');
    expect(workspaceSource).toContain(
      'onclick={(event) => (event.currentTarget.value = "")}',
    );
    expect(shellSource).toContain("<style>");
  });

  it("withholds the password input while stored-token renewal is pending", () => {
    expect(shellSource).toContain("{#if authRequired && !authenticated}");
    expect(shellSource).toContain("{#if !authPending}");
    expect(pageSource).toContain(
      "authPending={authState.authenticationPending}",
    );
  });

  it("renders the password input only after stored-token renewal rejects", () => {
    renderShell({ authenticated: true, authPending: false });
    expect(target?.querySelector('input[type="password"]')).toBeNull();

    renderShell({ authenticated: false, authPending: true });
    expect(target?.querySelector('input[type="password"]')).toBeNull();

    renderShell({ authenticated: false, authPending: false });
    expect(target?.querySelector('input[type="password"]')).not.toBeNull();
  });

  it("clears the ordinary picker before selection so the same files can be selected again", () => {
    renderShell({ authenticated: true, authPending: false });
    const picker = target?.querySelector<HTMLInputElement>(
      'input[type="file"]:not([webkitdirectory])',
    );
    expect(picker).toBeTruthy();
    const setValue = vi.fn();
    Object.defineProperty(picker!, "value", {
      configurable: true,
      get: () => "recording.wav",
      set: setValue,
    });

    picker!.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(setValue).toHaveBeenCalledExactlyOnceWith("");
  });
});
