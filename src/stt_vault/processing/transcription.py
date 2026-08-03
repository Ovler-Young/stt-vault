import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from openai import OpenAI

from stt_vault.core.models.records import SpeakerSegment, TranscriptChunk, TranscriptSegment

from .media_transcoding import extract_audio_chunk

ChunkExtractor = Callable[[Path, Path, float, float], Path]


class TranscriptionResponse(Protocol):
    text: str | None


@dataclass(frozen=True)
class _NormalizedTranscriptionResponse:
    text: str | None


class AudioTranscriptions(Protocol):
    def create(
        self,
        *,
        file: BinaryIO,
        model: str,
        prompt: str | None = None,
    ) -> TranscriptionResponse: ...


class TranscriptionAudio(Protocol):
    transcriptions: AudioTranscriptions


class TranscriptionClient(Protocol):
    audio: TranscriptionAudio


class TranscriptionClientFactory(Protocol):
    def __call__(self, *, api_key: str, base_url: str) -> TranscriptionClient: ...


class Clock(Protocol):
    def __call__(self) -> float: ...


class Sleeper(Protocol):
    def __call__(self, seconds: float) -> None: ...


class ChunkDoneCallback(Protocol):
    def __call__(self, index: int, result: TranscriptSegment) -> None: ...


class ChunkRetryCallback(Protocol):
    def __call__(self, index: int, attempt: int, error: Exception, retry_at: int) -> None: ...


def _normalize_transcription_response(response: object) -> TranscriptionResponse:
    if isinstance(response, Mapping):
        if "text" not in response:
            raise TypeError("transcription response mapping is missing text")
        text = response["text"]
    else:
        missing = object()
        text = getattr(response, "text", missing)
        if text is missing:
            raise TypeError("transcription response is missing text")
    if text is not None and not isinstance(text, str):
        raise TypeError("transcription response text must be a string or null")
    return _NormalizedTranscriptionResponse(text=text)


def build_chunks(
    segments: list[SpeakerSegment],
    *,
    max_seconds: float,
) -> list[TranscriptChunk]:
    chunks: list[TranscriptChunk] = []
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
                    "start": cursor,
                    "end": chunk_end,
                    "speaker": segment["speaker"],
                }
            )
            chunk_index += 1
            cursor = chunk_end
    return chunks


def build_transcription_plan(
    diarization: dict[str, list[SpeakerSegment]], *, max_seconds: float
) -> list[TranscriptChunk]:
    return build_chunks(diarization["raw_segments"], max_seconds=max_seconds)


def transcript_chunks_match_plan(
    existing_chunks: list[TranscriptSegment], chunks: list[TranscriptChunk]
) -> bool:
    for existing in existing_chunks:
        index = existing.get("chunk_index")
        if not isinstance(index, int) or index < 0 or index >= len(chunks):
            return False
        expected = chunks[index]
        if existing.get("speaker") != expected["speaker"]:
            return False
        for field in ("start", "end", "chunk_start", "chunk_end"):
            actual = existing.get(field)
            expected_value = expected["start"] if field.endswith("start") else expected["end"]
            if not isinstance(actual, int | float) or abs(float(actual) - expected_value) > 0.001:
                return False
    return True


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
        on_chunk_done: ChunkDoneCallback | None = None,
        on_chunk_retry: ChunkRetryCallback | None = None,
        client: TranscriptionClient | None = None,
        client_factory: TranscriptionClientFactory = OpenAI,
        chunk_extractor: ChunkExtractor = extract_audio_chunk,
        clock: Clock = time.time,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        self.client = client or client_factory(api_key=api_key, base_url=base_url)
        self.chunk_extractor = chunk_extractor
        self.model = model
        self.prompt = prompt
        self.concurrency = max(1, concurrency)
        self.retry_seconds = max(1, retry_seconds)
        self.max_retries = max(1, max_retries)
        self.retry_backoff_seconds = retry_backoff_seconds or [self.retry_seconds]
        self.on_chunk_done = on_chunk_done
        self.on_chunk_retry = on_chunk_retry
        self.clock = clock
        self.sleeper = sleeper
        self._pause_lock = threading.Lock()
        self._resume_at = 0.0

    def transcribe_chunks(
        self,
        media_path: Path,
        chunks: list[TranscriptChunk],
        tmp_dir: Path,
    ) -> list[TranscriptSegment]:
        if not chunks:
            return []

        results: list[TranscriptSegment] = []
        chunk_iter = iter(enumerate(chunks))
        pending: set[Future[TranscriptSegment]] = set()

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
        chunk: TranscriptChunk,
        tmp_dir: Path,
        index: int,
    ) -> TranscriptSegment:
        chunk_path = tmp_dir / f"chunk-{index:06d}.m4a"
        try:
            for attempt in range(1, self.max_retries + 1):
                try:
                    result = self._transcribe_attempt(media_path, chunk, chunk_path, attempt)
                    if self.on_chunk_done:
                        self.on_chunk_done(index, result)
                    return result
                except Exception as exc:
                    if attempt >= self.max_retries:
                        raise
                    delay = self._retry_delay(attempt)
                    retry_at = int(self.clock()) + delay
                    if self.on_chunk_retry:
                        self.on_chunk_retry(index, attempt, exc, retry_at)
                    self._pause_all(delay)
        finally:
            chunk_path.unlink(missing_ok=True)

        raise RuntimeError("unreachable transcription retry state")

    def _transcribe_attempt(
        self,
        media_path: Path,
        chunk: TranscriptChunk,
        chunk_path: Path,
        attempt: int,
    ) -> TranscriptSegment:
        self._wait_for_pause()
        self.chunk_extractor(
            media_path,
            chunk_path,
            float(chunk["start"]),
            float(chunk["end"]),
        )

        kwargs: dict[str, str] = {"model": self.model}
        if self.prompt:
            kwargs["prompt"] = self.prompt

        with chunk_path.open("rb") as audio_file:
            response = _normalize_transcription_response(
                self.client.audio.transcriptions.create(file=audio_file, **kwargs)
            )

        return {
            "start": chunk["start"],
            "end": chunk["end"],
            "chunk_start": chunk["start"],
            "chunk_end": chunk["end"],
            "speaker": chunk["speaker"],
            "text": (response.text or "").strip(),
            "attempts": attempt,
        }

    def _retry_delay(self, attempt: int) -> int:
        index = min(max(0, attempt - 1), len(self.retry_backoff_seconds) - 1)
        return self.retry_backoff_seconds[index]

    def _pause_all(self, delay: int) -> None:
        with self._pause_lock:
            now = self.clock()
            self._resume_at = max(self._resume_at, now + delay)
            sleep_for = max(0.0, self._resume_at - now)
        self.sleeper(sleep_for)

    def _wait_for_pause(self) -> None:
        with self._pause_lock:
            sleep_for = max(0.0, self._resume_at - self.clock())
        if sleep_for > 0:
            self.sleeper(sleep_for)
