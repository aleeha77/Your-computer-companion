"""Subprocess smoke test: boots the real app headless and expects exit 0.

Runs ``python -m peeko`` with ``PEEKO_SMOKE_TEST=1`` and the offscreen
Qt platform, so the full startup path (settings, logging, Qt app, avatar
window, auto-quit) is verified exactly as requested in Stage 0.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_smoke_test_via_module(tmp_path):
    env = os.environ.copy()
    env.update(
        {
            "PEEKO_SMOKE_TEST": "1",
            "QT_QPA_PLATFORM": "offscreen",
            "PEEKO_DATA_DIR": str(tmp_path / "data"),
            "PEEKO_CONFIG_DIR": str(tmp_path / "config"),
            "PEEKO_LOG_DIR": str(tmp_path / "logs"),
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "peeko"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"smoke test failed (rc={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # The app should have logged its startup lines.
    assert "Smoke-test mode" in result.stdout or "Smoke-test mode" in result.stderr


def test_console_script_exists():
    """The packaged ``peeko`` console script is declared in pyproject.toml."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[project.scripts]' in pyproject
    assert 'peeko = "peeko.app:main"' in pyproject


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX signal semantics (os.kill SIGINT) differ on Windows",
)
def test_sigint_quits_gracefully(tmp_path):
    """SIGINT must terminate the running app with exit code 0.

    Regression test: a plain ``signal.signal`` handler cannot run while
    ``app.exec()`` blocks in C++, so Peeko uses a wakeup-fd bridge that
    converts SIGINT into a Qt event. This test proves Ctrl+C works.
    """
    import signal
    import time

    env = os.environ.copy()
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "PEEKO_DATA_DIR": str(tmp_path / "data"),
            "PEEKO_CONFIG_DIR": str(tmp_path / "config"),
            "PEEKO_LOG_DIR": str(tmp_path / "logs"),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "peeko"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        time.sleep(3)  # give the app time to boot and enter the event loop
        assert proc.poll() is None, "app exited before SIGINT was sent"
        proc.send_signal(signal.SIGINT)
        out, _ = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:  # pragma: no cover - safety net
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, (
        f"SIGINT did not quit cleanly (rc={proc.returncode})\noutput:\n{out}"
    )
    assert "Termination signal" in out