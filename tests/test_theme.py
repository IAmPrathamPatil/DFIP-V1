"""Theme system source contracts: dark default, light overlay, persistence, charts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web" / "static"


def _css() -> str:
    return (WEB / "css" / "app.css").read_text(encoding="utf-8")


def _js(name: str) -> str:
    return (WEB / "js" / name).read_text(encoding="utf-8")


def test_theme_tokens_are_centralized_and_not_duplicated_as_a_second_sheet() -> None:
    css = _css()
    assert css.count(":root {") == 1
    assert 'html[data-theme="light"]' in css
    for token in (
        "--bg-primary:",
        "--bg-secondary:",
        "--surface:",
        "--surface-elevated:",
        "--border:",
        "--text-primary:",
        "--text-secondary:",
        "--muted:",
        "--accent:",
        "--success:",
        "--warning:",
        "--danger:",
        "--overlay:",
        "--topbar-bg:",
        "--sidebar-end:",
        "--chart-1:",
        "--table-hover:",
        "--scrollbar-thumb:",
    ):
        assert token in css
    assert css.index("--bg-0: #0b0f14") < css.index('html[data-theme="light"]')
    light = css[css.index('html[data-theme="light"]') :]
    assert "--bg-0: #f3f5f8" in light
    assert ".sidebar" in css
    assert ".topbar" in css
    assert ".metric-card" in css
    assert ".empty-state" in css
    assert ".modal" in css
    assert ".explorer-table" in css
    assert "var(--bg-2)" in css
    assert "var(--bg-3)" in css
    assert ".theme-switch" in css


def test_theme_boot_defaults_dark_and_persists_dfip_theme() -> None:
    index = (WEB / "index.html").read_text(encoding="utf-8")
    theme = _js("theme.js")
    components = _js("components.js")
    app = _js("app.js")
    assert 'localStorage.getItem("dfip.theme")' in index
    assert index.index("localStorage.getItem") < index.index('href="/css/app.css"')
    assert 'setAttribute("data-theme", "light")' in index
    assert "colorScheme = \"dark\"" in index
    assert 'const THEME_KEY = "dfip.theme"' in theme
    assert "THEME_DARK" in theme
    assert 'localStorage.getItem(THEME_KEY) === THEME_LIGHT' in theme
    assert "removeAttribute" in theme
    assert 'data-theme-set="dark"' in components
    assert 'data-theme-set="light"' in components
    assert "themeSwitcher()" in components
    assert 'from "./theme.js"' in app
    views = _js("views.js")
    assert "themeSwitcher()" in views
    assert "standalone" in views
    assert 'closest("[data-theme-set]")' in app
    assert "bootTheme()" in app
    assert "applyTheme(" in app


def test_charts_and_explorer_follow_theme_tokens() -> None:
    chart = _js("trend-chart.js")
    css = _css()
    assert "var(--chart-1)" in chart
    assert "#4c8dff" not in chart
    assert "stroke: var(--border)" in css
    assert "fill: var(--muted)" in css
    assert ".trend-tooltip" in css
    assert "var(--tooltip-shadow)" in css
    assert ".explorer-table thead th" in css
    assert ".explorer-sticky-rank" in css
    assert ".explorer-sticky-name" in css
    assert "scrollbar-color: var(--scrollbar-thumb)" in css
    explorer = css[css.index(".explorer-table-wrap") :]
    assert "background: var(--bg-2)" in explorer
    assert "background: var(--bg-3)" in explorer
