import { canAccessAdmin } from "./roles.js";
import { html, raw } from "./format.js";

function svg(markup) {
  return html`<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${raw(markup)}</svg>`;
}

export function icon(name) {
  const icons = {
    dashboard:
      '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    upload:
      '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 20h16"/>',
    runs: '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h10"/><circle cx="18.5" cy="18" r="2"/>',
    review: '<path d="M9 11l2 2 4-4"/><path d="M4 6h16v14H4z"/>',
    facts: '<path d="M4 19V5h11l5 5v9z"/><path d="M15 5v5h5"/>',
    logic: '<circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M8 7l3 9"/><path d="M16 7l-3 9"/>',
    labels: '<path d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path d="M12 4v16"/>',
    publications: '<path d="M12 3l8 4-8 4-8-4z"/><path d="M4 11l8 4 8-4"/><path d="M4 16l8 4 8-4"/>',
    downloads: '<path d="M12 4v12"/><path d="M7 11l5 5 5-5"/><path d="M4 20h16"/>',
    source: '<path d="M6 4h9l5 5v11H6z"/><path d="M15 4v5h5"/>',
    batches: '<rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/>',
    history: '<circle cx="12" cy="12" r="8"/><path d="M12 8v5l3 2"/>',
    client: '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M3 10h18"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  };
  return svg(icons[name] || icons.dashboard);
}

export function isNavCurrent(href, path) {
  if (path === href) return true;
  if (href === "/admin" || href === "/client") return false;
  return path.startsWith(`${href}/`);
}

function navItem(href, label, current, allowed, iconName) {
  if (!allowed) return "";
  const mark = isNavCurrent(href, current)
    ? html`<a class="item" href="${href}" aria-current="page">${icon(iconName)}<span>${label}</span></a>`
    : html`<a class="item" href="${href}">${icon(iconName)}<span>${label}</span></a>`;
  return html`<li>${mark}</li>`;
}

function pageTitleFor(path) {
  if (path === "/admin") return "Dashboard";
  if (path.startsWith("/admin/upload")) return "Upload Center";
  if (path.startsWith("/admin/source-files")) return "Source files";
  if (path.startsWith("/admin/batches")) return "Batches";
  if (path.startsWith("/admin/processing-runs")) return "Processing";
  if (path.startsWith("/admin/review")) return "Review";
  if (path.startsWith("/admin/facts")) return "Working-set facts";
  if (path.startsWith("/admin/history")) return "Fact history";
  if (path.startsWith("/admin/logic")) return "Logic";
  if (path.startsWith("/admin/labels")) return "Labels";
  if (path.startsWith("/admin/catalogs")) return "Logic & Labels";
  if (path.startsWith("/admin/publications")) return "Publications";
  if (path.startsWith("/admin/downloads")) return "Downloads";
  if (path === "/client") return "Published reporting";
  if (path.startsWith("/client/facts")) return "Published data";
  if (path === "/unauthorized") return "Not authorized";
  return "DFIP";
}

export function layout({ path, session, body }) {
  const role = session ? session.role : "";
  const admin = canAccessAdmin(role);
  const signedIn = Boolean(session);
  const area = path.startsWith("/admin") ? "Publisher" : "Reporting";
  const envLabel =
    session && session.auth_mode === "dev_token"
      ? "Development"
      : session && session.auth_mode
        ? "Signed in"
        : "";
  return html`
    <div class="shell">
      <button class="nav-backdrop" type="button" data-nav-close="true" aria-label="Close navigation"></button>
      <nav class="sidebar nav" aria-label="Application">
        <div class="brand-block">
          <div class="brand-mark">DF</div>
          <div>
            <p class="brand">DFIP</p>
            <p class="sub">${area}</p>
          </div>
        </div>
        ${
          admin
            ? html`
                <div class="nav-section">Operate</div>
                <ul>
                  ${navItem("/admin", "Dashboard", path, admin, "dashboard")}
                  ${navItem("/admin/upload", "Upload Center", path, admin, "upload")}
                  ${navItem("/admin/processing-runs", "Processing", path, admin, "runs")}
                  ${navItem("/admin/review", "Review", path, admin, "review")}
                  ${navItem("/admin/facts", "Facts", path, admin, "facts")}
                </ul>
                <div class="nav-section">Catalogs</div>
                <ul>
                  ${navItem("/admin/logic", "Logic", path, admin, "logic")}
                  ${navItem("/admin/labels", "Labels", path, admin, "labels")}
                </ul>
                <div class="nav-section">Publish</div>
                <ul>
                  ${navItem("/admin/publications", "Publications", path, admin, "publications")}
                  ${navItem("/admin/downloads", "Downloads", path, admin, "downloads")}
                </ul>
                <div class="nav-section">Lineage</div>
                <ul>
                  ${navItem("/admin/source-files", "Source files", path, admin, "source")}
                  ${navItem("/admin/batches", "Batches", path, admin, "batches")}
                  ${navItem("/admin/history", "History", path, admin, "history")}
                </ul>
              `
            : ""
        }
        ${
          signedIn
            ? html`
                <div class="nav-section">Reporting</div>
                <ul>
                  ${navItem("/client", "Overview", path, signedIn, "client")}
                  ${navItem("/client/facts", "Published data", path, signedIn, "facts")}
                </ul>
              `
            : ""
        }
        <div class="sidebar-foot">
          <p class="session-meta">
            Publish from Admin overview. UI route guards plus application-level
            API authorization. Not PostgreSQL RLS.
          </p>
        </div>
      </nav>
      <div class="workspace">
        <header class="topbar">
          <div class="topbar-title">
            <button class="menu-toggle" type="button" data-nav-toggle="true" aria-label="Open navigation">${icon("menu")}</button>
            <p class="topbar-kicker">${area}</p>
            <p class="topbar-heading">${pageTitleFor(path)}</p>
          </div>
          <div class="topbar-meta">
            ${envLabel ? html`<span class="env-pill">${envLabel}</span>` : ""}
            ${
              session
                ? html`<span class="role-pill">${session.subject} · ${session.role}</span>
                    <a class="btn-secondary" href="/sign-out" style="padding:0.38rem 0.7rem;border-radius:999px;display:inline-flex;align-items:center;">Sign out</a>`
                : html`<a href="/">Sign in</a>`
            }
          </div>
        </header>
        <main id="main" class="content">${body}</main>
      </div>
    </div>
  `;
}

export function pageHeader({ eyebrow, title, description, actions, crumbs }) {
  return html`
    <header class="page-header">
      ${
        crumbs && crumbs.length
          ? html`<nav class="crumbs" aria-label="Breadcrumb">${crumbs.map((part, index) => html`${index ? html`<span class="sep">/</span>` : ""}${part.href ? html`<a href="${part.href}">${part.label}</a>` : html`<span>${part.label}</span>`}`)}</nav>`
          : ""
      }
      <div class="page-header-row">
        <div>
          ${eyebrow ? html`<p class="eyebrow">${eyebrow}</p>` : ""}
          <h1>${title}</h1>
          ${description ? html`<p class="lede">${description}</p>` : ""}
        </div>
        ${actions ? html`<div class="page-actions">${actions}</div>` : ""}
      </div>
    </header>
  `;
}

export function metricCard({ label, value, hint, href, tone }) {
  const inner = html`
    <p class="metric-label">${label}</p>
    <p class="metric-value">${value}</p>
    ${hint ? html`<p class="metric-hint">${hint}</p>` : ""}
  `;
  const cls = `metric-card${tone ? ` ${tone}` : ""}`;
  if (href) return html`<a class="${cls}" href="${href}">${inner}</a>`;
  return html`<div class="${cls}">${inner}</div>`;
}

export function definitionList(entries) {
  const rows = entries.map(([label, value]) => html`<dt>${label}</dt><dd>${value}</dd>`);
  return html`<dl class="dl">${rows}</dl>`;
}

export function dataTable(caption, headers, rows) {
  if (!rows || !rows.length) {
    return html`<p class="muted">No rows in this page.</p>`;
  }
  const head = headers.map((header) => html`<th scope="col">${header}</th>`);
  const body = rows.map(
    (cells) => html`<tr>${cells.map((cell) => html`<td>${cell}</td>`)}</tr>`,
  );
  return html`
    <div class="table-wrap">
      <table>
        <caption class="sr-only">${caption}</caption>
        <thead><tr>${head}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;
}

export function lineageTrail(parts) {
  const nodes = [];
  parts.forEach((part, index) => {
    if (index) nodes.push(html`<span class="arrow" aria-hidden="true">→</span>`);
    if (part.href) {
      nodes.push(html`<a class="node" href="${part.href}">${part.label}</a>`);
    } else {
      nodes.push(html`<span class="node">${part.label}</span>`);
    }
  });
  return html`<div class="lineage" aria-label="Lineage">${nodes}</div>`;
}

export function workflowSteps(steps) {
  return html`
    <ol class="workflow">
      ${steps.map((step) => html`<li class="step ${step.state || ""}">${step.label}</li>`)}
    </ol>
  `;
}
