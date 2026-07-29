import os
import subprocess
import sys
from pathlib import Path


def test_module_execution_invokes_application_runner(tmp_path: Path) -> None:
    source_directory = Path(__file__).resolve().parents[3] / "src"
    (tmp_path / "sitecustomize.py").write_text(
        "from stt_vault.core import app\n"
        "\n"
        "def run():\n"
        "    print('application runner invoked')\n"
        "\n"
        "app.run = run\n"
    )
    environment = os.environ | {
        "PYTHONPATH": os.pathsep.join((str(tmp_path), str(source_directory))),
    }

    result = subprocess.run(
        [sys.executable, "-m", "stt_vault.core.app"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "application runner invoked" in result.stdout
