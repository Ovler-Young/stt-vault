"""Assign completed transcription text to diarization turns."""

import re

from stt_vault.core.models.records import SpeakerSegment, TranscriptSegment


def attribute_speakers(
    transcripts: list[TranscriptSegment], turns: list[SpeakerSegment]
) -> list[TranscriptSegment]:
    attributed: list[TranscriptSegment] = []
    for transcript in transcripts:
        overlaps = sorted(
            (
                {
                    "start": max(turn["start"], transcript["start"]),
                    "end": min(turn["end"], transcript["end"]),
                    "speaker": turn["speaker"],
                }
                for turn in turns
                if turn["end"] > transcript["start"] and turn["start"] < transcript["end"]
            ),
            key=lambda turn: (turn["start"], turn["end"], turn["speaker"]),
        )
        if not overlaps or not transcript["text"]:
            attributed.append(transcript)
            continue
        # Text-only providers: distribute whole words (or Chinese characters)
        # over speech duration, keeping every character of the provider response.
        tokens = re.findall(r"\S+\s*|^\s+", transcript["text"])
        if len(tokens) == 1 and re.search(r"[\u3400-\u9fff]", transcript["text"]):
            tokens = list(transcript["text"])
        duration = sum(turn["end"] - turn["start"] for turn in overlaps)
        cursor = 0
        elapsed = 0.0
        for index, turn in enumerate(overlaps):
            elapsed += turn["end"] - turn["start"]
            boundary = (
                len(tokens)
                if index == len(overlaps) - 1
                else round(len(tokens) * elapsed / duration)
            )
            text = "".join(tokens[cursor:boundary])
            cursor = boundary
            if text:
                attributed.append(
                    {
                        **transcript,
                        "start": turn["start"],
                        "end": turn["end"],
                        "chunk_start": turn["start"],
                        "chunk_end": turn["end"],
                        "speaker": turn["speaker"],
                        "text": text,
                    }
                )
    return attributed
