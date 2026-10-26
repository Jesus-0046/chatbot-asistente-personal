"""Tests de integración del pipeline Alembic contra una BD real.

Marco `db`: se omite si no existe TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.db


def run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        cwd=os.getcwd(),
        env=env,
    )


def test_alembic_upgrade_head_sin_errores() -> None:
    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    # Si había migraciones pendientes, alembic loguea el avance (a stderr).
    # Sobre una BD ya en head (idempotente) no hay "Running upgrade".
    if "running upgrade" in (result.stdout + result.stderr).lower():
        assert "0015_fase10_web_auth" in (result.stdout + result.stderr)


def test_alembic_current_muestra_cabecera() -> None:
    result = run_alembic("current")
    assert result.returncode == 0, result.stderr
    assert "0015_fase10_web_auth" in result.stdout
