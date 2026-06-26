import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from openai import OpenAI

from .media import extract_transcription_chunk


def build_chunks(
    segments: list[dict[str, Any]],
    *,
    max_seconds: float,
    overlap_seconds: float,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    chunk_index = 0
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + max_seconds)
            chunks.append(
                {
                    "chunk_index": chunk_index,
                    "start": max(0.0, cursor - overlap_seconds),
                    "end": chunk_end,
                    "speaker": segment["speaker"],
                    "source_start": start,
                    "source_end": end,
                }
            )
            chunk_index += 1
            cursor = chunk_end
    return chunks


class Transcriber:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        prompt: str,
        concurrency: int,
        retry_seconds: int,
        max_retries: int,
        retry_backoff_seconds: list[int] | None = None,
        on_chunk_done=None,
        on_chunk_retry=None,
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.prompt = prompt
        self.concurrency = max(1, concurrency)
        self.retry_seconds = max(1, retry_seconds)
        self.max_retries = max(1, max_retries)
        self.retry_backoff_seconds = retry_backoff_seconds or [self.retry_seconds]
        self.on_chunk_done = on_chunk_done
        self.on_chunk_retry = on_chunk_retry
        self._pause_lock = threading.Lock()
        self._resume_at = 0.0

    def transcribe_chunks(
        self,
        media_path: Path,
        chunks: list[dict[str, Any]],
        tmp_dir: Path,
    ) -> list[dict[str, Any]]:
        if not chunks:
            return []

        results: list[dict[str, Any]] = []
        chunk_iter = iter(enumerate(chunks))
        pending: set[Future[dict[str, Any]]] = set()

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            while True:
                self._wait_for_pause()
                while len(pending) < self.concurrency:
                    try:
                        index, chunk = next(chunk_iter)
                    except StopIteration:
                        break
                    pending.add(
                        executor.submit(
                            self._transcribe_one,
                            media_path,
                            chunk,
                            tmp_dir,
                            int(chunk.get("chunk_index", index)),
                        )
                    )

                if not pending:
                    break

                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    results.append(future.result())

        return sorted(results, key=lambda item: (item["start"], item["end"]))

    def _transcribe_one(
        self,
        media_path: Path,
        chunk: dict[str, Any],
        tmp_dir: Path,
        index: int,
    ) -> dict[str, Any]:
        chunk_path = tmp_dir / f"chunk-{index:06d}.m4a"
        try:
            for attempt in range(1, self.max_retries + 1):
                try:
                    self._wait_for_pause()
                    extract_transcription_chunk(
                        media_path,
                        chunk_path,
                        float(chunk["start"]),
                        float(chunk["end"]),
                    )

                    kwargs: dict[str, Any] = {"model": self.model}
                    if self.prompt:
                        kwargs["prompt"] = self.prompt

                    with chunk_path.open("rb") as audio_file:
                        response = self.client.audio.transcriptions.create(
                            file=audio_file, **kwargs
                        )

                    text = getattr(response, "text", None)
                    if text is None and isinstance(response, dict):
                        text = response.get("text")

                    result = {
                        "start": chunk["source_start"],
                        "end": chunk["source_end"],
                        "chunk_start": chunk["start"],
                        "chunk_end": chunk["end"],
                        "speaker": chunk["speaker"],
                        "text": (text or "").strip(),
                        "attempts": attempt,
                    }
                    if self.on_chunk_done:
                        self.on_chunk_done(index, result)
                    return result
                except Exception as exc:
                    if attempt >= self.max_retries:
                        raise
                    delay = self._retry_delay(attempt)
                    retry_at = int(time.time()) + delay
                    if self.on_chunk_retry:
                        self.on_chunk_retry(index, attempt, exc, retry_at)
                    self._pause_all(delay)
        finally:
            chunk_path.unlink(missing_ok=True)

        raise RuntimeError("unreachable transcription retry state")

    def _retry_delay(self, attempt: int) -> int:
        index = min(max(0, attempt - 1), len(self.retry_backoff_seconds) - 1)
        return self.retry_backoff_seconds[index]

    def _pause_all(self, delay: int) -> None:
        with self._pause_lock:
            now = time.time()
            self._resume_at = max(self._resume_at, now + delay)
            sleep_for = max(0.0, self._resume_at - now)
        time.sleep(sleep_for)

    def _wait_for_pause(self) -> None:
        with self._pause_lock:
            sleep_for = max(0.0, self._resume_at - time.time())
        if sleep_for > 0:
            time.sleep(sleep_for)
