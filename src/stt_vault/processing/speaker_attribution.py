"""Assign completed transcription text to diarization turns."""

import re
from dataclasses import replace

from stt_vault.core.models.records import SpeakerSegment, TranscriptSegment


def attribute_speakers(
    transcripts: list[TranscriptSegment], turns: list[SpeakerSegment]
) -> list[TranscriptSegment]:
    attributed: list[TranscriptSegment] = []
    for transcript in transcripts:
        overlaps = sorted(
            (
                SpeakerSegment(
                    max(turn.start, transcript.start),
                    min(turn.end, transcript.end),
                    turn.speaker,
                )
                for turn in turns
                if turn.end > transcript.start and turn.start < transcript.end
            ),
            key=lambda turn: (turn.start, turn.end, turn.speaker),
        )
        if not overlaps or not transcript.text:
            attributed.append(transcript)
            continue
        timed = _attribute_timed_text(transcript, overlaps)
        if timed is not None:
            attributed.extend(timed)
            continue
        # Text-only providers: distribute whole words (or Chinese characters)
        # over speech duration, keeping every character of the provider response.
        tokens = re.findall(r"\S+\s*|^\s+", transcript.text)
        if len(tokens) == 1 and re.search(r"[\u3400-\u9fff]", transcript.text):
            tokens = list(transcript.text)
        duration = sum(turn.end - turn.start for turn in overlaps)
        cursor = 0
        elapsed = 0.0
        for index, turn in enumerate(overlaps):
            elapsed += turn.end - turn.start
            boundary = (
                len(tokens)
                if index == len(overlaps) - 1
                else round(len(tokens) * elapsed / duration)
            )
            text = "".join(tokens[cursor:boundary])
            cursor = boundary
            if text:
                attributed.append(
                    replace(
                        transcript,
                        start=turn.start,
                        end=turn.end,
                        chunk_start=turn.start,
                        chunk_end=turn.end,
                        speaker=turn.speaker,
                        text=text,
                        timed_units=(),
                    )
                )
    return attributed


def _attribute_timed_text(
    transcript: TranscriptSegment, turns: list[SpeakerSegment]
) -> list[TranscriptSegment] | None:
    if not transcript.timed_units:
        return None
    offsets: list[int] = []
    cursor = 0
    for unit in transcript.timed_units:
        offset = transcript.text.find(unit.text, cursor)
        if offset < 0:
            return None
        offsets.append(offset)
        cursor = offset + len(unit.text)
    offsets[0] = 0
    result: list[TranscriptSegment] = []
    for index, unit in enumerate(transcript.timed_units):
        start, end = unit.start_ms / 1000, unit.end_ms / 1000
        turn = max(
            turns,
            key=lambda turn: (
                (turn.start < start <= turn.end)
                if start == end
                else (turn.start <= start and end <= turn.end),
                max(0.0, min(end, turn.end) - max(start, turn.start)),
                -abs((start + end) / 2 - (turn.start + turn.end) / 2),
            ),
        )
        text = transcript.text[
            offsets[index] : offsets[index + 1] if index + 1 < len(offsets) else None
        ]
        if result and result[-1].speaker == turn.speaker:
            previous = result[-1]
            result[-1] = replace(
                previous,
                end=max(previous.end, end),
                chunk_end=max(previous.end, end),
                text=previous.text + text,
                timed_units=(*previous.timed_units, unit),
            )
        else:
            result.append(
                replace(
                    transcript,
                    start=start,
                    end=end,
                    chunk_start=start,
                    chunk_end=end,
                    speaker=turn.speaker,
                    text=text,
                    timed_units=(unit,),
                )
            )
    return result
