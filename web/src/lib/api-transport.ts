import { getStoredAccessToken } from "$lib/api-auth";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getStoredAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok)
    throw new ApiError(
      response.status,
      `${response.status} ${await response.text()}`,
    );
  return response.json() as Promise<T>;
}
