"""DFIP database package.

P1: schema catalog, migration paths, and SQL inspection helpers.
V2 Phase 1: optional PostgreSQL adapters used when DATABASE_URL is set.
"""

from dfip_db.catalog import BUSINESS_KEY, QA_KPI_NAMES, SOURCE_COLUMNS
from dfip_db.paths import migration_files, repo_root

__all__ = [
    "BUSINESS_KEY",
    "QA_KPI_NAMES",
    "SOURCE_COLUMNS",
    "migration_files",
    "repo_root",
]
__version__ = "0.1.0"
