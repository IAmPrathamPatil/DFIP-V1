"""P3 ingestion: workbook → source_file → batch → stg_source_row."""

from dfip_core.ingest.headers import HeaderContractError, expected_source_headers
from dfip_core.ingest.pipeline import (
    DEFAULT_CLIENT_ID,
    IngestResult,
    bind_versions_for_day,
    discover_source_workbooks,
    ingest_workbook,
)
from dfip_core.ingest.reader import inspect_workbook, sha256_file
from dfip_core.ingest.store import InMemoryIngestStore

__all__ = [
    "DEFAULT_CLIENT_ID",
    "HeaderContractError",
    "IngestResult",
    "InMemoryIngestStore",
    "bind_versions_for_day",
    "discover_source_workbooks",
    "expected_source_headers",
    "ingest_workbook",
    "inspect_workbook",
    "sha256_file",
]
