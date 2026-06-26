from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from openai import OpenAI

from .media import extract_audio_chunk


def build_chunks(
    segments: list[dict[str, Any]],
    *,
    max_seconds: float,
    overlap_seconds: float,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + max_seconds)
            chunks.append(
                {
                    "start": max(0.0, cursor - overlap_seconds),
                    "end": chunk_end,
                    "speaker": segment["speaker"],
                    "source_start": start,
                    "source_end": end,
                }
            )
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
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.prompt = prompt
        self.concurrency = max(1, concurrency)

    def transcribe_chunks(
        self,
        wav_path: Path,
        chunks: list[dict[str, Any]],
        tmp_dir: Path,
    ) -> list[dict[str, Any]]:
        if not chunks:
            return []

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = [
                executor.submit(self._transcribe_one, wav_path, chunk, tmp_dir, index)
                for index, chunk in enumerate(chunks)
            ]
            results = [future.result() for future in as_completed(futures)]

        return sorted(results, key=lambda item: (item["start"], item["end"]))

    def _transcribe_one(
        self,
        wav_path: Path,
        chunk: dict[str, Any],
        tmp_dir: Path,
        index: int,
    ) -> dict[str, Any]:
        chunk_path = tmp_dir / f"chunk-{index:06d}.wav"
        extract_audio_chunk(wav_path, chunk_path, float(chunk["start"]), float(chunk["end"]))

        kwargs: dict[str, Any] = {"model": self.model}
        if self.prompt:
            kwargs["prompt"] = self.prompt

        with chunk_path.open("rb") as audio_file:
            response = self.client.audio.transcriptions.create(file=audio_file, **kwargs)

        text = getattr(response, "text", None)
        if text is None and isinstance(response, dict):
            text = response.get("text")

        return {
            "start": chunk["source_start"],
            "end": chunk["source_end"],
            "chunk_start": chunk["start"],
            "chunk_end": chunk["end"],
            "speaker": chunk["speaker"],
            "text": (text or "").strip(),
        }

