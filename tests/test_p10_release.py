"""P10 V1 closeout: documentation, UI copy, and release-state contracts.

Does not change P0–P9 API behavior. V2 architecture is not implemented.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_status_declares_v1_complete_and_p10_final() -> None:
    status = _read("documentation/STATUS.md")
    assert "**DFIP-V1 COMPLETE**" in status
    assert "## DFIP-V1 COMPLETE" in status
    assert "acceptance" in status
    assert "P10" in status
    assert "P11" in status
    assert "PivotTable" in status or "native Pivot" in status
    assert "P12" in status
    assert "## V2 (not implemented)" in status
    assert "GET /api/v1/facts" in status or "`/api/v1/facts`" in status
    assert "not publication-filtered" in status
    assert "not PostgreSQL RLS" in status
    assert "104,768" in status
    assert "29,129" in status


def test_readme_phase_table_includes_p11() -> None:
    readme = _read("README.md")
    assert "DFIP-V1 COMPLETE" in readme
    assert "| **P10** |" in readme
    assert "| **P11** |" in readme
    assert "### V2 (not implemented)" in readme
    assert "python -m pytest" in readme
    assert "ruff format --check" in readme


def test_publication_docs_do_not_promise_another_v1_phase() -> None:
    publication = _read("documentation/PUBLICATION.md")
    assert "later phases" not in publication
    assert "V2" in publication
    assert "authentication hook" in publication
    assert "production identity provider" in publication


def test_spa_does_not_claim_publication_or_api_authz_unimplemented() -> None:
    components = _read("apps/web/static/js/components.js")
    views = _read("apps/web/static/js/views.js")
    app_js = _read("apps/web/static/js/app.js")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in WEB_STATIC.rglob("*.js"))
    assert "Publication (not implemented)" not in combined
    assert "API authorization and RLS are not implemented" not in combined
    assert "does not yet enforce role" not in views
    assert "The API does not yet enforce" not in combined
    assert "listFacts" in app_js
    assert "listPublishedFacts" in app_js
    assert "loadPublishedFact" in app_js
    assert "Publish from Admin overview" in components
    assert "application-level" in components.lower() or "application-level" in views
    assert "not PostgreSQL RLS" in components or "not PostgreSQL RLS" in views


def test_pyproject_is_v1_release_identity() -> None:
    pyproject = _read("pyproject.toml")
    assert 'version = "1.0.0"' in pyproject
    assert 'version = "0.0.0"' not in pyproject
    assert "complete" in pyproject.lower()


def test_ci_runs_ruff_format_check() -> None:
    workflow = _read(".github/workflows/ci.yml")
    assert "ruff format --check packages tests" in workflow
    assert "python -m pytest" in workflow


def test_v2_features_are_not_claimed_as_v1() -> None:
    status = _read("documentation/STATUS.md")
    readme = _read("README.md")
    v2 = status[status.index("## V2 (not implemented)") :]
    for token in (
        "PostgreSQL RLS",
        "Supabase Auth",
        "HTTP upload",
        "KPI engine",
        "RECON-09",
    ):
        assert token in v2, token
    assert "RLS is implemented" not in status
    assert "RLS is implemented" not in readme
    assert "not a production identity provider" in readme
    assert "HS256 JWT is an authentication hook" in status or "HS256 JWT is" in status
