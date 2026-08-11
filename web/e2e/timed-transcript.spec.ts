import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { readFileSync } from "node:fs";

const timedUnitText = "fixture unit";
const longUnbrokenUnitText = "longunbrokenfixturetext".repeat(12);
const browserErrors = new WeakMap<Page, string[]>();

test.beforeEach(async ({ page }) => {
  const pageErrors: string[] = [];
  browserErrors.set(page, pageErrors);
  page.on("console", (message) => {
    if (message.type() === "error") pageErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.addInitScript(() => {
    let playCalls = 0;
    Object.defineProperty(window, "__timedTranscriptPlayCalls", {
      configurable: true,
      get: () => playCalls,
    });
    Object.defineProperty(HTMLMediaElement.prototype, "play", {
      configurable: true,
      value: () => {
        playCalls += 1;
        return Promise.resolve();
      },
    });
  });
});

test.afterEach(({ page }) => {
  expect(browserErrors.get(page)).toEqual([]);
});

test("persists fixture timed units and seeks from transcript controls", async ({
  page,
  request,
}) => {
  const token = await issueToken(request);
  const headers = { Authorization: `Bearer ${token}` };
  const assetId = await uploadFixtureAsset(
    request,
    headers,
    "timed-fixture.wav",
  );
  const detail = await waitForTerminalAsset(request, headers, assetId);
  const segment = detail.transcript_segments[0];
  const unit = segment.timed_units[0];
  const absoluteStartMs = Math.floor(segment.chunk_start * 1000 + 0.5) + 50;

  expect(unit).toMatchObject({
    unit_index: 0,
    text: timedUnitText,
    start_ms: absoluteStartMs,
    end_ms: absoluteStartMs + 100,
    token_kind: "word",
  });
  expect(segment.chunk_start).toBeGreaterThan(0);

  await page.addInitScript((accessToken) => {
    localStorage.setItem("stt-vault-access-token", accessToken);
  }, token);
  await page.goto(`/assets/${assetId}`);

  const timedControl = page.getByRole("button", {
    name: new RegExp(timedUnitText),
  });
  await expect(timedControl).toBeVisible();
  await expectViewportLayout(page, timedControl, { width: 1280, height: 720 });
  await expectViewportLayout(
    page,
    page.getByRole("button", {
      name: new RegExp(longUnbrokenUnitText),
    }),
    { width: 1280, height: 720 },
  );
  await page.waitForFunction(
    () =>
      document.querySelector("audio")?.readyState >=
      HTMLMediaElement.HAVE_METADATA,
  );
  await timedControl.click();
  await expectMediaSeek(page, absoluteStartMs, 1);

  await page.locator("audio").evaluate((element) => {
    element.dispatchEvent(new Event("play"));
    element.dispatchEvent(new Event("timeupdate"));
    element.dispatchEvent(new Event("seeked"));
  });
  await expect
    .poll(() =>
      timedControl.evaluate((button) => ({
        className: button.className,
        currentTime: document.querySelector("audio")?.currentTime,
      })),
    )
    .toMatchObject({
      className: expect.stringContaining("active"),
      currentTime: absoluteStartMs / 1000,
    });

  await timedControl.focus();
  await page.keyboard.press("Enter");
  await expectMediaSeek(page, absoluteStartMs, 2);
  await page.keyboard.press("Space");
  await expectMediaSeek(page, absoluteStartMs, 3);

  await page.setViewportSize({ width: 393, height: 852 });
  await expectViewportLayout(
    page,
    page.getByRole("button", {
      name: new RegExp(longUnbrokenUnitText),
    }),
    { width: 393, height: 852 },
  );

  const segmentOnlyId = await uploadFixtureAsset(
    request,
    headers,
    "segment-only.wav",
  );
  await waitForTerminalAsset(request, headers, segmentOnlyId);
  await page.goto(`/assets/${segmentOnlyId}`);
  await expect(page.locator("[data-timed-unit-control]")).toHaveCount(0);
  const fallbackControl = page.locator(".transcript button");
  await expect(fallbackControl).toBeVisible();
  await expectViewportLayout(page, fallbackControl, {
    width: 393,
    height: 852,
  });
});

async function issueToken(request: APIRequestContext): Promise<string> {
  const response = await request.post("/api/auth/token", {
    data: { password: "e2e-admin-password" },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).access_token as string;
}

async function uploadFixtureAsset(
  request: APIRequestContext,
  headers: Record<string, string>,
  name: string,
): Promise<string> {
  const response = await request.post("/api/assets", {
    headers,
    multipart: {
      file: { name, mimeType: "audio/wav", buffer: wavFixture() },
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).id as string;
}

async function waitForTerminalAsset(
  request: APIRequestContext,
  headers: Record<string, string>,
  assetId: string,
): Promise<{
  transcript_segments: Array<{
    chunk_start: number;
    timed_units: Array<Record<string, unknown>>;
  }>;
  error: unknown;
  job: { status: string; error: unknown } | null;
  events: Array<{ level: string }> | null;
}> {
  await expect
    .poll(async () => {
      const response = await request.get(`/api/assets/${assetId}`, { headers });
      expect(response.ok()).toBeTruthy();
      const detail = (await response.json()) as {
        error: unknown;
        events: Array<{ level: string }> | null;
        job: { error: unknown; status: string } | null;
        status: string;
      };
      expect(detail.error).toBeNull();
      expect(detail.job).toMatchObject({ status: "success", error: null });
      expect(
        (detail.events ?? []).filter(
          (event) => event.level === "error" || event.level === "warning",
        ),
      ).toEqual([]);
      return detail;
    })
    .toMatchObject({ status: "success" });
  const response = await request.get(`/api/assets/${assetId}`, { headers });
  return response.json() as Promise<{
    transcript_segments: Array<{
      chunk_start: number;
      timed_units: Array<Record<string, unknown>>;
    }>;
  }>;
}

async function expectMediaSeek(page: Page, startMs: number, calls: number) {
  await expect
    .poll(() =>
      page.locator("audio").evaluate((element) => ({
        currentTime: element.currentTime,
        playCalls: (
          window as typeof window & { __timedTranscriptPlayCalls: number }
        ).__timedTranscriptPlayCalls,
      })),
    )
    .toEqual({ currentTime: startMs / 1000, playCalls: calls });
}

async function expectViewportLayout(
  page: Page,
  locator: ReturnType<Page["locator"]>,
  viewport: { width: number; height: number },
) {
  expect(page.viewportSize()).toEqual(viewport);
  const bounds = await locator.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds?.x).toBeGreaterThanOrEqual(0);
  expect(bounds?.y).toBeGreaterThanOrEqual(0);
  expect((bounds?.x ?? 0) + (bounds?.width ?? 0)).toBeLessThanOrEqual(
    viewport.width,
  );
  expect((bounds?.y ?? 0) + (bounds?.height ?? 0)).toBeLessThanOrEqual(
    viewport.height,
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(viewport.width);
}

function wavFixture(): Buffer {
  const fixturePath = process.env.E2E_TIMED_TRANSCRIPT_AUDIO_PATH;
  if (!fixturePath)
    throw new Error(
      "Timed transcript fixture audio was not created by global setup",
    );
  return readFileSync(fixturePath);
}
