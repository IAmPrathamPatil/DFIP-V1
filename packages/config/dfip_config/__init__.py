"""DFIP configuration package.

P0: environment settings loader.
P2: versioned New Logic snapshots and deterministic lookup resolvers.
"""

from dfip_config.resolve import (
    resolve_campaign_label,
    resolve_filter_logic_1_group,
    resolve_rate_card_rule,
    resolve_template_status,
    select_version_for_day,
)
from dfip_config.settings import (
    ALLOWED_ENVIRONMENTS,
    BOOTSTRAP_TOKEN_HEADER,
    PRODUCTION_GRADE_ENVIRONMENTS,
    Settings,
    load_settings,
)

__all__ = [
    "ALLOWED_ENVIRONMENTS",
    "BOOTSTRAP_TOKEN_HEADER",
    "PRODUCTION_GRADE_ENVIRONMENTS",
    "Settings",
    "load_settings",
    "resolve_campaign_label",
    "resolve_filter_logic_1_group",
    "resolve_rate_card_rule",
    "resolve_template_status",
    "select_version_for_day",
]
__version__ = "0.2.0"
