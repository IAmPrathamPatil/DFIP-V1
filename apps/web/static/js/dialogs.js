import { html, toHtml } from "./format.js";

export function showToast({ tone = "info", title, message }) {
  let host = document.getElementById("dfip-toasts");
  if (!host) {
    host = document.createElement("div");
    host.id = "dfip-toasts";
    host.className = "toast-host";
    host.setAttribute("aria-live", "polite");
    document.body.appendChild(host);
  }
  const item = document.createElement("div");
  item.className = `toast toast-${tone}`;
  item.setAttribute("role", "status");
  item.innerHTML = toHtml(
    html`<strong>${title}</strong>${message ? html`<p>${message}</p>` : ""}`,
  );
  host.appendChild(item);
  window.setTimeout(() => {
    item.remove();
  }, 4200);
}

export function confirmAction({ title, message, confirmLabel = "Confirm", tone = "primary" }) {
  return new Promise((resolve) => {
    const root = document.createElement("div");
    root.className = "modal-root";
    const buttonClass = tone === "danger" ? "danger" : "";
    root.innerHTML = toHtml(html`
      <div class="modal-backdrop" data-confirm-cancel="true"></div>
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <h2 id="confirm-title">${title}</h2>
        <p class="muted">${message}</p>
        <div class="modal-actions">
          <button type="button" class="secondary" data-confirm-cancel="true">Cancel</button>
          <button type="button" class="${buttonClass}" data-confirm-ok="true">${confirmLabel}</button>
        </div>
      </div>
    `);
    const onKey = (event) => {
      if (event.key === "Escape") finish(false);
    };
    const finish = (value) => {
      document.removeEventListener("keydown", onKey);
      root.remove();
      resolve(value);
    };
    root.addEventListener("click", (event) => {
      if (event.target.closest("[data-confirm-ok]")) finish(true);
      else if (event.target.closest("[data-confirm-cancel]")) finish(false);
    });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(root);
    const confirmButton = root.querySelector("[data-confirm-ok]");
    if (confirmButton) confirmButton.focus();
  });
}
