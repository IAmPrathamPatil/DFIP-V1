"""P4 transformation / reconciliation contract tests.

Every rule asserted here is recovered from M1.4 and from the Excel formulas in
the supplied production workbooks:

    A = VLOOKUP([Campaign Name], 'New Logic'!A:M, 7,  0)   Filter Logic 1
    B = VLOOKUP([Campaign Name], 'New Logic'!A:M, 8,  0)   Filter Logic 2
    C = IFERROR(VLOOKUP([Template Name (WhatsApp)], 'New Logic'!Q:R, 2, 0), "")
    D = VLOOKUP([Campaign Name], 'New Logic'!A:M, 10, 0)   AMC Status FL3
    E = VLOOKUP([Campaign Name], 'New Logic'!A:M, 11, 0)   AMC Device FL4
    F = VLOOKUP([Campaign Name], 'New Logic'!A:M, 12, 0)   AMC Product FL5
    G = VLOOKUP([Campaign Name], 'New Logic'!A:M, 13, 0)   Manual Or Automated
    H = IF(C="Utility", AI*<utility>, IF(P="SMS", AI*0.15, IF(P="Email", AI*0.01,
        IF(P="RCS", AI*0.25, IF(P="WhatsApp", AI*<whatsapp>, 0)))))
    I = TEXT([Start Date], "HH")
    J = TEXT([Day], "MMM-YY")

Column I of 'New Logic' (`unused_sheet_template_status`) is never consulted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from dfip_config.resolve import resolve_campaign_label
from dfip_config.store import load_campaign_version, load_template_version
from dfip_core.ingest.headers import expected_source_headers
from dfip_core.ingest.store import InMemoryIngestStore, StagedRowRecord
from dfip_core.transform import (
    ConfigBinder,
    FactRecord,
    InMemoryFactStore,
    RowValidationError,
    bind_configuration,
    calculate_total_cost,
    derive,
    excel_blank_equivalent,
    extract_source_fields,
    reconcile_fact,
    reconcile_run,
    run_transformation,
    transform_row,
)
from dfip_core.transform.extract import NATIVE_RATE_HEADERS

CLIENT_ID = "a0000000-0000-4000-8000-000000000001"

# Fixtures taken from the committed P2 snapshots, not invented.
DUPLICATE_CAMPAIGN = "AMC Booking Push Read But Did Not Click"
DUPLICATE_FIRST_ROW_ORDER = 2228
DUPLICATE_LAST_ROW_ORDER = 2753
LEADING_SPACE_CAMPAIGN = " Email Additional: Drop_off 14th Oct"
GROUP7_CAMPAIGN = "TAMC_tplus90_UV_Mid/Prem_Push"
GROUP7_FILTER_LOGIC_1 = "TAMC | D2C AMC|Automated Renewal Campaign"
V2_ONLY_CAMPAIGN = (
    "Has Ended TAMC General Cohort1 T-15 to T+15 "
    "| AMC | TAMC | OW/OC | UV | Prem/Mid | Gold | Event | Uty"
)
# New Logic row whose column I reads 'Utility' while Q:R holds no entry.
COLUMN_I_TRAP_ROW_ORDER = 306
UTILITY_TEMPLATE = "monsoon_wa_new_v2_tamc_tplus250_utility"
MARKETING_TEMPLATE_NAMED_UTILITY = "efl_utility_221124"

AUG_DAY = "2025-08-01"
JUL_DAY = "2025-07-31"


def make_raw(**overrides: Any) -> dict[str, Any]:
    """A full 57-header staging payload with only the given cells populated."""
    raw: dict[str, Any] = dict.fromkeys(expected_source_headers())
    defaults = {
        "Day": AUG_DAY,
        "Campaign ID": "camp-1",
        "Variation ID": "var-1",
        "Campaign Name": GROUP7_CAMPAIGN,
        "Channel": "WhatsApp",
        "Delivered": 100,
    }
    raw.update(defaults)
    unknown = set(overrides) - set(raw)
    assert not unknown, f"test used non-source headers: {sorted(unknown)}"
    raw.update(overrides)
    return raw


def staged(row_number: int = 2, **overrides: Any) -> StagedRowRecord:
    raw = make_raw(**overrides)
    day: date | None
    try:
        day = date.fromisoformat(str(raw["Day"])[:10])
    except (TypeError, ValueError):
        day = None
    return StagedRowRecord(
        id=f"stg-{row_number}",
        batch_id="batch-1",
        source_row_number=row_number,
        raw=raw,
        campaign_id=raw["Campaign ID"],
        variation_id=raw["Variation ID"],
        day=day,
    )


def transform(**overrides: Any) -> FactRecord:
    outcome = transform_row(
        staged(**overrides),
        ConfigBinder(),
        client_id=CLIENT_ID,
        batch_id="batch-1",
        processing_run_id="run-1",
    )
    assert outcome.rejection is None, outcome.rejection
    assert outcome.fact is not None
    return outcome.fact


def reject(**overrides: Any):
    outcome = transform_row(
        staged(**overrides),
        ConfigBinder(),
        client_id=CLIENT_ID,
        batch_id="batch-1",
        processing_run_id="run-1",
    )
    assert outcome.fact is None
    assert outcome.rejection is not None
    return outcome.rejection


# ---------------------------------------------------------------------------
# A. Campaign lookup
# ---------------------------------------------------------------------------


def test_campaign_lookup_applies_all_six_outputs() -> None:
    fact = transform(**{"Campaign Name": GROUP7_CAMPAIGN})
    assert fact.label_match_status == "matched"
    assert fact.filter_logic_1 == GROUP7_FILTER_LOGIC_1
    assert fact.filter_logic_2 == "TAMC_tplus90"
    assert fact.amc_status_filter_logic_3 == "OW/OC"
    assert fact.amc_device_category_filter_logic_4 == "Prem/Mid"
    assert fact.amc_product_cat_filter_logic_5 == "UV"
    assert fact.manual_or_automated == "Automated"


def test_duplicate_campaign_names_resolve_to_lowest_row_order() -> None:
    rows = load_campaign_version("campaign-v2").rows
    duplicates = [row for row in rows if row.get("campaign_name") == DUPLICATE_CAMPAIGN]
    assert len(duplicates) > 1, "fixture must stay a genuine duplicate"

    first = min(duplicates, key=lambda row: row["row_order"])
    last = max(duplicates, key=lambda row: row["row_order"])
    assert first["row_order"] == DUPLICATE_FIRST_ROW_ORDER
    assert last["row_order"] == DUPLICATE_LAST_ROW_ORDER
    assert first["filter_logic_2"] != last["filter_logic_2"], "fixture must disambiguate"

    fact = transform(**{"Campaign Name": DUPLICATE_CAMPAIGN})
    assert fact.filter_logic_2 == first["filter_logic_2"]
    assert fact.filter_logic_2 != last["filter_logic_2"]


def test_leading_space_is_meaningful_and_never_trimmed() -> None:
    spaced = resolve_campaign_label("campaign-v2", LEADING_SPACE_CAMPAIGN)
    bare = resolve_campaign_label("campaign-v2", LEADING_SPACE_CAMPAIGN.lstrip())
    assert spaced.match_status == "matched"
    assert bare.match_status == "matched"
    assert spaced.row_order != bare.row_order, "a trim would collapse these two rows"

    fact = transform(**{"Campaign Name": LEADING_SPACE_CAMPAIGN})
    assert fact.campaign_name == LEADING_SPACE_CAMPAIGN
    assert fact.campaign_name.startswith(" ")


def test_campaign_lookup_is_case_insensitive() -> None:
    upper = transform(**{"Campaign Name": GROUP7_CAMPAIGN.upper()})
    assert upper.label_match_status == "matched"
    assert upper.filter_logic_1 == GROUP7_FILTER_LOGIC_1
    assert upper.campaign_name == GROUP7_CAMPAIGN.upper(), "source text is preserved verbatim"


def test_missing_campaign_yields_null_outputs_not_an_unlabeled_bucket() -> None:
    fact = transform(**{"Campaign Name": "no such campaign exists in New Logic"})
    assert fact.label_match_status == "unmatched"
    assert fact.filter_logic_1 is None
    assert fact.filter_logic_2 is None
    assert fact.amc_status_filter_logic_3 is None
    assert fact.amc_device_category_filter_logic_4 is None
    assert fact.amc_product_cat_filter_logic_5 is None
    assert fact.manual_or_automated is None


def test_blank_campaign_name_is_distinct_from_unmatched() -> None:
    assert transform(**{"Campaign Name": None}).label_match_status == "blank_key"
    assert transform(**{"Campaign Name": ""}).label_match_status == "blank_key"
    assert transform(**{"Campaign Name": "zzz nope"}).label_match_status == "unmatched"


# ---------------------------------------------------------------------------
# B. Template lookup (New Logic Q:R)
# ---------------------------------------------------------------------------


def test_template_status_comes_from_q_r() -> None:
    fact = transform(**{"Template Name (WhatsApp)": UTILITY_TEMPLATE})
    assert fact.template_match_status == "matched"
    assert fact.template_status == "Utility"


def test_column_i_of_new_logic_is_never_used_as_template_status() -> None:
    """New Logic column I says 'Utility' on this row; Q:R says nothing.

    Reading column I would flip both the Template Status and the rate branch,
    so this row proves the engine ignores it.
    """
    row = next(
        row
        for row in load_campaign_version("campaign-v2").rows
        if row["row_order"] == COLUMN_I_TRAP_ROW_ORDER
    )
    assert row["unused_sheet_template_status"] == "Utility"

    fact = transform(
        **{
            "Campaign Name": row["campaign_name"],
            "Template Name (WhatsApp)": None,
            "Channel": "WhatsApp",
            "Delivered": 1000,
        }
    )
    assert fact.label_match_status == "matched"
    assert fact.filter_logic_1 == row["filter_logic_1"]
    assert fact.template_status == "", "column I must not become Template Status"
    assert fact.total_cost == Decimal("1000") * Decimal("0.785000"), "WhatsApp rate, not Utility"
    assert fact.total_cost != Decimal("1000") * Decimal("0.115000")


def test_template_name_containing_utility_still_uses_column_r() -> None:
    row = next(
        row
        for row in load_template_version("template-v4").rows
        if row["template_name"] == MARKETING_TEMPLATE_NAMED_UTILITY
    )
    assert row["template_status"] == "Marketing"

    fact = transform(
        **{"Template Name (WhatsApp)": MARKETING_TEMPLATE_NAMED_UTILITY, "Channel": "WhatsApp"}
    )
    assert fact.template_status == "Marketing"
    # Rated as WhatsApp, not Utility, despite the template name.
    assert fact.total_cost == Decimal("100") * Decimal("0.785000")


def test_missing_template_returns_blank_string_not_null() -> None:
    missing = transform(**{"Template Name (WhatsApp)": "not-a-template"})
    assert missing.template_status == ""
    assert missing.template_match_status == "unmatched"

    blank = transform(**{"Template Name (WhatsApp)": None})
    assert blank.template_status == ""
    assert blank.template_match_status == "blank_key"


def test_template_miss_and_campaign_miss_stay_distinguishable() -> None:
    fact = transform(**{"Campaign Name": "zzz nope", "Template Name (WhatsApp)": "zzz also nope"})
    assert fact.label_match_status == "unmatched"
    assert fact.filter_logic_1 is None
    assert fact.template_match_status == "unmatched"
    assert fact.template_status == ""


# ---------------------------------------------------------------------------
# C. Filter Logic 1_2
# ---------------------------------------------------------------------------


def test_filter_logic_1_group_uses_exact_membership() -> None:
    fact = transform()
    assert fact.filter_logic_1 == GROUP7_FILTER_LOGIC_1
    assert fact.filter_logic_1_group == "Group7"


def test_unknown_filter_logic_1_passes_through_unchanged() -> None:
    binder = ConfigBinder()
    bundle = binder.for_day(date(2025, 8, 1))
    assert bundle.filter_logic_1_group("Not A Known Group") == "Not A Known Group"
    assert bundle.filter_logic_1_group(" leading space kept ") == " leading space kept "


def test_blank_blank_group_membership_is_preserved() -> None:
    bundle = ConfigBinder().for_day(date(2025, 8, 1))
    assert bundle.filter_logic_1_group(None) is None

    fact = transform(**{"Campaign Name": "zzz nope"})
    assert fact.filter_logic_1 is None
    assert fact.filter_logic_1_group is None


# ---------------------------------------------------------------------------
# D. Rate-card resolution
# ---------------------------------------------------------------------------


def test_utility_template_status_outranks_channel() -> None:
    fact = transform(
        **{"Template Name (WhatsApp)": UTILITY_TEMPLATE, "Channel": "WhatsApp", "Delivered": 1000}
    )
    assert fact.template_status == "Utility"
    assert fact.total_cost == Decimal("1000") * Decimal("0.115000")
    assert fact.total_cost != Decimal("1000") * Decimal("0.785000")


@pytest.mark.parametrize(
    ("channel", "rate"),
    [("SMS", "0.15"), ("Email", "0.01"), ("RCS", "0.25"), ("WhatsApp", "0.785")],
)
def test_channel_rate_fallback(channel: str, rate: str) -> None:
    fact = transform(**{"Channel": channel, "Delivered": 400})
    assert fact.total_cost == (Decimal("400") * Decimal(rate)).quantize(Decimal("0.0001"))
    assert fact.rate_card_rule_id is not None


def test_unmatched_channel_costs_zero_and_records_no_rule() -> None:
    fact = transform(**{"Channel": "Push", "Delivered": 5000})
    assert fact.template_status == ""
    assert fact.total_cost == Decimal("0.0000")
    assert fact.rate_card_rule_id is None


def test_null_channel_costs_zero() -> None:
    fact = transform(**{"Channel": None, "Delivered": 5000})
    assert fact.total_cost == Decimal("0.0000")
    assert fact.rate_card_rule_id is None


@pytest.mark.parametrize(
    ("day", "rate_version", "whatsapp_rate", "utility_rate"),
    [
        (JUL_DAY, "rate-v1", "0.83", "0.12"),
        (AUG_DAY, "rate-v2", "0.785", "0.115"),
    ],
)
def test_rate_version_boundary_2025_08_01(
    day: str, rate_version: str, whatsapp_rate: str, utility_rate: str
) -> None:
    bundle = bind_configuration(date.fromisoformat(day))
    assert bundle.rate_card_version_label == rate_version

    whatsapp = calculate_total_cost(rate_version, "", "WhatsApp", 200)
    assert whatsapp.rate == Decimal(whatsapp_rate).quantize(Decimal("0.000001"))
    assert whatsapp.total_cost == (Decimal("200") * Decimal(whatsapp_rate)).quantize(
        Decimal("0.0001")
    )

    utility = calculate_total_cost(rate_version, "Utility", "WhatsApp", 200)
    assert utility.total_cost == (Decimal("200") * Decimal(utility_rate)).quantize(
        Decimal("0.0001")
    )


def test_configuration_boundary_selects_campaign_and_template_versions() -> None:
    july = bind_configuration(date(2025, 7, 31))
    august = bind_configuration(date(2025, 8, 1))
    assert july.campaign_version_label == "campaign-v1"
    assert august.campaign_version_label == "campaign-v2"
    assert july.rate_card_version_label == "rate-v1"
    assert august.rate_card_version_label == "rate-v2"
    assert july.template_version_label != august.template_version_label

    assert july.campaign(V2_ONLY_CAMPAIGN).match_status == "unmatched"
    assert august.campaign(V2_ONLY_CAMPAIGN).match_status == "matched"


# ---------------------------------------------------------------------------
# E. Total Cost
# ---------------------------------------------------------------------------


def test_total_cost_is_delivered_times_rate_not_sent_or_clicks() -> None:
    fact = transform(**{"Channel": "SMS", "Delivered": 1000, "Sent": 9999, "Unique Clicks": 7777})
    assert fact.total_cost == Decimal("150.0000")
    assert fact.total_cost != Decimal("9999") * Decimal("0.15")
    assert fact.total_cost != Decimal("7777") * Decimal("0.15")


def test_native_web_engage_rate_columns_are_not_the_dfip_rate() -> None:
    poisoned = {header: 999.0 for header in NATIVE_RATE_HEADERS}
    fact = transform(**{"Channel": "SMS", "Delivered": 1000, **poisoned})
    assert fact.total_cost == Decimal("150.0000")

    baseline = transform(**{"Channel": "SMS", "Delivered": 1000})
    assert fact.total_cost == baseline.total_cost


def test_excel_total_cost_column_is_not_an_input() -> None:
    """A:J are derived columns and are not part of the 57 staged source headers."""
    headers = expected_source_headers()
    for derived in ("Total Cost", "Filter Logic 1", "Filter Logic 2", "Template Status", "HHH"):
        assert derived not in headers


def test_null_delivered_costs_zero_but_delivered_stays_null() -> None:
    fact = transform(**{"Channel": "SMS", "Delivered": None})
    assert fact.delivered is None
    assert fact.total_cost == Decimal("0.0000")
    assert fact.rate_card_rule_id is not None, "the SMS branch still matched"


def test_zero_delivered_costs_zero() -> None:
    fact = transform(**{"Channel": "SMS", "Delivered": 0})
    assert fact.delivered == 0
    assert fact.total_cost == Decimal("0.0000")


@pytest.mark.parametrize("value", ["abc", "1,000", " 12", "12 ", "1.5", True])
def test_malformed_delivered_is_rejected_not_coerced(value: Any) -> None:
    rejection = reject(**{"Delivered": value})
    assert rejection.reason_code == "INVALID_NUMERIC"


def test_numeric_strings_and_whole_floats_are_accepted() -> None:
    assert transform(**{"Delivered": "250"}).delivered == 250
    assert transform(**{"Delivered": 250.0}).delivered == 250


# ---------------------------------------------------------------------------
# Grain, variation id (OPEN-A6), and derived scalars
# ---------------------------------------------------------------------------


def test_blank_variation_id_uses_the_empty_string_key() -> None:
    for blank in (None, ""):
        fact = transform(**{"Variation ID": blank})
        assert fact.variation_id == blank
        assert fact.variation_id_key == ""
        assert fact.key == (CLIENT_ID, "camp-1", "", date(2025, 8, 1))


def test_non_blank_variation_id_is_its_own_key() -> None:
    fact = transform(**{"Variation ID": "var-9"})
    assert fact.variation_id == "var-9"
    assert fact.variation_id_key == "var-9"


def test_blank_variation_key_cannot_collide_with_a_real_variation() -> None:
    blank = transform(**{"Variation ID": None}).key
    real = transform(**{"Variation ID": "var-9"}).key
    assert blank != real
    assert derive.variation_id_key("0") == "0", "no sentinel literal is invented"


def test_missing_key_components_are_rejected() -> None:
    assert reject(**{"Campaign ID": None}).reason_code == "MISSING_CAMPAIGN_ID"
    assert reject(**{"Campaign ID": ""}).reason_code == "MISSING_CAMPAIGN_ID"
    assert reject(**{"Day": None}).reason_code == "MISSING_DAY"
    assert reject(**{"Day": "not-a-date"}).reason_code == "INVALID_DAY"


def test_month_and_hhh_follow_the_recovered_text_formulas() -> None:
    fact = transform(**{"Day": "2025-08-01", "Start Date": "2025-08-01T19:30:00"})
    assert fact.month_label == "Aug-25"
    assert fact.month_start == date(2025, 8, 1)
    assert fact.hhh == "19"

    assert derive.month_label(date(2025, 4, 30)) == "Apr-25"
    assert derive.month_label(date(2025, 10, 1)) == "Oct-25"


def test_blank_start_date_formats_as_hour_zero() -> None:
    fact = transform(**{"Start Date": None})
    assert fact.start_date is None
    assert fact.hhh == "00"


# ---------------------------------------------------------------------------
# Lineage, idempotency, run lifecycle
# ---------------------------------------------------------------------------


def build_batch(rows: list[StagedRowRecord]) -> tuple[InMemoryIngestStore, str]:
    store = InMemoryIngestStore()
    source_file = store.register_source_file(
        client_id=CLIENT_ID,
        sha256="0" * 64,
        original_filename="synthetic.xlsx",
        byte_size=1,
        source_kind="legacy_workbook",
    )
    batch = store.create_batch(source_file_id=source_file.id, client_id=CLIENT_ID)
    batch.status = "staged"
    for row in rows:
        store.add_staged_row(
            StagedRowRecord(
                id=row.id,
                batch_id=batch.id,
                source_row_number=row.source_row_number,
                raw=row.raw,
                campaign_id=row.campaign_id,
                variation_id=row.variation_id,
                day=row.day,
            )
        )
    store.create_processing_run(
        batch_id=batch.id,
        campaign_label_version_id="a0000000-0000-4000-8000-000000000022",
        template_label_version_id="a0000000-0000-4000-8000-000000000034",
        rate_card_version_id="a0000000-0000-4000-8000-000000000012",
        label_group_version_id="a0000000-0000-4000-8000-000000000041",
        engine_version="0.4.0",
    )
    return store, batch.id


def _transform(store, facts, batch_id, **kwargs):
    run = store.processing_run_for_batch(batch_id)
    assert run is not None
    return run_transformation(store, facts, batch_id, processing_run_id=run.id, **kwargs)


def test_run_binds_configuration_versions_onto_every_fact() -> None:
    store, batch_id = build_batch([staged(2), staged(3, **{"Campaign ID": "camp-2"})])
    facts = InMemoryFactStore()
    result = _transform(store, facts, batch_id)

    assert result.transformed == 2
    assert result.rejected == 0
    assert result.processing_run.status == "succeeded"
    assert result.processing_run.finished_at is not None
    assert store.batches[batch_id].status == "processed"
    assert result.version_labels["campaign"] == {"campaign-v2"}
    assert result.version_labels["rate_card"] == {"rate-v2"}

    for fact in facts.for_run(result.processing_run.id):
        assert fact.batch_id == batch_id
        assert fact.processing_run_id == result.processing_run.id
        assert fact.campaign_label_version_id == "a0000000-0000-4000-8000-000000000022"
        assert fact.template_label_version_id == "a0000000-0000-4000-8000-000000000034"
        assert fact.rate_card_version_id == "a0000000-0000-4000-8000-000000000012"
        assert fact.label_group_version_id == "a0000000-0000-4000-8000-000000000041"


def test_repeated_transformation_is_idempotent() -> None:
    store, batch_id = build_batch([staged(2)])
    facts = InMemoryFactStore()
    now = datetime(2025, 8, 1, 12, 0, tzinfo=UTC)

    first = _transform(store, facts, batch_id, now=now)
    assert (first.inserted, first.restated, first.unchanged) == (1, 0, 0)

    second = _transform(store, facts, batch_id, now=now)
    assert (second.inserted, second.restated, second.unchanged) == (0, 0, 1)
    assert len(facts.facts) == 1
    assert facts.history == [], "an unchanged fact is not archived"


def test_transformation_is_deterministic() -> None:
    now = datetime(2025, 8, 1, 12, 0, tzinfo=UTC)
    outcomes = [
        transform_row(
            staged(2),
            ConfigBinder(),
            client_id=CLIENT_ID,
            batch_id="batch-1",
            processing_run_id="run-1",
            now=now,
        ).fact
        for _ in range(3)
    ]
    assert outcomes[0] == outcomes[1] == outcomes[2]


def test_restated_fact_is_archived_with_the_superseding_run() -> None:
    store, batch_id = build_batch([staged(2, **{"Delivered": 100, "Channel": "SMS"})])
    facts = InMemoryFactStore()
    first = _transform(store, facts, batch_id)
    first_run_id = first.processing_run.id
    original = next(iter(facts.facts.values()))
    assert original.total_cost == Decimal("15.0000")

    # Restate the same grain from a corrected source value.
    store.staged_rows[0].raw["Delivered"] = 200
    second = _transform(store, facts, batch_id)

    assert second.restated == 1
    assert len(facts.facts) == 1
    current = next(iter(facts.facts.values()))
    assert current.total_cost == Decimal("30.0000")
    assert current.first_seen_at == original.first_seen_at, "lineage keeps the original sighting"

    assert len(facts.history) == 1
    archived = facts.history[0]
    assert archived.fact.total_cost == Decimal("15.0000")
    assert archived.superseded_by_run_id == first_run_id


def test_duplicate_grain_in_one_run_keeps_the_first_row() -> None:
    rows = [
        staged(2, **{"Channel": "SMS", "Delivered": 100}),
        staged(3, **{"Channel": "SMS", "Delivered": 999}),
    ]
    store, batch_id = build_batch(rows)
    facts = InMemoryFactStore()
    result = _transform(store, facts, batch_id)

    assert result.transformed == 1
    assert result.rejected == 1
    assert result.rejections[0].reason_code == "DUPLICATE_GRAIN_KEY"
    assert result.rejections[0].source_row_number == 3
    assert next(iter(facts.facts.values())).total_cost == Decimal("15.0000")


def test_malformed_row_does_not_corrupt_accepted_rows() -> None:
    rows = [
        staged(2, **{"Campaign ID": "ok-1", "Channel": "SMS", "Delivered": 100}),
        staged(3, **{"Campaign ID": "bad-1", "Delivered": "not a number"}),
        staged(4, **{"Campaign ID": "ok-2", "Channel": "SMS", "Delivered": 200}),
    ]
    store, batch_id = build_batch(rows)
    facts = InMemoryFactStore()
    result = _transform(store, facts, batch_id)

    assert result.transformed == 2
    assert result.rejected == 1
    assert {fact.campaign_id for fact in facts.facts.values()} == {"ok-1", "ok-2"}

    rejected = store.rejected_for_batch(batch_id)
    assert [row.reason_code for row in rejected] == ["INVALID_NUMERIC"]
    assert rejected[0].source_row_number == 3


def test_run_without_processing_run_is_refused() -> None:
    store, batch_id = build_batch([staged(2)])
    store.processing_runs.clear()
    with pytest.raises(ValueError, match="unknown processing_run"):
        run_transformation(
            store, InMemoryFactStore(), batch_id, processing_run_id="missing-run"
        )


def test_transformation_targets_explicit_processing_run() -> None:
    store, batch_id = build_batch([staged(2)])
    oldest = store.processing_run_for_batch(batch_id)
    assert oldest is not None
    newest = store.add_processing_run(
        batch_id=batch_id,
        campaign_label_version_id="a0000000-0000-4000-8000-000000000022",
        template_label_version_id="a0000000-0000-4000-8000-000000000034",
        rate_card_version_id="a0000000-0000-4000-8000-000000000012",
        label_group_version_id="a0000000-0000-4000-8000-000000000041",
        engine_version="0.4.0",
    )
    facts = InMemoryFactStore()
    result = run_transformation(store, facts, batch_id, processing_run_id=newest.id)
    assert result.processing_run.id == newest.id
    assert result.processing_run.id != oldest.id
    assert store.get_processing_run(oldest.id).status == "pending"
    for fact in facts.list_current():
        assert fact.processing_run_id == newest.id


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def test_reconciliation_passes_for_a_clean_run() -> None:
    rows = [
        staged(2, **{"Campaign ID": "a", "Channel": "SMS", "Delivered": 10}),
        staged(3, **{"Campaign ID": "b", "Channel": "Push", "Delivered": 10}),
        staged(
            4,
            **{
                "Campaign ID": "c",
                "Channel": "WhatsApp",
                "Delivered": 10,
                "Template Name (WhatsApp)": UTILITY_TEMPLATE,
            },
        ),
    ]
    store, batch_id = build_batch(rows)
    facts = InMemoryFactStore()
    result = _transform(store, facts, batch_id)

    report = reconcile_run(
        facts.for_run(result.processing_run.id),
        rate_card_version_label="rate-v2",
        processing_run_id=result.processing_run.id,
        campaign_label_version_id="a0000000-0000-4000-8000-000000000022",
        template_label_version_id="a0000000-0000-4000-8000-000000000034",
    )
    assert report.passed, report.failures
    assert report.counts["facts"] == 3
    assert report.counts["rate_unmatched"] == 1
    assert report.total_delivered == 30
    assert report.total_cost == Decimal("1.5000") + Decimal("0") + Decimal("1.1500")


def test_reconciliation_detects_a_tampered_fact() -> None:
    fact = transform(**{"Channel": "SMS", "Delivered": 100})
    assert reconcile_fact(fact, "rate-v2") == []

    from dataclasses import replace

    tampered = replace(fact, total_cost=Decimal("999.0000"))
    problems = reconcile_fact(tampered, "rate-v2")
    assert any("total_cost" in problem for problem in problems)


def test_excel_blank_equivalent_normalises_the_vlookup_zero_artifact() -> None:
    """VLOOKUP onto an empty New Logic cell renders as numeric 0 in Excel."""
    assert excel_blank_equivalent(0) is None
    assert excel_blank_equivalent(0.0) is None
    assert excel_blank_equivalent("0") == "0", "a real string label is untouched"
    assert excel_blank_equivalent("NA") == "NA"
    assert excel_blank_equivalent(None) is None


# ---------------------------------------------------------------------------
# Extraction contract
# ---------------------------------------------------------------------------


def test_extraction_preserves_source_text_exactly() -> None:
    fields = extract_source_fields(
        make_raw(**{"Campaign Name": "  Padded Name  ", "Variation Name": "MiXeD"})
    )
    assert fields.campaign_name == "  Padded Name  "
    assert fields.variation_name == "MiXeD"


def test_extraction_rejects_rather_than_repairs() -> None:
    with pytest.raises(RowValidationError) as caught:
        extract_source_fields(make_raw(**{"Sent": "twelve"}))
    assert caught.value.reason_code == "INVALID_NUMERIC"


def test_revenue_columns_are_decimal_not_float() -> None:
    fact = transform(**{"Revenue (INR)": 1234.56})
    assert fact.revenue_inr == Decimal("1234.56")
    assert isinstance(fact.revenue_inr, Decimal)


# ---------------------------------------------------------------------------
# Scope guard
# ---------------------------------------------------------------------------


def test_p4_does_not_introduce_p5_plus_scope() -> None:
    core = Path(__file__).resolve().parents[1] / "packages" / "core" / "dfip_core"
    forbidden = (
        "fastapi",
        "starlette",
        "uvicorn",
        "jwt",
        "login",
        "row level security",
        "publication_current",
        "power query",
    )
    for path in core.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{path.name} references {token!r}"


def test_p4_does_not_compute_kpis() -> None:
    fields = set(FactRecord.__dataclass_fields__)
    for kpi_like in ("ctr", "conversion_rate", "delivered_rate", "open_rate"):
        assert kpi_like not in fields
