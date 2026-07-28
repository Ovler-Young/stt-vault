import { ApiError } from "$lib/api/transport";
import type { FolderTree } from "$lib/api/types";
import {
  getStoredAccessToken,
  login,
  setStoredAccessToken,
} from "$lib/api/auth";

export type HomeAuthState = {
  adminPassword: string;
  authenticated: boolean;
  error: string;
};

type HomeAuthCallbacks = {
  onChange: (state: HomeAuthState) => void;
  onSessionExpired: () => void;
};

export function createHomeAuthController(callbacks: HomeAuthCallbacks) {
  let state: HomeAuthState = {
    adminPassword: "",
    authenticated: Boolean(getStoredAccessToken()),
    error: "",
  };

  function update(changes: Partial<HomeAuthState>) {
    state = { ...state, ...changes };
    callbacks.onChange(state);
  }

  return {
    get state() {
      return state;
    },
    setPassword(adminPassword: string) {
      update({ adminPassword });
    },
    async signIn(): Promise<boolean> {
      try {
        await login(state.adminPassword);
        update({ adminPassword: "", authenticated: true, error: "" });
        return true;
      } catch (requestError) {
        update({ error: errorMessage(requestError) });
        return false;
      }
    },
    handleRequestError(requestError: unknown) {
      if (requestError instanceof ApiError && requestError.status === 401) {
        setStoredAccessToken("");
        update({
          authenticated: false,
          error: "Session expired. Sign in again.",
        });
        callbacks.onSessionExpired();
        return;
      }
      update({ error: errorMessage(requestError) });
    },
    signOut() {
      setStoredAccessToken("");
      update({ authenticated: false, error: "" });
      callbacks.onSessionExpired();
    },
  };
}

export function emptyHomeTree(): FolderTree {
  return { folders: [], assets: [] };
}

function errorMessage(requestError: unknown): string {
  return requestError instanceof Error
    ? requestError.message
    : String(requestError);
}
