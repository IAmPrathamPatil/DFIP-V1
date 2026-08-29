"""DFIP core package.

P3: ingestion lives in `dfip_core.ingest`.
P4: transformation / reconciliation lives in `dfip_core.transform`.
P5: the HTTP API lives in `dfip_api`, not in this package.
"""

from dfip_core.ingest import ingest_workbook, inspect_workbook
from dfip_core.transform import (
    FactRecord,
    InMemoryFactStore,
    reconcile_run,
    run_transformation,
    transform_row,
)

__all__ = [
    "FactRecord",
    "InMemoryFactStore",
    "ingest_workbook",
    "inspect_workbook",
    "reconcile_run",
    "run_transformation",
    "transform_row",
]
__version__ = "0.4.0"
