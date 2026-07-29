import pytest

from stt_vault.processing.diarization.instrumentation import (
    _rss_value_to_mb,
    instrument_diarizer,
)


def test_linux_rss_values_use_kib_units_above_one_gibibyte() -> None:
    assert _rss_value_to_mb(2 * 1024 * 1024, "linux") == 2048.0


def test_macos_rss_values_use_byte_units() -> None:
    assert _rss_value_to_mb(2 * 1024 * 1024, "darwin") == 2.0


def test_instrument_diarizer_rejects_provider_that_cannot_store_marker() -> None:
    class ReadOnlyProvider:
        __slots__ = ()

        def diarize(self, _wav_path: str, *, generate_colors: bool) -> None:
            return None

    with pytest.raises(TypeError, match="does not support instrumentation"):
        instrument_diarizer(ReadOnlyProvider(), lambda *_args: None)
