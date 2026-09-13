"""Cumulative history CSV: same tenant snapshot as history/facts JSON.

Does not change JSON pagination. Does not rewrite DataMashup.
"""

from __future__ import annotations

import csv
import io
from datetime import date

from dfip_api.schemas import FACT_TABLE_COLUMNS
from dfip_db.mapping import FACT_WIRE_COLUMNS
from dfip_web.client_report_download import FACT_VALUE_FIELDS
from dfip_web.published_facts_pages import PUBLISHED_FACTS_CSV_RELATIVE_PATH
from fastapi.testclient import TestClient

from test_p5_api import AUTH, CLIENT_ID, JWT_SECRET, _encode_jwt, make_settings
from test_p7_publication import _publish, publisher_app
from test_r10_published_history import CLIENT_B, HISTORY_PATH, _fact, _publish_snapshot

HISTORY_CSV_PATH = PUBLISHED_FACTS_CSV_RELATIVE_PATH


def _csv_rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_history_csv_column_contract_matches_json_and_workbook() -> None:
    assert tuple(FACT_WIRE_COLUMNS) == FACT_VALUE_FIELDS
    assert tuple(FACT_TABLE_COLUMNS) == FACT_VALUE_FIELDS


def test_history_csv_requires_auth_and_matches_json_items() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    assert http.get(HISTORY_CSV_PATH).status_code == 401
    empty = http.get(HISTORY_CSV_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert empty.status_code == 200
    assert "text/csv" in empty.headers["content-type"]
    empty_rows = _csv_rows(empty.text)
    assert empty_rows == [list(FACT_VALUE_FIELDS)]
    created = _publish(http)
    assert created.status_code == 201
    json_body = http.get(
        HISTORY_PATH,
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 200},
    )
    csv_body = http.get(HISTORY_CSV_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert json_body.status_code == 200
    assert csv_body.status_code == 200
    items = json_body.json()["items"]
    rows = _csv_rows(csv_body.text)
    assert rows[0] == list(FACT_VALUE_FIELDS)
    assert len(rows) - 1 == len(items)
    day_idx = FACT_VALUE_FIELDS.index("day")
    camp_idx = FACT_VALUE_FIELDS.index("campaign_id")
    json_keys = {(item["campaign_id"], item["day"]) for item in items}
    csv_keys = {(row[camp_idx], row[day_idx]) for row in rows[1:]}
    assert csv_keys == json_keys


def test_history_csv_is_tenant_scoped() -> None:
    app, _ingest, _facts, store = publisher_app()
    _publish_snapshot(
        store,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-a",
                day=date(2025, 10, 1),
                month_label="Oct-25",
                sent=1,
                unique_conversions=1,
                unique_click_through_conversions=1,
            )
        ],
        run_id="run-a",
    )
    _publish_snapshot(
        store,
        [
            _fact(
                client_id=CLIENT_B,
                campaign_id="camp-b",
                day=date(2025, 10, 1),
                month_label="Oct-25",
                sent=2,
                unique_conversions=2,
                unique_click_through_conversions=2,
            )
        ],
        run_id="run-b",
    )
    http = TestClient(app)
    own = _csv_rows(
        http.get(HISTORY_CSV_PATH, headers=AUTH, params={"client_id": CLIENT_ID}).text
    )
    other = _csv_rows(
        http.get(HISTORY_CSV_PATH, headers=AUTH, params={"client_id": CLIENT_B}).text
    )
    camp_idx = FACT_VALUE_FIELDS.index("campaign_id")
    assert {row[camp_idx] for row in own[1:]} == {"camp-a"}
    assert {row[camp_idx] for row in other[1:]} == {"camp-b"}
    from dfip_api.app import create_app

    bound = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=_ingest,
        fact_store=_facts,
        publication_store=store,
    )
    headers = {"Authorization": f"Bearer {_encode_jwt(role='client', client_id=CLIENT_ID)}"}
    jwt_http = TestClient(bound)
    leaked = jwt_http.get(HISTORY_CSV_PATH, headers=headers, params={"client_id": CLIENT_B})
    assert leaked.status_code == 403
    scoped = jwt_http.get(HISTORY_CSV_PATH, headers=headers)
    assert scoped.status_code == 200
    scoped_rows = _csv_rows(scoped.text)
    assert {row[camp_idx] for row in scoped_rows[1:]} == {"camp-a"}
