"""P4 transformation / reconciliation engine."""

from dfip_core.transform.cost import CostResolution, calculate_total_cost
from dfip_core.transform.engine import (
    ENGINE_VERSION,
    FACT_PERSIST_CHUNK_SIZE,
    ConfigBinder,
    RowOutcome,
    RowRejection,
    TransformResult,
    run_transformation,
    transform_row,
    transform_rows,
)
from dfip_core.transform.extract import RowValidationError, SourceFields, extract_source_fields
from dfip_core.transform.fact import FactKey, FactRecord
from dfip_core.transform.labels import ConfigBundle, ConfigurationBindingError, bind_configuration
from dfip_core.transform.reconcile import (
    ReconciliationReport,
    excel_blank_equivalent,
    reconcile_fact,
    reconcile_run,
)
from dfip_core.transform.store import InMemoryFactStore, SupersededFact

__all__ = [
    "ENGINE_VERSION",
    "FACT_PERSIST_CHUNK_SIZE",
    "ConfigBinder",
    "ConfigBundle",
    "ConfigurationBindingError",
    "CostResolution",
    "FactKey",
    "FactRecord",
    "InMemoryFactStore",
    "ReconciliationReport",
    "RowOutcome",
    "RowRejection",
    "RowValidationError",
    "SourceFields",
    "SupersededFact",
    "TransformResult",
    "bind_configuration",
    "calculate_total_cost",
    "excel_blank_equivalent",
    "extract_source_fields",
    "reconcile_fact",
    "reconcile_run",
    "run_transformation",
    "transform_row",
    "transform_rows",
]
