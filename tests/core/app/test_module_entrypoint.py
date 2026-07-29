import os
import subprocess
import sys
from pathlib import Path


def test_module_execution_invokes_application_runner(tmp_path: Path) -> None:
    (tmp_path / "sitecustomize.py").write_text(
        "import uvicorn\n"
        "\n"
        "def run(*args, **kwargs):\n"
        "    print('application runner invoked')\n"
        "\n"
        "uvicorn.run = run\n"
    )
    environment = os.environ | {
        "PYTHONPATH": os.pathsep.join(filter(None, (str(tmp_path), os.environ.get("PYTHONPATH")))),
    }

    result = subprocess.run(
        [sys.executable, "-m", "stt_vault.core.app"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "application runner invoked" in result.stdout
