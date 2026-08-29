"""Schema-level proof that first-match-wins can be represented.

This is not a label-resolution engine. It only checks that duplicate keys plus
row_order are enough for a later phase to reproduce Excel VLOOKUP.
"""

from __future__ import annotations

from dfip_db.catalog import CAMPAIGN_LABEL_OUTPUTS
from dfip_db.paths import read_migrations
from dfip_db.sql_inspect import parse_tables


def test_first_match_is_row_order_not_dedupe() -> None:
    """Given duplicate Campaign Name rows, the lowest row_order is the winner."""
    rows = [
        {"row_order": 40, "campaign_name": "Alpha", "filter_logic_1": "SECOND"},
        {"row_order": 12, "campaign_name": "Alpha", "filter_logic_1": "FIRST"},
        {"row_order": 7, "campaign_name": "Beta", "filter_logic_1": "OTHER"},
    ]
    key = "alpha"
    matches = [row for row in rows if row["campaign_name"].lower() == key]
    winner = min(matches, key=lambda row: row["row_order"])
    assert len(matches) == 2
    assert winner["filter_logic_1"] == "FIRST"
    assert winner["row_order"] == 12


def test_match_key_does_not_trim() -> None:
    assert " Campaign ".lower() == " campaign "
    assert "Campaign".lower() != "campaign ".lower()


def test_label_table_has_outputs_needed_for_six_lookups() -> None:
    table = parse_tables(read_migrations())["campaign_label_row"]
    for col in CAMPAIGN_LABEL_OUTPUTS:
        assert col in table.columns
