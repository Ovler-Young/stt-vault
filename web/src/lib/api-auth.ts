import { request } from "$lib/api-transport";
import type { AccessToken } from "$lib/api-types";

const accessTokenKey = "stt-vault-access-token";
export type { AccessToken } from "$lib/api-types";
export function getStoredAccessToken(): string {
  if (typeof localStorage === "undefined") return "";
  const storedToken = localStorage.getItem(accessTokenKey);
  if (storedToken) return storedToken;
  if (typeof sessionStorage === "undefined") return "";
  const sessionToken = sessionStorage.getItem(accessTokenKey);
  if (sessionToken) localStorage.setItem(accessTokenKey, sessionToken);
  return sessionToken ?? "";
}
export function setStoredAccessToken(value: string) {
  if (typeof localStorage === "undefined") return;
  if (value) localStorage.setItem(accessTokenKey, value);
  else localStorage.removeItem(accessTokenKey);
}
export function authenticatedResourceUrl(
  path: string,
  params = new URLSearchParams(),
): string {
  const token = getStoredAccessToken();
  if (token) params.set("access_token", token);
  const query = params.toString();
  return `${path}${query ? `?${query}` : ""}`;
}
export async function login(password: string): Promise<AccessToken> {
  const token = await request<AccessToken>("/api/auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  setStoredAccessToken(token.access_token);
  return token;
}
