"""Professional website UI source contracts.

These tests lock the V2 website information architecture onto the existing
P5/P7 API client. They do not start a browser and do not invent backend routes.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_JS = ROOT / "apps" / "web" / "static" / "js"
WEB_CSS = ROOT / "apps" / "web" / "static" / "css" / "app.css"


def _read(name: str) -> str:
    return (WEB_JS / name).read_text(encoding="utf-8")


def test_website_shell_is_dark_and_has_sidebar() -> None:
    css = WEB_CSS.read_text(encoding="utf-8")
    assert "--bg-0:" in css
    assert "--accent:" in css
    assert ".sidebar" in css
    assert ".topbar" in css
    assert ".metric-card" in css
    assert ".empty-state" in css
    assert ".modal" in css
    components = _read("components.js")
    assert "Upload Center" in components
    assert "Publications" in components
    assert "Downloads" in components
    assert 'if (!allowed) return "";' in components
    assert "navItem(" in components
    assert 'class="item disabled"' not in components


def test_website_routes_cover_demo_workflow() -> None:
    app_js = _read("app.js")
    views = _read("views.js")
    for name in (
        "admin-upload",
        "admin-review",
        "admin-logic",
        "admin-labels",
        "admin-publications",
        "admin-downloads",
    ):
        assert name in app_js
    assert 'access: "admin"' in app_js
    assert 'access: "client"' in app_js
    assert "Upload New Dataset" in views
    assert "Upload Raw Data" in views
    assert "Upload Logic" in views
    assert "Upload Labels" in views
    assert "Activate Version" in views
    assert "Deactivate Version" in views
    assert "Publish Run" in views
    assert "Download CSV" in views
    assert "Working set" in views
    assert "Published data" in views
    assert "No QA findings were recorded for this run." in views
    assert "QA has not been recorded for this run." in views
    assert 'data-qa-verdict="null"' in views
    assert "QA passed" not in views
    assert "qaAllowsPublish" in views
    assert "Publication required" in views
    assert "does not publish" in views.lower()


def test_website_uses_qa_findings_endpoint_without_fabricating_verdict() -> None:
    client_js = _read("api-client.js")
    app_js = _read("app.js")
    views = _read("views.js")
    assert "listQaFindings" in client_js
    assert "/qa-findings" in client_js
    assert "listQaFindings" in app_js
    assert "qa_verdict" in views
    assert "QA has not been recorded for this run." in views
    assert 'data-qa-verdict="null"' in views
    assert "No stored QA verdict for this run." not in views
    admin_facts = app_js[
        app_js.index('if (name === "admin-facts")') : app_js.index(
            'if (name === "admin-fact-detail")'
        )
    ]
    assert "listFacts" in admin_facts
    assert "listPublishedFacts" not in admin_facts
    client_facts = app_js[
        app_js.index('if (name === "client-facts")') : app_js.index(
            'if (name === "client-fact-detail")'
        )
    ]
    assert "listPublishedFacts" in client_facts
    assert "listFacts" not in client_facts


def test_website_confirms_consequential_actions() -> None:
    app_js = _read("app.js")
    dialogs = _read("dialogs.js")
    assert "confirmAction" in app_js
    assert "Activate this version?" in app_js
    assert "Publish this run?" in app_js
    assert "showToast" in app_js
    assert 'role="dialog"' in dialogs
    assert "clearToken" in app_js
    assert "sessionStorage" in _read("auth.js")


def test_website_client_surface_is_reporting_not_pipeline() -> None:
    components = _read("components.js")
    views = _read("views.js")
    operate = components.index("Operate")
    reporting = components.index(">Reporting</div>", operate)
    admin_block = components[operate:reporting]
    assert "Upload Center" in admin_block
    assert "canAccessAdmin" in components
    assert "Published reporting" in views
    assert 'data-upload-form="true"' in views
    start = views.index("export function clientHomeView")
    end = views.index("export function clientFactListView")
    client_home = views[start:end]
    assert "data-upload-form" not in client_home
    assert "data-publish-form" not in client_home
    assert "data-download-published" in client_home
