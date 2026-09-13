"""Private original-source object storage.

Stores uploaded XLSX bytes outside PostgreSQL. Keys are deterministic:
``{client_id}/{source_file_id}/{sha256}.xlsx``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from dfip_config.settings import Settings

from dfip_api.errors import AuthorizationError, PersistenceUnavailableError

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
URI_SCHEME = "dfip-source"
DEFAULT_BUCKET = "dfip-source-files"


def source_object_key(client_id: str, source_file_id: str, sha256: str) -> str:
    _assert_object_ids(client_id, source_file_id, sha256)
    return f"{client_id}/{source_file_id}/{sha256}.xlsx"


def source_object_uri(bucket: str, key: str) -> str:
    name = (bucket or DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
    if "/" in name or "\\" in name or ".." in name:
        raise PersistenceUnavailableError("Source file could not be archived.")
    return f"{URI_SCHEME}://{name}/{key}"


def _assert_object_ids(client_id: str, source_file_id: str, sha256: str) -> None:
    if not _UUID.match(client_id) or not _UUID.match(source_file_id) or not _SHA256.match(sha256):
        raise PersistenceUnavailableError("Source file could not be archived.")


class SourceObjectStore(Protocol):
    def put(
        self,
        *,
        client_id: str,
        source_file_id: str,
        sha256: str,
        payload: bytes,
    ) -> str: ...

    def exists(self, *, client_id: str, source_file_id: str, sha256: str) -> bool: ...

    def get(self, *, client_id: str, source_file_id: str, sha256: str) -> bytes: ...

    def object_count(self) -> int: ...

    def delete(self, *, client_id: str, source_file_id: str, sha256: str) -> None: ...


class InMemorySourceObjectStore:
    """Process-local store for tests and empty DFIP_STORAGE_ENDPOINT."""

    def __init__(self, bucket: str = DEFAULT_BUCKET) -> None:
        self.bucket = (bucket or DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
        self._objects: dict[str, bytes] = {}

    def put(
        self,
        *,
        client_id: str,
        source_file_id: str,
        sha256: str,
        payload: bytes,
    ) -> str:
        key = source_object_key(client_id, source_file_id, sha256)
        if key not in self._objects:
            self._objects[key] = payload
        return source_object_uri(self.bucket, key)

    def exists(self, *, client_id: str, source_file_id: str, sha256: str) -> bool:
        key = source_object_key(client_id, source_file_id, sha256)
        return key in self._objects

    def get(self, *, client_id: str, source_file_id: str, sha256: str) -> bytes:
        key = source_object_key(client_id, source_file_id, sha256)
        stored = self._objects.get(key)
        if stored is None:
            raise AuthorizationError("Not authorized to access this resource.")
        return stored

    def object_count(self) -> int:
        return len(self._objects)

    def delete(self, *, client_id: str, source_file_id: str, sha256: str) -> None:
        self._objects.pop(source_object_key(client_id, source_file_id, sha256), None)

    def purge_client(self, client_id: str) -> None:
        prefix = f"{client_id}/"
        self._objects = {
            key: payload for key, payload in self._objects.items() if not key.startswith(prefix)
        }


class FilesystemSourceObjectStore:
    """Private directory layout matching the object key. Not a public HTTP root."""

    def __init__(self, root: Path, bucket: str = DEFAULT_BUCKET) -> None:
        self.root = Path(root).resolve()
        self.bucket = (bucket or DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, client_id: str, source_file_id: str, sha256: str) -> Path:
        key = source_object_key(client_id, source_file_id, sha256)
        path = (self.root / self.bucket / Path(key)).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise PersistenceUnavailableError("Source file could not be archived.") from exc
        return path

    def put(
        self,
        *,
        client_id: str,
        source_file_id: str,
        sha256: str,
        payload: bytes,
    ) -> str:
        path = self._path(client_id, source_file_id, sha256)
        if path.is_file():
            return source_object_uri(
                self.bucket, source_object_key(client_id, source_file_id, sha256)
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)
        return source_object_uri(self.bucket, source_object_key(client_id, source_file_id, sha256))

    def exists(self, *, client_id: str, source_file_id: str, sha256: str) -> bool:
        return self._path(client_id, source_file_id, sha256).is_file()

    def get(self, *, client_id: str, source_file_id: str, sha256: str) -> bytes:
        path = self._path(client_id, source_file_id, sha256)
        if not path.is_file():
            raise AuthorizationError("Not authorized to access this resource.")
        return path.read_bytes()

    def object_count(self) -> int:
        bucket_root = self.root / self.bucket
        if not bucket_root.is_dir():
            return 0
        return sum(1 for item in bucket_root.rglob("*.xlsx") if item.is_file())

    def delete(self, *, client_id: str, source_file_id: str, sha256: str) -> None:
        path = self._path(client_id, source_file_id, sha256)
        if path.is_file():
            path.unlink()

    def purge_client(self, client_id: str) -> None:
        from dfip_api.purge import remove_tenant_archive, tenant_archive_dir

        folder = tenant_archive_dir(self.root, client_id, self.bucket)
        remove_tenant_archive(folder)


def build_source_object_store(settings: Settings) -> SourceObjectStore:
    """Empty endpoint → in-memory. Local path → private filesystem tree.

    HTTP(S) endpoints are not implemented (no public bucket, no extra SDK).
    """
    bucket = settings.dfip_storage_bucket.strip() or DEFAULT_BUCKET
    endpoint = settings.dfip_storage_endpoint.strip()
    if not endpoint:
        return InMemorySourceObjectStore(bucket)
    lowered = endpoint.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        raise PersistenceUnavailableError(
            "Remote object-storage endpoints are not configured. "
            "Set DFIP_STORAGE_ENDPOINT to a private local directory."
        )
    path = Path(endpoint)
    if lowered.startswith("file:"):
        path = Path(endpoint.removeprefix("file://").removeprefix("file:"))
    return FilesystemSourceObjectStore(path, bucket)
