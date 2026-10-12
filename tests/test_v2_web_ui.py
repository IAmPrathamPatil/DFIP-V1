"""Professional website UI source contracts.

These tests lock the V2 website information architecture onto the existing
P5/P7 API client. They do not start a browser and do not invent backend routes.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_JS = ROOT / "apps" / "web" / "static" / "js"
WEB_STATIC = ROOT / "apps" / "web" / "static"
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
    assert "Upload Selected Files" in views
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
    assert "Reports" in views
    assert "export function clientOverviewView" in views
    assert 'data-upload-form="true"' in views
    start = views.index("export function clientHomeView")
    end = views.index("export function clientFactListView")
    client_home = views[start:end]
    assert "data-upload-form" not in client_home
    assert "data-publish-form" not in client_home
    assert "data-download-published" in client_home


def test_sidebar_collapsible_layout() -> None:
    """Test that the sidebar has collapsible layout with proper CSS and JS."""
    css = WEB_CSS.read_text(encoding="utf-8")
    app_js = _read("app.js")
    components = _read("components.js")

    # CSS variables for sidebar width and collapsed width
    assert "--sidebar-width:" in css
    assert "--sidebar-collapsed-width:" in css

    # Sidebar collapsed styles
    assert ".sidebar.collapsed" in css
    assert ".shell.sidebar-collapsed" in css
    assert ".sidebar.collapsed .brand-block" in css
    assert ".sidebar.collapsed .nav-section" in css
    assert ".sidebar.collapsed .nav ul" in css

    # Menu toggle button styles (single unified toggle)
    assert ".menu-toggle" in css
    assert ".shell.sidebar-collapsed .menu-toggle svg" in css

    # Desktop collapse transition
    assert "transition: width 200ms ease" in css
    assert "padding 200ms ease" in css

    # Menu toggle rotation on collapse
    assert "transform: rotate(180deg)" in css

    # JS: sidebar toggle handler (unified with nav toggle)
    assert "data-nav-toggle" in app_js
    assert "sidebar-collapsed" in app_js
    assert "dfip.sidebarCollapsed" in app_js
    assert "localStorage.setItem" in app_js

    # Components: single toggle button in layout
    assert 'data-nav-toggle="true"' in components
    assert 'data-sidebar-toggle="true"' in components
    assert 'aria-controls="sidebar-nav"' in components
    assert 'id="sidebar-nav"' in components


def test_sidebar_responsive_behavior() -> None:
    """Test responsive sidebar behavior for mobile and desktop."""
    css = WEB_CSS.read_text(encoding="utf-8")

    # Mobile drawer (max-width: 1024px)
    assert "@media (max-width: 1024px)" in css
    assert "transform: translateX(-105%)" in css

    # Mobile open state
    assert ".shell.nav-open .sidebar" in css
    assert "transform: none" in css

    # Single unified toggle button visible on both desktop and mobile
    assert ".menu-toggle" in css

    # Reduced motion support
    assert "prefers-reduced-motion: reduce" in css
    assert "transition: none !important" in css


def test_sidebar_persistence() -> None:
    """Test sidebar collapse persistence via localStorage."""
    app_js = _read("app.js")

    # localStorage key
    assert "dfip.sidebarCollapsed" in app_js

    # Restore on boot
    assert "localStorage.getItem" in app_js
    assert "sidebar-collapsed" in app_js
    assert "aria-expanded" in app_js
    assert "aria-label" in app_js
    assert "Expand sidebar" in app_js
    assert "Collapse sidebar" in app_js

    # Toggle handler (unified nav/sidebar toggle)
    assert "data-nav-toggle" in app_js
    assert "classList.toggle" in app_js
    assert "localStorage.setItem" in app_js


def test_studio_progressive_loading_contract() -> None:
    """Test that Analytics Studio renders progressively."""
    app_js = _read("app.js")
    views = _read("views.js")
    css = WEB_CSS.read_text(encoding="utf-8")

    # Loading shell function exists
    assert "loadingStudioShell" in views
    assert "data-studio-shell" in views

    # Shell renders before analytics requests finish
    assert "studio-nav-start" in app_js
    assert "studio-kpis-ready" in app_js
    assert "studio-shell-ready" in app_js
    assert "studio-complete" in app_js

    # Studio refresh sequence and shell mounted check
    assert "studioRefreshSeq" in app_js
    assert "studioShellMounted" in app_js

    # Secondary sections update independently
    assert "setStudioHost" in app_js

    # KPIs render first (primary data)
    assert "getOverviewKpis" in app_js

    # Two distinct Trends requests are preserved (secondary=revenue_inr, breakdown=channel)
    assert "secondary: \"revenue_inr\"" in app_js
    assert "breakdown: \"channel\"" in app_js

    # Each section has independent loading/error handling
    for zone in ("Trend", "Distribution", "StackedBar", "ShareStacked", "Combo", "Detail", "Narrative"):
        assert f"studio{zone}Zone" in views

    # Loading states use studioZoneLoading
    assert "studioZoneLoading" in views

    # Filter bar in shell
    assert "data-studio-filter-bar" in views
    assert "data-studio-kpis-host" in views


def test_studio_stale_response_protection() -> None:
    """Test that stale Studio responses cannot overwrite active view."""
    app_js = _read("app.js")

    # stillCurrent guard used in loadStudioProgressive
    assert "stillCurrent" in app_js

    # Guard applied before updating each section
    studio_refresh = app_js[
        app_js.index("async function loadStudioProgressive(") : app_js.index("async function renderRoute(")
    ]
    assert studio_refresh.count("stillCurrent()") >= 6  # Multiple section updates

    # Auth errors still propagate
    assert "error.status === 401" in studio_refresh
    assert "isInactiveCompanyError" in studio_refresh


def test_studio_host_uniqueness() -> None:
    """Test that Studio host selectors are unique within loadingStudioShell."""
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    shell = views[views.index("export function loadingStudioShell") : views.index("function overviewHostOrRetry")]

    hosts = (
        "data-studio-context",
        "data-studio-filter-bar",
        "data-studio-kpis-host",
        "data-studio-stacked-zone",
        "data-studio-share-zone",
        "data-studio-trend-zone",
        "data-studio-mix-zone",
        "data-studio-combo-zone",
        "data-studio-detail-zone",
        "data-studio-narrative-zone",
    )
    for host in hosts:
        assert shell.count(f'{host}="true"') == 1


def test_credential_view_setup_required() -> None:
    """Test that credentialView renders setup form when setupRequired is true."""
    views = _read("views.js")
    cred_section = views[views.index("export function credentialView") : views.index("export function companySelectView")]
    assert "setupRequired" in cred_section
    assert "Create the publisher account" in cred_section
    assert "data-publisher-setup-form" in cred_section
    assert "data-publisher-setup-username" in cred_section
    assert "data-publisher-setup-password" in cred_section
    assert "data-publisher-setup-confirm" in cred_section


def test_credential_view_setup_completed_with_setup_requested() -> None:
    """Test that credentialView shows setup-completed message when setupRequested is true but setupRequired is false."""
    views = _read("views.js")
    cred_section = views[views.index("export function credentialView") : views.index("export function companySelectView")]
    assert "setupRequested" in cred_section
    assert "Publisher setup has already been completed" in cred_section
    assert "data-publisher-setup-already-done" in cred_section
    assert "Please sign in with your existing account" in cred_section


def test_credential_view_sign_in_form() -> None:
    """Test that credentialView renders sign-in form when setup is not required."""
    views = _read("views.js")
    cred_section = views[views.index("export function credentialView") : views.index("export function companySelectView")]
    # Sign-in form elements
    assert "data-credential-form" in cred_section
    assert "Sign in" in cred_section
    # Create publisher account link
    assert "Create publisher account" in cred_section
    assert 'href="/?setup=true"' in cred_section
    assert "data-navigate" in cred_section


def test_app_js_credential_route_checks_setup_status() -> None:
    """Test that the credential route always checks setupStatus first."""
    app_js = _read("app.js")
    credential_section = app_js[app_js.index('if (name === "credential")') : app_js.index('if (name === "unauthorized")')]
    # Should always call setupStatus
    assert "api.setupStatus()" in credential_section
    # Should check publisher_setup_required
    assert "publisher_setup_required" in credential_section
    # Should handle wantsSetup query param
    assert "query.get(\"setup\")" in credential_section
    # Should pass setupRequested to credentialView
    assert "setupRequested" in credential_section


def test_setup_link_points_to_correct_url() -> None:
    """Test that the Create publisher account link points to /?setup=true."""
    views = _read("views.js")
    cred_section = views[views.index("export function credentialView") : views.index("export function companySelectView")]
    # The link should have the setup=true query param
    assert 'href="/?setup=true"' in cred_section
    # The link should use data-navigate for SPA navigation
    assert 'data-navigate' in cred_section
