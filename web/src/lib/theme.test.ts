import { afterEach, describe, expect, it, vi } from "vitest";
import { getThemePreference, setThemePreference } from "./theme";

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
  vi.restoreAllMocks();
});

describe("theme preferences", () => {
  it("uses the system preference when browser storage cannot be read", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Storage access denied", "SecurityError");
    });

    expect(getThemePreference()).toBe("system");
  });

  it("applies the selected theme when browser storage cannot be written", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Storage access denied", "SecurityError");
    });

    setThemePreference("dark");

    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
