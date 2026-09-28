"""The zero-install `mark-cli.py` launcher delegates to the real CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mark import __version__

LAUNCHER = Path(__file__).resolve().parent.parent / "mark-cli.py"


def test_launcher_reports_version() -> None:
    proc = subprocess.run(
        [sys.executable, str(LAUNCHER), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert __version__ in proc.stdout
