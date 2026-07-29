import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

from stt_vault.core.models.api import DiarizationResult
from stt_vault.processing.diarization import DiarizerManager, _create_senko_diarizer
from stt_vault.processing.diarization.contracts import ProviderDiarizationPayload


def test_diarization_model_rejects_malformed_provider_data() -> None:
    with pytest.raises(ValidationError):
        DiarizationResult.model_validate(
            {
                "raw_segments": [{"start": "bad", "end": 1.0, "speaker": "SPEAKER_00"}],
                "merged_segments": [],
                "speaker_centroids": {},
                "timing_stats": {},
            }
        )


def test_diarizer_manager_rejects_malformed_provider_result() -> None:
    class MalformedProvider:
        def diarize(self, _wav_path: str, *, generate_colors: bool) -> ProviderDiarizationPayload:
            assert generate_colors
            return {
                "raw_segments": [{"start": "bad", "end": 1.0, "speaker": "SPEAKER_00"}],
                "merged_segments": [],
                "speaker_centroids": {},
                "timing_stats": {},
            }

    manager = DiarizerManager(device="cpu", idle_timeout_seconds=1)
    manager._diarizer = MalformedProvider()

    with pytest.raises(ValidationError):
        manager.diarize("audio.wav")


def test_diarizer_manager_rejects_non_array_provider_centroid() -> None:
    class MalformedProvider:
        def diarize(self, _wav_path: str, *, generate_colors: bool) -> ProviderDiarizationPayload:
            assert generate_colors
            return {
                "raw_segments": [],
                "merged_segments": [],
                "speaker_centroids": {"SPEAKER_00": [0.1, 0.2]},
                "timing_stats": {},
            }

    manager = DiarizerManager(device="cpu", idle_timeout_seconds=1)
    manager._diarizer = MalformedProvider()

    with pytest.raises(ValueError, match="invalid speaker centroid"):
        manager.diarize("audio.wav")


def test_diarizer_manager_uses_injected_factory() -> None:
    calls: list[str] = []

    class EmptyProvider:
        def diarize(
            self, _wav_path: str, *, generate_colors: bool
        ) -> ProviderDiarizationPayload | None:
            assert generate_colors
            return None

    provider = EmptyProvider()
    manager = DiarizerManager(
        device="cpu",
        idle_timeout_seconds=1,
        diarizer_factory=lambda device: calls.append(device) or provider,
    )

    assert manager.diarize("audio.wav") is None
    assert calls == ["cpu"]
    assert manager._diarizer is provider


@pytest.mark.parametrize(
    ("device", "cuda_available", "expected_calls"),
    [
        ("cpu", False, ["disable_nnpack:False", "construct:cpu"]),
        ("auto", False, ["cuda_available:False", "disable_nnpack:False", "construct:auto"]),
        ("auto", True, ["cuda_available:True", "construct:auto"]),
        ("cuda", True, ["construct:cuda"]),
        ("mps", True, ["construct:mps"]),
    ],
)
def test_senko_factory_disables_nnpack_before_cpu_model_construction(
    monkeypatch: pytest.MonkeyPatch,
    device: str,
    cuda_available: bool,
    expected_calls: list[str],
) -> None:
    calls: list[str] = []

    class FakeDiarizer:
        def __init__(self, *, device: str, warmup: bool, quiet: bool) -> None:
            assert warmup
            assert quiet
            calls.append(f"construct:{device}")

    def is_available() -> bool:
        raise AssertionError("NNPACK availability checks emit the warning being suppressed")

    torch = ModuleType("torch")
    torch.backends = SimpleNamespace(
        nnpack=SimpleNamespace(
            is_available=is_available,
            set_flags=lambda enabled: calls.append(f"disable_nnpack:{enabled}"),
        )
    )
    torch.cuda = SimpleNamespace(
        is_available=lambda: calls.append(f"cuda_available:{cuda_available}") or cuda_available
    )
    senko = ModuleType("senko")
    senko.Diarizer = FakeDiarizer
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "senko", senko)
    monkeypatch.setattr(
        "stt_vault.processing.diarization.SenkoDiarizationProvider",
        lambda implementation: implementation,
    )

    _create_senko_diarizer(device)

    assert calls == expected_calls


def test_cpu_nnpack_disable_preserves_native_inference_output_and_suppresses_warning() -> None:
    script = textwrap.dedent(
        """
        import json
        import sys
        from types import ModuleType

        import torch


        def run_model():
            model = torch.nn.Conv1d(1, 1, kernel_size=3, bias=True)
            with torch.no_grad():
                model.weight.copy_(torch.tensor([[[0.25, -0.5, 0.75]]]))
                model.bias.copy_(torch.tensor([0.125]))
            samples = torch.tensor([[[1.0, -2.0, 3.0, -4.0, 5.0]]])
            return model(samples).flatten().tolist()


        if sys.argv[1] == "enabled":
            torch.backends.nnpack.set_flags(True)
            print(json.dumps({"output": run_model()}))
        else:
            disable_calls = []
            set_flags = torch.backends.nnpack.set_flags

            def track_set_flags(enabled):
                disable_calls.append(enabled)
                return set_flags(enabled)

            torch.backends.nnpack.set_flags = track_set_flags

            class Diarizer:
                output = None

                def __init__(self, *, device, warmup, quiet):
                    assert device == "cpu"
                    assert warmup
                    assert quiet
                    type(self).output = run_model()

            senko = ModuleType("senko")
            senko.Diarizer = Diarizer
            sys.modules["senko"] = senko

            import stt_vault.processing.diarization as diarization

            diarization.SenkoDiarizationProvider = lambda implementation: implementation
            diarization._create_senko_diarizer("cpu")
            print(json.dumps({"disable_calls": disable_calls, "output": Diarizer.output}))
        """
    )
    source_root = Path(__file__).parents[2] / "src"
    environment = os.environ | {
        "PYTHONPATH": os.pathsep.join(
            value for value in [str(source_root), os.environ.get("PYTHONPATH", "")] if value
        )
    }

    def run(mode: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-c", script, mode],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result

    enabled = json.loads(run("enabled").stdout)
    disabled = run("disabled")
    disabled_result = json.loads(disabled.stdout)

    assert disabled_result["disable_calls"] == [False]
    assert disabled_result["output"] == pytest.approx(enabled["output"], rel=1e-6, abs=1e-6)
    assert "NNPACK.cpp:56" not in disabled.stderr
