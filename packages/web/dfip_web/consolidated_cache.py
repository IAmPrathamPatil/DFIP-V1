"""Reusable on-disk cache for generated consolidated company workbooks.

The consolidated workbook is the one download that is expensive per request:
it materializes the company's whole cumulative published history (routinely
far larger than a single publication snapshot) and then renders the full
nine-sheet workbook from it. Generating one per click repeats all of that work
even though the input rarely changes between clicks.

This module stores the rendered artifact keyed by company plus a cheap
fingerprint of the eligible published history, so an unchanged company is
served straight from disk and only regenerated when its history actually
moves.

Design constraints:

* **No new business logic.** The key is derived from the same client-scoped
  published history the download already uses. Nothing here decides what is
  eligible for publication; that stays in the store and the service.
* **Fail open.** Any cache problem (unwritable directory, truncated file,
  unreadable entry) degrades to a normal render rather than an error. A cache
  must never turn a working download into a failing one.
* **Bounded.** One artifact per company. Writing a new fingerprint replaces
  the previous file for that company, so the cache cannot grow without limit.
* **Private.** The file is written with owner-only permissions because the
  workbook contains one company's published rows. This is not access control:
  authorization is still enforced by the endpoint before any cache lookup.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

CONSOLIDATED_CACHE_DIRNAME = "dfip-consolidated-cache"


@dataclass(frozen=True)
class CachedWorkbook:
    """A previously generated workbook for one company and history fingerprint."""

    body: bytes
    filename: str


def cache_root(base_dir: str | os.PathLike[str] | None = None) -> Path:
    """Return the cache directory, creating it when possible.

    ``base_dir`` is an explicit operator override. Otherwise the cache lives
    under the system temp directory, alongside the other per-request scratch
    space DFIP already uses.
    """
    root = Path(base_dir) if base_dir else Path(tempfile.gettempdir())
    root = root / CONSOLIDATED_CACHE_DIRNAME
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return root
    return root


def _safe_token(value: str) -> str:
    keep = [ch if (ch.isalnum() or ch in "-_") else "_" for ch in value]
    return "".join(keep).strip("_") or "company"


def artifact_key(client_id: str, fingerprint: Sequence[str]) -> str:
    """Stable filename for one company at one history fingerprint.

    Every fingerprint component is sanitized and length-bounded, so the key is
    deterministic per company and cannot collide between distinct histories.
    """
    parts = [_safe_token(str(component)) or "0" for component in fingerprint]
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{digest}.xlsx"


def _company_dir(root: Path, client_id: str) -> Path:
    return root / _safe_token(client_id)


def load(root: Path, client_id: str, fingerprint: Sequence[str]) -> bytes | None:
    """Return the cached workbook bytes, or ``None`` on any miss or problem.

    Only a non-empty, structurally valid ZIP counts as a hit. Validating the
    container (not every entry) is cheap: it reads the central directory, so a
    half-written or truncated file is rejected rather than handed to a client.
    """
    try:
        path = _company_dir(root, client_id) / artifact_key(client_id, fingerprint)
        if not path.is_file():
            return None
        if not zipfile.is_zipfile(path):
            return None
        body = path.read_bytes()
    except OSError:
        return None
    if not body or not body.startswith(b"PK"):
        return None
    return body


def store(root: Path, client_id: str, fingerprint: Sequence[str], body: bytes) -> None:
    """Persist a generated workbook and drop the company's previous artifact.

    Writes to a temporary file first and renames it into place so a concurrent
    reader never observes a half-written workbook. Best effort throughout: a
    failure here leaves the download working, just uncached.
    """
    directory = _company_dir(root, client_id)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / artifact_key(client_id, fingerprint)
        handle, temp_name = tempfile.mkstemp(dir=directory, suffix=".part")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(body)
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        for stale in directory.glob("*.xlsx"):
            if stale.name != target.name:
                try:
                    stale.unlink()
                except OSError:
                    pass
    except OSError:
        return


def purge_company(root: Path, client_id: str) -> None:
    """Remove every cached artifact for one company."""
    try:
        directory = _company_dir(root, client_id)
        for stale in directory.glob("*.xlsx"):
            try:
                stale.unlink()
            except OSError:
                pass
        directory.rmdir()
    except OSError:
        return
