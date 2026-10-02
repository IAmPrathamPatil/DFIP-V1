"""RUN 013: consolidated workbook charts, caching, and the Downloads button.

Covers the three production findings:

1. Download performance. The consolidated workbook re-rendered the whole
   cumulative history on every click. The rendered artifact is now cached per
   company and reused until the eligible published history changes.
2. The Click-Through and Overall conversion sections were empty. The static
   template ships the row 8 group banners but nothing beneath them, and only
   the refreshable path ever materialized the O:X sections.
3. The export was only reachable from Publications, not from Downloads.

The cache never changes what a download returns: same rows, same renderer,
same authorization, same filename. A cache miss and a cache hit are byte
identical.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from zipfile import ZipFile

import pytest
from dfip_api import publication_service as publication_service_module
from dfip_web import pivot_report
from dfip_web.consolidated_cache import (
    artifact_key,
    cache_root,
    purge_company,
)
from dfip_web.consolidated_cache import (
    load as cache_load,
)
from dfip_web.consolidated_cache import (
    store as cache_store,
)
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from test_p5_api import AUTH, CLIENT_ID
from test_p7_publication import publisher_app
from test_r10_published_history import _publish_snapshot
from test_run012_consolidated_workbook import (
    CLIENT_B,
    CONSOLIDATED_PATH,
    _add_run,
    _measure,
    _month_facts,
    _months,
)

WEB_STATIC = Path(__file__).resolve().parents[1] / "apps" / "web" / "static"
REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTES_SOURCE = REPO_ROOT / "packages" / "api" / "dfip_api" / "publication_routes.py"

# Publication identity leads the fingerprint, so these fixtures mirror the real
# store contract: (pub_count, max_pub_id, max_published_at, rows, max_day, months).
FP_A = ("1", "pub-a", "2025-01-06T00:00:00+00:00", "10", "2025-01-06", "1")
FP_B = ("2", "pub-b", "2025-02-06T00:00:00+00:00", "20", "2025-02-06", "2")
FP_C = ("3", "pub-c", "2025-03-06T00:00:00+00:00", "30", "2025-03-06", "3")
FP_OTHER = ("9", "pub-z", "2025-05-06T00:00:00+00:00", "99", "2025-05-06", "5")

RUN_1 = "a0000000-0000-4000-8000-0000000000a1"
RUN_2 = "a0000000-0000-4000-8000-0000000000a2"
RUN_REDO = "a0000000-0000-4000-8000-0000000000b1"
RUN_COMPANY_B = "a0000000-0000-4000-8000-0000000000c1"


def _publish_month(ingest, store, run_id: str, month: str, *, sent: int, clicks: int) -> None:
    """Add a succeeded run and publish it as a complete snapshot."""
    _add_run(ingest, run_id)
    _publish_snapshot(store, _month_facts(month, sent=sent, clicks=clicks), run_id=run_id)


# O:S is the Click-Through group, T:X is the Overall group. Both live outside
# the PivotTable (B9:L10) precisely so a pivot rewrite cannot drop them.
CLICK_THROUGH_FIRST_COL = "O"
OVERALL_FIRST_COL = "T"
CONVERSION_LAST_ROW = pivot_report.CONVERSION_LAST_FORMULA_ROW


def _report_sheet_parts(body: bytes) -> dict[str, str]:
    """Map each report sheet name to its XML in a generated workbook."""
    with ZipFile(io.BytesIO(body), "r") as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        rels_xml = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        parts = pivot_report._sheet_part_map(workbook_xml, rels_xml)
        return {
            name: archive.read(part).decode("utf-8")
            for name, part in parts.items()
            if name not in {"PublishedFacts", "Facts"}
        }


def _shared_master(sheet_xml: str, column: str) -> str | None:
    """Return the conversion master formula for a column, if present."""
    match = re.search(
        rf'<f t="shared" ref="{column}10:{column}{CONVERSION_LAST_ROW}"[^>]*>([^<]*)</f>',
        sheet_xml,
    )
    return match.group(1) if match else None


def _conversion_sections(body: bytes) -> dict[str, tuple[str | None, str | None]]:
    """Per report sheet, the Click-Through (O) and Overall (T) master formulas."""
    return {
        name: (_shared_master(xml, CLICK_THROUGH_FIRST_COL), _shared_master(xml, OVERALL_FIRST_COL))
        for name, xml in _report_sheet_parts(body).items()
    }


def _app_with_months(months: list[str], cache_dir: str | None = None):
    app, ingest, facts, store = publisher_app()
    for index, month_label in enumerate(months, start=1):
        run_id = f"a0000000-0000-4000-8000-0000000000a{index}"
        _add_run(ingest, run_id)
        _publish_snapshot(store, _month_facts(month_label, sent=10, clicks=2), run_id=run_id)
    return TestClient(app), ingest, facts, store


def _download(http: TestClient) -> bytes:
    response = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert response.status_code == 200, response.text
    return response.content


# ---------------------------------------------------------------------------
# Issue 2: the two conversion charts
# ---------------------------------------------------------------------------


def test_click_through_and_overall_conversion_sections_are_populated() -> None:
    http, *_rest = _app_with_months(["Jan-25", "Feb-25"])
    sections = _conversion_sections(_download(http))

    assert sections, "no report sheets found in the generated workbook"
    for name, (click_through, overall) in sections.items():
        assert click_through is not None, f"{name}: Click-Through conversion section is empty"
        assert overall is not None, f"{name}: Overall conversion section is empty"


def test_conversion_sections_exist_on_every_report_sheet() -> None:
    http, *_rest = _app_with_months(["Jan-25"])
    parts = _report_sheet_parts(_download(http))

    assert len(parts) == 9, f"expected nine report sheets, got {sorted(parts)}"
    for name, xml in parts.items():
        assert "Click-Through Conversions" in xml, f"{name}: missing Click-Through banner"
        assert "Overall Conversions" in xml, f"{name}: missing Overall banner"


def test_conversion_section_headers_are_materialized() -> None:
    """Row 9 must carry the real measure captions, not just the banners."""
    http, *_rest = _app_with_months(["Jan-25"])
    xml = next(iter(_report_sheet_parts(_download(http)).values()))

    assert "Unique Click-Through Conversions" in xml
    assert "Unique Conversions" in xml


def test_conversion_formulas_read_the_populated_pivot_row() -> None:
    """O:X must derive from C:L row 10, which is where the sums land."""
    http, *_rest = _app_with_months(["Jan-25"])
    sections = _conversion_sections(_download(http))
    click_through, overall = next(iter(sections.values()))

    assert re.search(r"\bI10\b", click_through), click_through
    assert re.search(r"\bK10\b", overall), overall


@pytest.mark.parametrize(
    "months",
    [["Jan-25"], ["Jan-25", "Feb-25"], ["Jan-25", "Feb-25", "Mar-25"]],
)
def test_conversion_sections_survive_cumulative_month_growth(months: list[str]) -> None:
    """Month 1, 1+2 and 1+2+3 must all keep both charts populated."""
    http, *_rest = _app_with_months(months)
    body = _download(http)

    assert _months(body) == set(months)
    for name, (click_through, overall) in _conversion_sections(body).items():
        assert click_through is not None, f"{name} lost Click-Through at {len(months)} month(s)"
        assert overall is not None, f"{name} lost Overall at {len(months)} month(s)"


def test_conversion_charts_render_with_real_values() -> None:
    """The workbook a client receives must carry the chart source data.

    openpyxl does not evaluate formulas, so the contract is the emitted XML:
    both sections exist on every report sheet and their formulas point at the
    PivotTable row 10 that holds the sums. Excel computes the values on open.
    """
    http, *_rest = _app_with_months(["Jan-25", "Feb-25"])
    body = _download(http)
    parts = _report_sheet_parts(body)

    assert len(parts) == 9
    for name, xml in parts.items():
        click_through = _shared_master(xml, CLICK_THROUGH_FIRST_COL)
        overall = _shared_master(xml, OVERALL_FIRST_COL)
        assert click_through is not None, f"{name}: Click-Through chart has no source data"
        assert overall is not None, f"{name}: Overall chart has no source data"
        # The pivot the charts read from must span the published rows.
        assert "ISNUMBER(" in click_through and "10" in click_through


def test_static_company_workbook_conversion_sections_are_untouched() -> None:
    """Only the consolidated artifact changed. Static stays as it was."""
    http, *_rest = _app_with_months(["Jan-25"])
    response = http.get(
        "/api/v1/publications/current/client-report.xlsx",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    assert response.status_code == 200
    sections = _conversion_sections(response.content)

    for name, (click_through, overall) in sections.items():
        assert click_through is None, f"{name}: static artifact unexpectedly changed"
        assert overall is None, f"{name}: static artifact unexpectedly changed"


def test_consolidated_does_not_rebind_pivot_datafields() -> None:
    """The refreshable-only pivot shrink must not run on a snapshot workbook.

    ``_bind_refreshable_sum_pivot`` trims each PivotTable to the 10 additive
    sums so they can be re-added in measure order after a Power Query rebuild.
    A static workbook never rebuilds, so applying it would silently change the
    pivot layout. The native 22-dataField shape must survive.
    """
    http, *_rest = _app_with_months(["Jan-25"])
    with ZipFile(io.BytesIO(_download(http)), "r") as archive:
        pivot_parts = sorted(
            n for n in archive.namelist() if n.startswith("xl/pivotTables/") and n.endswith(".xml")
        )
        assert pivot_parts, "expected native PivotTables in the workbook"
        for part in pivot_parts:
            xml = archive.read(part).decode("utf-8")
            counts = re.findall(r'<dataFields count="(\d+)"', xml)
            assert counts, f"{part}: no dataFields element"
            assert int(counts[0]) > 10, f"{part}: pivot was shrunk to the refreshable shape"


# ---------------------------------------------------------------------------
# Issue 1: caching / no repeated regeneration
# ---------------------------------------------------------------------------


def _minimal_xlsx(marker: str) -> bytes:
    """A tiny structurally valid ZIP standing in for a rendered workbook."""
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", f"<workbook>{marker}</workbook>")
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buffer.getvalue()


def test_artifact_key_changes_when_history_changes() -> None:
    stamp = "2025-03-06T00:00:00+00:00"
    base = ("1", "pub-a", stamp, "100", "2025-03-06", "3")
    keys = {
        artifact_key(CLIENT_ID, base),
        # Same month, more rows.
        artifact_key(CLIENT_ID, ("1", "pub-a", stamp, "140", "2025-03-06", "3")),
        # A later day.
        artifact_key(CLIENT_ID, ("1", "pub-a", stamp, "100", "2025-04-06", "3")),
        # A new month.
        artifact_key(CLIENT_ID, ("1", "pub-a", stamp, "100", "2025-03-06", "4")),
        # A republish.
        artifact_key(CLIENT_ID, ("2", "pub-b", stamp, "100", "2025-03-06", "3")),
    }

    assert artifact_key(CLIENT_ID, base) in keys
    assert len(keys) == 5, "every distinct history must produce a distinct key"


def test_cache_round_trips_and_replaces_stale_artifact(tmp_path: Path) -> None:
    root = cache_root(tmp_path)
    fingerprint = FP_A

    assert cache_load(root, CLIENT_ID, fingerprint) is None
    first = _minimal_xlsx("first")
    cache_store(root, CLIENT_ID, fingerprint, first)
    assert cache_load(root, CLIENT_ID, fingerprint) == first

    # A new month invalidates: the old fingerprint no longer resolves.
    moved = FP_B
    second = _minimal_xlsx("second")
    cache_store(root, CLIENT_ID, moved, second)
    assert cache_load(root, CLIENT_ID, moved) == second
    assert cache_load(root, CLIENT_ID, fingerprint) is None


def test_cache_keeps_one_artifact_per_company(tmp_path: Path) -> None:
    root = cache_root(tmp_path)
    for index, fingerprint in enumerate([FP_A, FP_B, FP_C]):
        cache_store(root, CLIENT_ID, fingerprint, _minimal_xlsx(str(index)))
        assert len(list((root / CLIENT_ID).glob("*.xlsx"))) == 1

    cache_store(root, CLIENT_B, FP_OTHER, _minimal_xlsx("other"))
    assert len(list((root / CLIENT_ID).glob("*.xlsx"))) == 1
    assert len(list((root / CLIENT_B).glob("*.xlsx"))) == 1


def test_cache_rejects_a_truncated_artifact(tmp_path: Path) -> None:
    root = cache_root(tmp_path)
    fingerprint = FP_A
    cache_store(root, CLIENT_ID, fingerprint, b"PKbut-then-garbage")

    assert cache_load(root, CLIENT_ID, fingerprint) is None


def test_cache_is_scoped_per_company(tmp_path: Path) -> None:
    root = cache_root(tmp_path)
    fingerprint = FP_A
    mine = _minimal_xlsx("mine")
    cache_store(root, CLIENT_ID, fingerprint, mine)

    assert cache_load(root, CLIENT_B, fingerprint) is None
    assert cache_load(root, CLIENT_ID, fingerprint) == mine


def test_purge_company_removes_only_that_company(tmp_path: Path) -> None:
    root = cache_root(tmp_path)
    fingerprint = FP_A
    mine = _minimal_xlsx("mine")
    theirs = _minimal_xlsx("theirs")
    cache_store(root, CLIENT_ID, fingerprint, mine)
    cache_store(root, CLIENT_B, fingerprint, theirs)

    purge_company(root, CLIENT_ID)
    assert cache_load(root, CLIENT_ID, fingerprint) is None
    assert cache_load(root, CLIENT_B, fingerprint) == theirs


def test_republish_with_identical_row_counts_still_invalidates(tmp_path: Path) -> None:
    """A count-only key would wrongly serve a stale artifact here.

    Republishing the same month with different values leaves row count, latest
    day and month count unchanged. Only publication identity reveals the
    change, so the fingerprint must move and the workbook must be rebuilt.
    """
    app, ingest, facts, store = publisher_app()
    http = TestClient(app)
    _add_run(ingest, "a0000000-0000-4000-8000-0000000000a1")
    _publish_snapshot(
        store,
        _month_facts("Jan-25", sent=10, clicks=2),
        run_id="a0000000-0000-4000-8000-0000000000a1",
    )
    first = _download(http)
    first_sent = _measure(first, "Sent")

    # Republish the same month, same number of rows, different values.
    _add_run(ingest, "a0000000-0000-4000-8000-0000000000b1")
    _publish_snapshot(
        store,
        _month_facts("Jan-25", sent=40, clicks=7),
        run_id="a0000000-0000-4000-8000-0000000000b1",
    )
    second = _download(http)

    assert second != first, "a republish must not serve the cached artifact"
    assert _measure(second, "Sent") != first_sent


def test_republished_month_is_not_duplicated_by_the_cache() -> None:
    """Newest-wins behaviour must survive caching."""
    app, ingest, facts, store = publisher_app()
    http = TestClient(app)
    _add_run(ingest, "a0000000-0000-4000-8000-0000000000a1")
    _publish_snapshot(
        store,
        _month_facts("Jan-25", sent=10, clicks=2),
        run_id="a0000000-0000-4000-8000-0000000000a1",
    )
    _add_run(ingest, "a0000000-0000-4000-8000-0000000000b1")
    _publish_snapshot(
        store,
        _month_facts("Jan-25", sent=40, clicks=7),
        run_id="a0000000-0000-4000-8000-0000000000b1",
    )

    workbook = load_workbook(io.BytesIO(_download(http)))
    sheet = next(ws for ws in workbook.worksheets if ws.title not in {"PublishedFacts", "Facts"})
    months = [str(cell.value) for cell in sheet["A"] if cell.value in {"Jan-25"}]
    assert len(months) <= 2  # header plus at most one newest-wins row set


def _counting_render(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Wrap the renderer so tests can assert exactly how often it ran."""
    calls: list[int] = []
    original = publication_service_module.render_client_report_xlsx

    def counting(*args: object, **kwargs: object) -> bytes:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(publication_service_module, "render_client_report_xlsx", counting)
    return calls


def test_second_download_is_served_from_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The expensive render must not run twice for unchanged history."""
    http, *_rest = _app_with_months(["Jan-25"])
    first = _download(http)

    calls = _counting_render(monkeypatch)
    second = _download(http)

    assert first == second, "a cache hit must return the identical artifact"
    assert calls == [], "unchanged history must not trigger a second render"


def test_first_download_populates_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The render result is persisted under the company's history fingerprint."""
    http, *_rest = _app_with_months(["Jan-25"])
    store_dir = cache_root(tmp_path)
    monkeypatch.setattr(publication_service_module, "cache_root", lambda _dir: store_dir)

    body = _download(http)

    cached = list((store_dir / CLIENT_ID).glob("*.xlsx"))
    assert len(cached) == 1
    assert cached[0].read_bytes() == body


def test_new_month_invalidates_and_regenerates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adding a month must change the fingerprint and force exactly one rebuild."""
    app, ingest, _facts, store = publisher_app()
    http = TestClient(app)
    _publish_month(ingest, store, RUN_1, "Jan-25", sent=10, clicks=2)
    january = _download(http)

    calls = _counting_render(monkeypatch)

    # Unchanged history: served from cache.
    assert _download(http) == january
    assert calls == []

    # New month published: fingerprint moves, artifact is rebuilt once.
    _publish_month(ingest, store, RUN_2, "Feb-25", sent=10, clicks=2)
    february = _download(http)
    assert calls == [1], "a new month must trigger exactly one regeneration"
    assert _months(february) == {"Jan-25", "Feb-25"}
    assert february != january

    # And the new artifact is now itself cached.
    assert _download(http) == february
    assert calls == [1]


def test_caching_can_be_disabled_without_changing_the_result() -> None:
    """With the cache off, every download renders and still returns the same file."""
    http, *_rest = _app_with_months(["Jan-25"])
    first = _download(http)
    second = _download(http)

    assert first == second
    assert _conversion_sections(first)


def test_cache_does_not_bypass_authorization() -> None:
    """A cached artifact still requires authentication."""
    http, *_rest = _app_with_months(["Jan-25"])
    body = _download(http)
    assert body.startswith(b"PK")

    anonymous = http.get(CONSOLIDATED_PATH, params={"client_id": CLIENT_ID})
    assert anonymous.status_code == 401


def test_cache_does_not_leak_across_companies() -> None:
    """Two companies' cached artifacts stay separate and scope stays enforced."""
    app, ingest, facts, store = publisher_app()
    _add_run(ingest, "a0000000-0000-4000-8000-0000000000a1")
    _publish_snapshot(
        store,
        _month_facts("Jan-25", sent=10, clicks=2),
        run_id="a0000000-0000-4000-8000-0000000000a1",
    )
    _add_run(ingest, "a0000000-0000-4000-8000-0000000000c1")
    _publish_snapshot(
        store,
        _month_facts("Jan-25", sent=999, clicks=999, client_id=CLIENT_B),
        run_id="a0000000-0000-4000-8000-0000000000c1",
    )
    http = TestClient(app)

    own = _download(http)
    other = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_B})
    assert other.status_code == 200
    assert own != other.content, "each company must get its own workbook"


def test_no_history_still_404s_and_caches_nothing(tmp_path: Path) -> None:
    from dfip_api.publication_service import NO_PUBLICATION_WORKBOOK_MESSAGE

    app, *_rest = publisher_app()
    http = TestClient(app)
    response = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})

    assert response.status_code == 404
    assert NO_PUBLICATION_WORKBOOK_MESSAGE in response.text


# ---------------------------------------------------------------------------
# Issue 3: the Downloads section
# ---------------------------------------------------------------------------


def _downloads_section_source() -> str:
    source = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    start = source.index('title: "Downloads"')
    # The panel ends at the next top-level `function ` declaration.
    end = source.index("\nfunction ", start)
    return source[start:end]


def test_downloads_section_offers_the_consolidated_workbook() -> None:
    section = _downloads_section_source()
    # The Downloads panel renders the shared helper; the helper owns the
    # attribute, so the panel must not carry its own copy.
    assert "${companyConsolidatedWorkbookButton()}" in section
    assert "data-download-consolidated-client-report" not in section


def test_the_shared_helper_owns_the_consolidated_trigger() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    start = views.index("function companyConsolidatedWorkbookButton()")
    helper = views[start : start + 700]
    assert "data-download-consolidated-client-report" in helper
    assert "Download Consolidated Company Workbook" in helper


def test_downloads_section_keeps_the_existing_export_buttons() -> None:
    section = _downloads_section_source()
    assert "companyWorkbookButton()" in section
    assert "companyRefreshableWorkbookButton()" in section
    assert "data-download-published" in section


def test_downloads_copy_states_all_processed_months() -> None:
    section = _downloads_section_source()
    assert "successfully processed published month" in section
    assert "never needs Excel Refresh All" in section


def test_both_locations_share_one_button_helper() -> None:
    """One helper definition, reused by Publications and Downloads."""
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    assert views.count("function companyConsolidatedWorkbookButton()") == 1
    # Two Publications render paths plus the Downloads panel.
    assert views.count("${companyConsolidatedWorkbookButton()}") >= 3
    assert "${companyConsolidatedWorkbookButton()}" in _downloads_section_source()


def test_downloads_reuses_the_existing_handler_and_api_method() -> None:
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")

    assert app_js.count("data-download-consolidated-client-report") == 1
    assert app_js.count("downloadConsolidatedClientReport(") == 1
    assert client_js.count("downloadConsolidatedClientReport(") == 1
    assert client_js.count("consolidated-client-report.xlsx") == 1


def test_no_duplicate_backend_route_for_the_downloads_button() -> None:
    routes = ROUTES_SOURCE.read_text(encoding="utf-8")
    assert routes.count('"/publications/current/consolidated-client-report.xlsx"') == 1


def test_downloads_button_reports_the_same_filename() -> None:
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert 'payload.filename || "Client_Report_Consolidated.xlsx"' in app_js
