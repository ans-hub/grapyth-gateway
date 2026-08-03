import { requestAdminApi } from "./api.js";
import {
  cloneTemplate,
  createEmptyState,
  formatDate,
  showLoadFailure,
  showLoadingState,
} from "./ui.js";

const auditContentElement = document.getElementById("audit-content");
const auditSearchElement = document.getElementById("audit-search");

let auditEvents = [];

function renderAudit() {
  const query = auditSearchElement.value.trim().toLowerCase();
  const events = auditEvents.filter((event) => {
    const searchableText = [
      event.action,
      event.target_type,
      event.target_id,
      JSON.stringify(event.details),
    ].join(" ");
    return searchableText.toLowerCase().includes(query);
  });

  document.getElementById("audit-summary").textContent =
    `${events.length} of ${auditEvents.length} events`;

  if (!events.length) {
    auditContentElement.replaceChildren(
      createEmptyState(
        "⌕",
        query ? "No matching events" : "No audit events",
        query
          ? "Try a different filter"
          : "Administrative changes will appear here",
      ),
    );
    return;
  }

  const fragment = cloneTemplate("audit-table-template");
  const tableBody = fragment.querySelector("tbody");

  for (const event of events) {
    const rowFragment = cloneTemplate("audit-row-template");
    rowFragment.querySelector("[data-audit-time]").textContent = formatDate(
      event.created_at,
    );
    rowFragment.querySelector("[data-audit-action]").textContent = event.action;
    rowFragment.querySelector("[data-audit-target-type]").textContent =
      event.target_type;
    rowFragment.querySelector("[data-audit-target-id]").textContent =
      event.target_id;
    rowFragment.querySelector("[data-audit-details]").textContent =
      JSON.stringify(event.details, null, 2);
    tableBody.append(rowFragment);
  }

  auditContentElement.replaceChildren(fragment);
}

export function showAuditLoading() {
  showLoadingState(auditContentElement, "audit events");
}

export function showAuditFailure(message) {
  showLoadFailure(auditContentElement, message);
}

export async function loadAudit(isCurrent) {
  const payload = await requestAdminApi("/admin/api/audit");
  if (!isCurrent()) {
    return;
  }
  auditEvents = payload.items;
  renderAudit();
}

export function initializeAudit() {
  auditSearchElement.addEventListener("input", renderAudit);
}
