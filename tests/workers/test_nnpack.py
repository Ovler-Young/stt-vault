import sys
from types import ModuleType, SimpleNamespace

import pytest

from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1
from stt_vault.processing.diarization import _create_senko_diarizer

EMBEDDING_SPACE = EmbeddingSpaceV1(
    space_id="test-space",
    model_id="test-model",
    revision="r1",
    sha256="a" * 64,
    dimension=192,
    metric="cosine",
)


def test_cpu_factory_suppresses_fake_nnpack_warning_before_construction(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    nnpack_enabled = True

    class FakeDiarizer:
        def __init__(self, *, device: str, warmup: bool, quiet: bool) -> None:
            assert device == "cpu"
            assert warmup
            assert quiet
            calls.append("construct")
            if nnpack_enabled:
                print("Could not initialize NNPACK", file=sys.stderr)

    def set_flags(enabled: bool) -> None:
        nonlocal nnpack_enabled
        calls.append(f"set_flags:{enabled}")
        nnpack_enabled = enabled

    def is_available() -> bool:
        raise AssertionError("NNPACK availability checks emit the warning being suppressed")

    torch = ModuleType("torch")
    torch.backends = SimpleNamespace(
        nnpack=SimpleNamespace(is_available=is_available, set_flags=set_flags)
    )
    senko = ModuleType("senko")
    senko.Diarizer = FakeDiarizer
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "senko", senko)
    FakeDiarizer(device="cpu", warmup=True, quiet=True)

    assert "Could not initialize NNPACK" in capsys.readouterr().err
    calls.clear()
    _create_senko_diarizer("cpu", EMBEDDING_SPACE)

    assert calls == ["set_flags:False", "construct"]
    assert "Could not initialize NNPACK" not in capsys.readouterr().err
