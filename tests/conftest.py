"""Shared fixtures for the optional desktop-pet tests.

The core builder tests do not need Qt.  Qt is imported lazily so a user who
installed only the base dependencies can still run the builder test-suite.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONUTF8", "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def assets_dir(project_root: Path) -> Path:
    return project_root / "examples" / "demo_character"


@pytest.fixture(scope="session")
def qapp():
    qt = pytest.importorskip("PySide6.QtWidgets")
    app = qt.QApplication.instance()
    if app is None:
        app = qt.QApplication([])
    yield app
