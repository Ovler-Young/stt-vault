import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/timed-transcript.global.ts",
  timeout: 120_000,
  workers: 1,
  use: {
    baseURL:
      process.env.E2E_TIMED_TRANSCRIPT_BASE_URL ?? "http://127.0.0.1:18080",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
