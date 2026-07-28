import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "$lib/api/transport";

const { getStoredAccessToken, login, setStoredAccessToken } = vi.hoisted(
  () => ({
    getStoredAccessToken: vi.fn(),
    login: vi.fn(),
    setStoredAccessToken: vi.fn(),
  }),
);

vi.mock("$lib/api/auth", () => ({
  getStoredAccessToken,
  login,
  setStoredAccessToken,
}));

import { createHomeAuthController, type HomeAuthState } from "./home-page.auth";

describe("home page authentication controller", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    getStoredAccessToken.mockReturnValue("");
  });

  it("emits authenticated state after a successful sign in", async () => {
    login.mockResolvedValue({ access_token: "token" });
    const states: HomeAuthState[] = [];
    const controller = createHomeAuthController({
      onChange: (state) => states.push(state),
      onSessionExpired: vi.fn(),
    });
    controller.setPassword("secret");

    await expect(controller.signIn()).resolves.toBe(true);

    expect(controller.state.authenticated).toBe(true);
    expect(controller.state.adminPassword).toBe("");
    expect(states.at(-1)?.error).toBe("");
  });

  it("clears credentials and notifies the route when a session expires", () => {
    const onSessionExpired = vi.fn();
    const controller = createHomeAuthController({
      onChange: vi.fn(),
      onSessionExpired,
    });

    controller.handleRequestError(new ApiError(401, "expired"));

    expect(setStoredAccessToken).toHaveBeenCalledWith("");
    expect(controller.state.authenticated).toBe(false);
    expect(controller.state.error).toBe("Session expired. Sign in again.");
    expect(onSessionExpired).toHaveBeenCalledOnce();
  });
});
