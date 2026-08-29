"""Filesystem locations for the P6 static SPA (apps/web/static)."""

from __future__ import annotations

from pathlib import Path


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def static_root() -> Path:
    return repository_root() / "apps" / "web" / "static"
