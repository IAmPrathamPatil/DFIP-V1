"""Paths for DFIP SQL migrations."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "supabase").is_dir():
            return parent
    raise FileNotFoundError("DFIP repository root not found from dfip_db package")


def migrations_dir() -> Path:
    return repo_root() / "supabase" / "migrations"


def migration_files() -> list[Path]:
    files = sorted(migrations_dir().glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"No SQL migrations in {migrations_dir()}")
    return files


def v1_migration_files() -> list[Path]:
    """Locked P0–P10 SQL files. Phase 1 appends later timestamps."""
    stamps = {f"2026082300000{index}" for index in range(1, 10)}
    stamps.add("20260823000010")
    return [path for path in migration_files() if path.name[:14] in stamps]


def _concat(files: list[Path]) -> str:
    parts = []
    for path in files:
        parts.append(f"-- file: {path.name}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def read_migrations() -> str:
    return _concat(migration_files())


def read_v1_migrations() -> str:
    return _concat(v1_migration_files())
