"""P1 database foundation — structural tests. No live PostgreSQL required."""

from __future__ import annotations

from dfip_db.catalog import (
    BUSINESS_KEY,
    CAMPAIGN_LABEL_OUTPUTS,
    QA_KPI_FORMULAS,
    QA_KPI_NAMES,
    REQUIRED_INDEXES,
    REQUIRED_TABLES,
    SOURCE_COLUMNS,
)
from dfip_db.migrate import LEDGER_TABLE
from dfip_db.paths import migration_files, read_migrations, read_v1_migrations
from dfip_db.sql_inspect import (
    parse_indexes,
    parse_insert_tuples,
    parse_tables,
    strip_sql_comments,
    unquote_sql_string,
    validate_sql_shape,
)


def test_migration_files_are_ordered_sql() -> None:
    files = migration_files()
    names = [path.name for path in files]
    assert names == sorted(names)
    assert len(files) >= 4
    assert all(path.suffix == ".sql" for path in files)
    assert names[0].endswith("_p1_extensions.sql")


def test_migration_ledger_is_not_a_second_sql_tree() -> None:
    for path in migration_files():
        assert LEDGER_TABLE not in path.read_text(encoding="utf-8")


def test_migrations_are_syntactically_shaped() -> None:
    sql = read_migrations()
    problems = validate_sql_shape(sql)
    assert problems == []


def test_required_tables_exist() -> None:
    tables = parse_tables(read_migrations())
    missing = [name for name in REQUIRED_TABLES if name not in tables]
    assert missing == []


def test_foreign_keys_point_at_defined_tables() -> None:
    tables = parse_tables(read_migrations())
    unknown = []
    for table in tables.values():
        for _local, target in table.foreign_keys:
            if target not in tables:
                unknown.append((table.name, target))
    assert unknown == []


def test_required_indexes_exist() -> None:
    indexes = parse_indexes(read_migrations())
    missing = [name for name in REQUIRED_INDEXES if name not in indexes]
    assert missing == []
    assert indexes["campaign_label_row_lookup_idx"][0] == "campaign_label_row"
    assert indexes["campaign_label_row_lookup_idx"][1] == (
        "version_id",
        "campaign_name_key",
        "row_order",
    )
    assert indexes["template_label_row_lookup_idx"][1] == (
        "version_id",
        "template_name_key",
        "row_order",
    )


def test_duplicate_campaign_names_are_allowed() -> None:
    table = parse_tables(read_migrations())["campaign_label_row"]
    unique_sets = {cols for cols in table.unique}
    assert ("campaign_name",) not in unique_sets
    assert ("campaign_name_key",) not in unique_sets
    assert ("version_id", "campaign_name") not in unique_sets
    assert ("version_id", "campaign_name_key") not in unique_sets
    assert ("version_id", "row_order") in unique_sets
    assert "row_order" in table.columns
    assert "campaign_name_key" in table.columns
    assert "lower(campaign_name)" in table.body


def test_template_lookup_structure() -> None:
    table = parse_tables(read_migrations())["template_label_row"]
    for col in ("row_order", "template_name", "template_name_key", "template_status"):
        assert col in table.columns
    assert ("version_id", "row_order") in set(table.unique)
    assert ("template_name",) not in set(table.unique)
    assert "lower(template_name)" in table.body


def test_six_campaign_label_outputs_exist() -> None:
    table = parse_tables(read_migrations())["campaign_label_row"]
    missing = [col for col in CAMPAIGN_LABEL_OUTPUTS if col not in table.columns]
    assert missing == []
    assert "unused_sheet_template_status" in table.columns


def test_business_key_on_fact() -> None:
    table = parse_tables(read_migrations())["fact_campaign_day"]
    assert table.primary_key == ("client_id", "campaign_id", "variation_id_key", "day")
    for col in BUSINESS_KEY:
        assert col in table.columns
    assert "variation_id_key" in table.columns
    assert "total_cost" in table.columns
    for col in CAMPAIGN_LABEL_OUTPUTS:
        assert col in table.columns


def test_publication_fact_snapshot_table_shape() -> None:
    table = parse_tables(read_migrations())["publication_fact"]
    assert table.primary_key == (
        "publication_id",
        "client_id",
        "campaign_id",
        "variation_id_key",
        "day",
    )
    assert "sent" in table.columns
    targets = {target for _local, target in table.foreign_keys}
    assert "publication" in targets
    assert "client" in targets


def test_publication_history_grain_table_shape() -> None:
    table = parse_tables(read_migrations())["publication_history_grain"]
    assert table.primary_key == (
        "client_id",
        "campaign_id",
        "variation_id_key",
        "day",
    )
    assert "sent" in table.columns
    assert "month_label" in table.columns
    assert "publication_id" not in table.columns
    targets = {target for _local, target in table.foreign_keys}
    assert "client" in targets


def test_excel_workbook_grant_table_shape() -> None:
    table = parse_tables(read_migrations())["excel_workbook_grant"]
    assert table.primary_key == ("id",)
    assert "jti" in table.columns
    assert "user_id" in table.columns
    assert "client_id" in table.columns
    assert "expires_at" in table.columns
    assert "revoked_at" in table.columns
    targets = {target for _local, target in table.foreign_keys}
    assert "app_user" in targets
    assert "client" in targets


def test_analytics_saved_analysis_table_shape() -> None:
    table = parse_tables(read_migrations())["analytics_saved_analysis"]
    assert table.primary_key == ("id",)
    assert "owner_subject" in table.columns
    assert "client_id" in table.columns
    assert "title" in table.columns
    assert "state" in table.columns
    targets = {target for _local, target in table.foreign_keys}
    assert "client" in targets
    assert "app_user" not in targets


def test_source_column_catalog_seed_matches_contract() -> None:
    rows = parse_insert_tuples(read_migrations(), "we_source_column")
    assert len(rows) == 67
    seeded = []
    for row in rows:
        seeded.append(
            (
                int(row[0]),
                unquote_sql_string(row[1]),
                unquote_sql_string(row[2]),
                unquote_sql_string(row[3]),
                unquote_sql_string(row[4]),
                unquote_sql_string(row[5]),
            )
        )
    expected = [
        (
            col.excel_position,
            col.excel_letter,
            col.excel_header,
            col.db_column,
            col.source_role,
            col.value_kind,
        )
        for col in SOURCE_COLUMNS
    ]
    assert seeded == expected
    headers = [col.excel_header for col in SOURCE_COLUMNS]
    assert "AMC Device Category -  Filter Logic 4" in headers
    assert "AMC Product Cat -  Filter Logic 5" in headers
    assert sum(1 for col in SOURCE_COLUMNS if col.source_role == "web_engage_source") == 57
    assert sum(1 for col in SOURCE_COLUMNS if col.source_role == "derived_excel") == 10


def test_kpi_registry_has_exactly_sixteen_qa_definitions() -> None:
    rows = parse_insert_tuples(read_migrations(), "kpi_definition")
    qa_rows = [row for row in rows if unquote_sql_string(row[1]) == "qa"]
    assert len(qa_rows) == 16
    names = tuple(unquote_sql_string(row[2]) for row in qa_rows)
    formulas = tuple(unquote_sql_string(row[4]) for row in qa_rows)
    orders = tuple(int(row[9]) for row in qa_rows)
    assert names == QA_KPI_NAMES
    assert formulas == QA_KPI_FORMULAS
    assert orders == tuple(range(1, 17))
    kpi_10 = "a0000000-0000-4000-8000-000000000210"
    assert unquote_sql_string(qa_rows[11][10]) == kpi_10
    assert unquote_sql_string(qa_rows[12][10]) == kpi_10
    assert unquote_sql_string(qa_rows[9][10]) == ""


def test_rate_card_precedence_is_represented() -> None:
    versions = parse_insert_tuples(read_migrations(), "rate_card_version")
    labels = {unquote_sql_string(row[2]) for row in versions}
    assert labels == {"rate-v1", "rate-v2"}
    rules = parse_insert_tuples(read_migrations(), "rate_card_rule")
    assert len(rules) == 10
    by_version: dict[str, list[tuple[int, str, str, str]]] = {}
    for row in rules:
        version = unquote_sql_string(row[1])
        by_version.setdefault(version, []).append(
            (
                int(row[2]),
                unquote_sql_string(row[3]),
                unquote_sql_string(row[4]),
                unquote_sql_string(row[6]),
            )
        )
    for version_rules in by_version.values():
        ordered = sorted(version_rules, key=lambda item: item[0])
        assert [item[0] for item in ordered] == [1, 2, 3, 4, 5]
        assert ordered[0][1] == "template_status"
        assert ordered[0][2] == "Utility"
        assert [item[1] for item in ordered[1:]] == ["channel", "channel", "channel", "channel"]
        assert [item[2] for item in ordered[1:]] == ["SMS", "Email", "RCS", "WhatsApp"]
    v2_id = "a0000000-0000-4000-8000-000000000012"
    v2 = sorted(by_version[v2_id], key=lambda item: item[0])
    assert [item[3] for item in v2] == [
        "0.115000",
        "0.150000",
        "0.010000",
        "0.250000",
        "0.785000",
    ]


def test_no_rls_or_auth_policies() -> None:
    sql = read_v1_migrations().lower()
    assert "enable row level security" not in sql
    assert "create policy" not in sql
    assert "auth.users" not in sql


def test_no_kpi_generated_columns_on_fact() -> None:
    body = parse_tables(read_migrations())["fact_campaign_day"].body.lower()
    assert "ctr" not in body
    assert "roas" not in body
    assert "delivered_rate" not in body


def test_login_role_must_not_keep_table_grants() -> None:
    files = {path.name: path.read_text(encoding="utf-8") for path in migration_files()}
    name = "20260914000029_revoke_dfip_app_table_grants.sql"
    assert name in files
    sql = files[name]
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM dfip_app" in sql
    assert "GRANT " not in strip_sql_comments(sql)
    assert "BYPASSRLS" not in sql.upper()
