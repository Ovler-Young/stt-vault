import re

_LOCAL_SPEAKER_PATTERN = re.compile(r"SPEAKER_\d+")
_UNUSABLE_SPEAKER_NAMES = {"unknown", "unidentified", "n/a", "none"}


def is_local_speaker_label(value: str) -> bool:
    return bool(_LOCAL_SPEAKER_PATTERN.fullmatch(value))


def is_usable_speaker_name(value: object) -> bool:
    if not isinstance(value, str):
        return False
    name = value.strip()
    return (
        bool(name)
        and len(name) <= 120
        and name.casefold() not in _UNUSABLE_SPEAKER_NAMES
        and not is_local_speaker_label(name)
        and all(character.isprintable() for character in name)
    )
