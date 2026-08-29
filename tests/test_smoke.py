"""Smoke tests for the P0 repository foundation.

These tests prove the project initializes, packages import, and settings load
without secrets. They do not exercise Web Engage logic or a live database.
"""

from __future__ import annotations

import dfip_api
import dfip_config
import dfip_core
import dfip_db
import dfip_shared
import dfip_web


def test_packages_import() -> None:
    assert dfip_core.__version__ == "0.4.0"
    assert dfip_db.__version__ == "0.1.0"
    assert dfip_shared.__version__ == "0.0.0"
    assert dfip_config.__version__ == "0.2.0"
    assert dfip_api.__version__ == "0.5.0"
    assert dfip_web.__version__ == "0.6.0"


def test_project_name_in_pyproject() -> None:
    from pathlib import Path

    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "dfip"' in text
    assert "requires-python" in text
