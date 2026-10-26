"""Fixtures de test: omisión automática de tests de integración sin BD real."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _skip_db_tests_without_database(request: pytest.FixtureRequest) -> None:
    if "db" in request.node.keywords and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL no definida; se omite el test de integración con BD")
