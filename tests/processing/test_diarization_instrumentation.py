from stt_vault.processing.diarization.instrumentation import _rss_value_to_mb


def test_linux_rss_values_use_kib_units_above_one_gibibyte() -> None:
    assert _rss_value_to_mb(2 * 1024 * 1024, "linux") == 2048.0


def test_macos_rss_values_use_byte_units() -> None:
    assert _rss_value_to_mb(2 * 1024 * 1024, "darwin") == 2.0
