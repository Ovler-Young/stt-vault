import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { uploadAsset } from "./uploads";

beforeEach(() => {
  vi.stubGlobal(
    "File",
    class extends File {
      slice(start?: number, end?: number, contentType?: string): Blob {
        const blob = super.slice(start, end, contentType);
        // jsdom does not implement Blob.arrayBuffer().
        blob.arrayBuffer = () =>
          new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result as ArrayBuffer);
            reader.onerror = () => reject(reader.error);
            reader.readAsArrayBuffer(blob);
          });
        return blob;
      }
    },
  );
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("uploadAsset", () => {
  it("creates, transfers, and completes a session outside endpoint wrappers", async () => {
    const requests: Array<{ path: string; method: string }> = [];
    const responses = [
      { id: "upload-1", filename: "recording.wav", size: 3, offset: 0 },
      { id: "upload-1", filename: "recording.wav", size: 3, offset: 3 },
      { id: "asset-1", status: "queued" },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init: RequestInit) => {
        requests.push({ path, method: init.method ?? "GET" });
        return new Response(JSON.stringify(responses.shift()), { status: 200 });
      }),
    );

    await expect(
      uploadAsset(new File(["wav"], "recording.wav")),
    ).resolves.toEqual({
      id: "asset-1",
      status: "queued",
    });

    expect(requests).toEqual([
      { path: "/api/uploads", method: "POST" },
      { path: "/api/uploads/upload-1", method: "PUT" },
      { path: "/api/uploads/upload-1/complete", method: "POST" },
    ]);
  });

  it("transfers bounded chunks from a resumed offset", async () => {
    const chunkSize = 8 * 1024 * 1024;
    const bytes = new Uint8Array(chunkSize + 5);
    bytes[2] = 17;
    bytes[chunkSize + 1] = 29;
    bytes[chunkSize + 2] = 41;
    bytes[chunkSize + 4] = 53;
    const file = new File([bytes], "recording.wav", { lastModified: 123 });
    localStorage.setItem(
      `stt-vault-upload:recording.wav:${file.size}:123`,
      "upload-1",
    );
    let offset = 2;
    const chunks: Array<{ range: string | null; bytes: Uint8Array }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init: RequestInit) => {
        if (init.method === "PUT") {
          expect(Object.prototype.toString.call(init.body)).toBe(
            "[object ArrayBuffer]",
          );
          const chunk = new Uint8Array(init.body as ArrayBuffer);
          chunks.push({
            range: new Headers(init.headers).get("Content-Range"),
            bytes: chunk,
          });
          offset += chunk.byteLength;
        }
        return new Response(
          JSON.stringify(
            path.endsWith("/complete")
              ? { id: "asset-1", status: "queued" }
              : {
                  id: "upload-1",
                  filename: file.name,
                  size: file.size,
                  offset,
                },
          ),
          { status: 200 },
        );
      }),
    );
    const progress: number[] = [];

    await uploadAsset(file, file.name, ({ uploaded }) =>
      progress.push(uploaded),
    );

    expect(chunks.map(({ range }) => range)).toEqual([
      `bytes 2-${chunkSize + 1}/${file.size}`,
      `bytes ${chunkSize + 2}-${file.size - 1}/${file.size}`,
    ]);
    expect(chunks.map(({ bytes }) => bytes.byteLength)).toEqual([chunkSize, 3]);
    expect(
      chunks[0].bytes.every((value, index) => value === bytes[index + 2]),
    ).toBe(true);
    expect(chunks[1].bytes).toEqual(bytes.subarray(chunkSize + 2));
    expect(progress).toEqual([2, chunkSize + 2, file.size]);
  });
});
