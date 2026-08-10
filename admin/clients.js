import { requestAdminApi } from "./api.js";
import { openProviderTest } from "./providers.js";
import {
  cloneTemplate,
  createEmptyState,
  formatDate,
  formatMoney,
  showConfirmation,
  showLoadFailure,
  showLoadingState,
  showToast,
  showToken,
} from "./ui.js";

const clientListElement = document.getElementById("client-list");
const clientDetailElement = document.getElementById("client-detail");
const clientSearchElement = document.getElementById("client-search");
const clientDialog = document.getElementById("client-dialog");
const creditDialog = document.getElementById("credit-dialog");

const clientState = {
  clients: [],
  providers: [],
  plans: [],
  selectedClientId: "",
  section: "overview",
  calls: null,
  ledger: null,
};

let navigateTo;
let reloadCurrentRoute;

function selectedClient() {
  return clientState.clients.find(
    (client) => client.id === clientState.selectedClientId,
  ) || null;
}

function selectedPlan(client) {
  return clientState.plans.find(
    (plan) => plan.id === client?.pricingPlanId,
  ) || null;
}

function selectedProvider(client) {
  return clientState.providers.find(
    (provider) => provider.id === client?.providerCredentialId,
  ) || null;
}

function appendProviderOptions(select, selectedId = "", includeBlank = false) {
  select.replaceChildren();

  if (includeBlank) {
    select.add(new Option("No provider credential", ""));
  }

  for (const provider of clientState.providers) {
    const suffix = provider.enabled ? "" : " — disabled";
    const option = new Option(
      `${provider.name}${suffix}`,
      provider.id,
      false,
      provider.id === selectedId,
    );
    option.disabled = !provider.enabled;
    select.add(option);
  }
}

function appendPlanOptions(select, selectedId = "") {
  select.replaceChildren();

  for (const plan of clientState.plans) {
    const suffix = plan.enabled ? "" : " — disabled";
    const option = new Option(
      `${plan.name} · ${plan.version}${suffix}`,
      plan.id,
      false,
      plan.id === selectedId,
    );
    option.disabled = !plan.enabled;
    select.add(option);
  }
}

function updateNavigationCounts() {
  document.getElementById("client-count").textContent =
    clientState.clients.length || "";
  document.getElementById("provider-count").textContent =
    clientState.providers.length || "";
  document.getElementById("pricing-count").textContent =
    clientState.plans.length || "";
}

function renderClientList() {
  const query = clientSearchElement.value.trim().toLowerCase();
  const clients = clientState.clients.filter((client) => {
    const searchableText = `${client.name} ${client.id} ${client.note}`;
    return searchableText.toLowerCase().includes(query);
  });

  if (!clients.length) {
    clientListElement.replaceChildren(
      createEmptyState(
        "⌕",
        query ? "No matching clients" : "No clients yet",
        query
          ? "Try a different search term"
          : "Create a client to issue an installation token",
      ),
    );
    return;
  }

  const rows = document.createDocumentFragment();
  for (const client of clients) {
    const rowFragment = cloneTemplate("entity-row-template");
    const row = rowFragment.querySelector(".entity-row");
    row.dataset.clientId = client.id;
    row.classList.toggle("active", client.id === clientState.selectedClientId);

    rowFragment.querySelector("[data-entity-name]").textContent = client.name;

    const status = rowFragment.querySelector("[data-entity-status]");
    status.textContent = client.enabled ? "Active" : "Disabled";
    status.classList.toggle("success", client.enabled);

    rowFragment.querySelector("[data-entity-primary]").textContent =
      client.model || "No model";
    rowFragment.querySelector("[data-entity-secondary]").textContent =
      client.billingMode === "prepaid"
        ? formatMoney(client.balanceUsd)
        : "Meter only";

    rows.append(rowFragment);
  }

  clientListElement.replaceChildren(rows);
}

function renderClientOverview(client, target) {
  const plan = selectedPlan(client);
  const provider = selectedProvider(client);
  const fragment = cloneTemplate("client-overview-template");

  fragment.querySelector("[data-client-balance]").textContent =
    client.billingMode === "prepaid"
      ? formatMoney(client.balanceUsd)
      : "Meter only";
  fragment.querySelector("[data-client-call-count]").textContent = Number(
    client.usage?.calls || 0,
  ).toLocaleString();
  fragment.querySelector("[data-client-billed]").textContent = formatMoney(
    client.usage?.billed_cost_usd,
  );
  fragment.querySelector("[data-client-margin]").textContent = formatMoney(
    client.usage?.margin_usd,
  );

  const form = fragment.querySelector("#client-policy-form");
  form.elements.name.value = client.name;
  form.elements.reasoningEffort.value = client.reasoningEffort;
  form.elements.billingMode.value = client.billingMode;
  form.elements.note.value = client.note || "";
  appendProviderOptions(
    form.elements.providerCredentialId,
    client.providerCredentialId,
    true,
  );
  appendPlanOptions(form.elements.pricingPlanId, client.pricingPlanId);

  const testButton = fragment.querySelector("[data-test-client-connection]");
  testButton.disabled = !(provider?.enabled && plan);

  fragment.querySelector("[data-client-access-title]").textContent =
    client.enabled ? "Disable client" : "Enable client";
  fragment.querySelector("[data-client-access-message]").textContent =
    client.enabled
      ? "All gateway requests using this token will be rejected"
      : "Allow this installation to call the gateway again";

  const toggleButton = fragment.querySelector("[data-toggle-client]");
  toggleButton.textContent = client.enabled ? "Disable" : "Enable";
  toggleButton.classList.toggle("danger", client.enabled);

  fragment.querySelector("[data-client-provider]").textContent =
    provider?.name || "Not assigned";
  fragment.querySelector("[data-client-plan]").textContent =
    plan?.name || "Not assigned";
  fragment.querySelector("[data-client-model]").textContent =
    plan?.model || client.model || "—";
  fragment.querySelector("[data-client-pricing-version]").textContent =
    plan?.version || "—";

  target.replaceChildren(fragment);
}

function renderCalls(target) {
  if (clientState.calls === null) {
    showLoadingState(target, "calls");
    return;
  }

  if (!clientState.calls.length) {
    target.replaceChildren(
      createEmptyState(
        "○",
        "No calls yet",
        "Successful and failed provider calls will appear here",
      ),
    );
    return;
  }

  const fragment = cloneTemplate("client-calls-template");
  const tableBody = fragment.querySelector("tbody");

  for (const call of clientState.calls) {
    const rowFragment = cloneTemplate("client-call-row-template");
    rowFragment.querySelector("[data-call-time]").textContent = formatDate(
      call.created_at,
    );

    const status = rowFragment.querySelector("[data-call-status]");
    status.textContent = call.status;
    status.classList.add(call.status === "ok" ? "success" : "danger");
    const error = rowFragment.querySelector("[data-call-error]");
    error.textContent = call.error_code;
    error.classList.toggle("hidden", !call.error_code);
    rowFragment
      .querySelector("[data-call-below-cost]")
      .classList.toggle("hidden", !call.below_cost);

    rowFragment.querySelector("[data-call-model]").textContent =
      call.resolved_model || call.requested_model || "—";
    rowFragment.querySelector("[data-call-reasoning]").textContent =
      call.reasoning_effort || "—";
    rowFragment.querySelector("[data-call-tokens]").textContent =
      `${Number(call.input_tokens || 0).toLocaleString()} / ` +
      Number(call.output_tokens || 0).toLocaleString();
    rowFragment.querySelector("[data-call-provider-cost]").textContent =
      call.provider_cost_usd == null
        ? "—"
        : formatMoney(call.provider_cost_usd);
    rowFragment.querySelector("[data-call-billed]").textContent =
      call.charged_usd == null ? "—" : formatMoney(call.charged_usd);
    rowFragment.querySelector("[data-call-margin]").textContent =
      call.margin_usd == null ? "—" : formatMoney(call.margin_usd);

    tableBody.append(rowFragment);
  }

  target.replaceChildren(fragment);
}

function renderLedger(target) {
  if (clientState.ledger === null) {
    showLoadingState(target, "credit ledger");
    return;
  }

  if (!clientState.ledger.length) {
    target.replaceChildren(
      createEmptyState(
        "$",
        "No ledger entries",
        "Credit adjustments and AI usage charges will appear here",
      ),
    );
    return;
  }

  const fragment = cloneTemplate("client-ledger-template");
  const tableBody = fragment.querySelector("tbody");

  for (const entry of clientState.ledger) {
    const rowFragment = cloneTemplate("client-ledger-row-template");
    rowFragment.querySelector("[data-ledger-time]").textContent = formatDate(
      entry.created_at,
    );
    rowFragment.querySelector("[data-ledger-kind]").textContent =
      entry.kind.replaceAll("_", " ");
    rowFragment.querySelector("[data-ledger-amount]").textContent = formatMoney(
      entry.amount_usd,
    );
    rowFragment.querySelector("[data-ledger-note]").textContent =
      entry.note || "—";
    rowFragment.querySelector("[data-ledger-call]").textContent =
      entry.call_id || "—";
    tableBody.append(rowFragment);
  }

  target.replaceChildren(fragment);
}

function renderClientSection(client, target) {
  if (clientState.section === "calls") {
    renderCalls(target);
    return;
  }
  if (clientState.section === "ledger") {
    renderLedger(target);
    return;
  }
  renderClientOverview(client, target);
}

function renderClientDetail() {
  const client = selectedClient();
  if (!client) {
    const hasClients = clientState.clients.length > 0;
    let createButton = null;
    if (!hasClients) {
      createButton = document.createElement("button");
      createButton.type = "button";
      createButton.className = "primary";
      createButton.dataset.createClient = "";
      createButton.textContent = "Create client";
    }

    clientDetailElement.replaceChildren(
      createEmptyState(
        "◇",
        "Select a client",
        hasClients
          ? "Choose an installation from the list"
          : "Create the first installation to configure access and billing",
        createButton,
      ),
    );
    return;
  }

  const fragment = cloneTemplate("client-detail-template");
  fragment.querySelector("[data-client-name]").textContent = client.name;

  const status = fragment.querySelector("[data-client-status]");
  status.textContent = client.enabled ? "Active" : "Disabled";
  status.classList.toggle("success", client.enabled);

  fragment.querySelector("[data-client-identity]").textContent =
    `${client.id} · token …${client.tokenHint}`;

  for (const tab of fragment.querySelectorAll("[data-client-section]")) {
    tab.classList.toggle(
      "active",
      tab.dataset.clientSection === clientState.section,
    );
  }

  renderClientSection(
    client,
    fragment.querySelector("#client-detail-content"),
  );
  clientDetailElement.replaceChildren(fragment);
}

function renderClients() {
  renderClientList();
  renderClientDetail();
}

async function loadClientActivity(section, isCurrent = () => true) {
  const client = selectedClient();
  if (!client || !["calls", "ledger"].includes(section)) {
    return;
  }

  const clientId = client.id;
  if (section === "calls") {
    clientState.calls = null;
  }
  if (section === "ledger") {
    clientState.ledger = null;
  }
  renderClientDetail();

  try {
    const payload = await requestAdminApi(
      `/admin/api/installations/${encodeURIComponent(clientId)}/${section}`,
    );
    if (
      !isCurrent() ||
      clientState.selectedClientId !== clientId ||
      clientState.section !== section
    ) {
      return;
    }

    if (section === "calls") {
      clientState.calls = payload.items;
    }
    if (section === "ledger") {
      clientState.ledger = payload.items;
    }
    renderClientDetail();
  } catch (error) {
    showToast(error.message, true);
    if (clientState.selectedClientId === clientId) {
      const content = document.getElementById("client-detail-content");
      content.replaceChildren(
        createEmptyState(
          "!",
          `Could not load ${section}`,
          error.message,
        ),
      );
    }
  }
}

export function showClientsLoading() {
  clientListElement.replaceChildren();
  showLoadingState(clientDetailElement, "clients");
}

export function showClientsFailure(message) {
  showLoadFailure(clientDetailElement, message);
}

export async function loadClients(isCurrent) {
  const [clients, providers, plans] = await Promise.all([
    requestAdminApi("/admin/api/installations"),
    requestAdminApi("/admin/api/provider-credentials"),
    requestAdminApi("/admin/api/pricing-plans"),
  ]);

  if (!isCurrent()) {
    return;
  }

  clientState.clients = clients.items;
  clientState.providers = providers.items;
  clientState.plans = plans.items;

  if (
    !clientState.clients.some(
      (client) => client.id === clientState.selectedClientId,
    )
  ) {
    clientState.selectedClientId = clientState.clients[0]?.id || "";
    clientState.section = "overview";
  }

  updateNavigationCounts();
  renderClients();

  if (clientState.section !== "overview") {
    await loadClientActivity(clientState.section, isCurrent);
  }
}

export function openClientDialog() {
  if (!clientState.providers.some((provider) => provider.enabled)) {
    showToast(
      "Add an active provider credential before creating a client",
      true,
    );
    navigateTo("providers");
    return;
  }

  if (!clientState.plans.some((plan) => plan.enabled)) {
    showToast("Create an active pricing plan before creating a client", true);
    navigateTo("pricing");
    return;
  }

  document.getElementById("client-form").reset();
  appendProviderOptions(document.getElementById("client-provider"));
  appendPlanOptions(document.getElementById("client-plan"));
  clientDialog.showModal();
}

function openCreditDialog(client) {
  document.getElementById("credit-form").reset();
  creditDialog.dataset.clientId = client.id;
  document.getElementById("credit-client-name").textContent = client.name;
  creditDialog.showModal();
}

export function initializeClients(handlers) {
  navigateTo = handlers.navigate;
  reloadCurrentRoute = handlers.reloadCurrentRoute;

  clientSearchElement.addEventListener("input", renderClientList);

  clientListElement.addEventListener("click", (event) => {
    const row = event.target.closest("[data-client-id]");
    if (!row) {
      return;
    }

    clientState.selectedClientId = row.dataset.clientId;
    clientState.section = "overview";
    clientState.calls = null;
    clientState.ledger = null;
    renderClients();
  });

  clientDetailElement.addEventListener("click", async (event) => {
    if (event.target.closest("[data-create-client]")) {
      openClientDialog();
      return;
    }

    const client = selectedClient();
    if (!client) {
      return;
    }

    const sectionButton = event.target.closest("[data-client-section]");
    if (sectionButton) {
      clientState.section = sectionButton.dataset.clientSection;
      if (clientState.section === "overview") {
        renderClientDetail();
      } else {
        await loadClientActivity(clientState.section);
      }
      return;
    }

    if (event.target.closest("[data-credit-client]")) {
      openCreditDialog(client);
    }

    if (event.target.closest("[data-test-client-connection]")) {
      const provider = selectedProvider(client);
      if (provider) {
        openProviderTest(provider, selectedPlan(client)?.model || client.model);
      }
    }

    if (event.target.closest("[data-rotate-client-token]")) {
      const approved = await showConfirmation(
        "Rotate client token?",
        `The current token for ${client.name} will stop working immediately`,
        "Rotate token",
        true,
      );
      if (!approved) {
        return;
      }

      try {
        const payload = await requestAdminApi(
          `/admin/api/installations/${encodeURIComponent(client.id)}/rotate-token`,
          { method: "POST" },
        );
        showToken(payload.token);
        await reloadCurrentRoute();
      } catch (error) {
        showToast(error.message, true);
      }
    }

    if (event.target.closest("[data-toggle-client]")) {
      const approved = await showConfirmation(
        `${client.enabled ? "Disable" : "Enable"} client?`,
        client.enabled
          ? "Gateway calls using this installation token will be rejected"
          : "This installation will regain gateway access",
        client.enabled ? "Disable client" : "Enable client",
        client.enabled,
      );
      if (!approved) {
        return;
      }

      try {
        await requestAdminApi(
          `/admin/api/installations/${encodeURIComponent(client.id)}`,
          {
            method: "PATCH",
            body: { enabled: !client.enabled },
          },
        );
        await reloadCurrentRoute(
          `Client ${client.enabled ? "disabled" : "enabled"}`,
        );
      } catch (error) {
        showToast(error.message, true);
      }
    }
  });

  clientDetailElement.addEventListener("submit", async (event) => {
    if (event.target.id !== "client-policy-form") {
      return;
    }

    event.preventDefault();
    const client = selectedClient();
    const formData = new FormData(event.target);

    try {
      await requestAdminApi(
        `/admin/api/installations/${encodeURIComponent(client.id)}`,
        {
          method: "PATCH",
          body: Object.fromEntries(formData.entries()),
        },
      );
      await reloadCurrentRoute("Client configuration saved");
    } catch (error) {
      showToast(error.message, true);
    }
  });

  document.getElementById("client-form").addEventListener(
    "submit",
    async (event) => {
      event.preventDefault();
      const body = {
        name: document.getElementById("client-name").value,
        note: document.getElementById("client-note").value,
        providerCredentialId:
          document.getElementById("client-provider").value,
        pricingPlanId: document.getElementById("client-plan").value,
        reasoningEffort: document.getElementById("client-reasoning").value,
        billingMode: document.getElementById("client-billing").value,
      };

      try {
        const payload = await requestAdminApi("/admin/api/installations", {
          method: "POST",
          body,
        });
        clientDialog.close();
        clientState.selectedClientId = payload.item.id;
        await reloadCurrentRoute();
        showToken(payload.token);
      } catch (error) {
        showToast(error.message, true);
      }
    },
  );

  document.getElementById("credit-form").addEventListener(
    "submit",
    async (event) => {
      event.preventDefault();
      try {
        await requestAdminApi(
          `/admin/api/installations/${encodeURIComponent(creditDialog.dataset.clientId)}/credit`,
          {
            method: "POST",
            body: {
              amountUsd: document.getElementById("credit-amount").value,
              note: document.getElementById("credit-note").value,
            },
          },
        );
        creditDialog.close();
        await reloadCurrentRoute("Credit adjusted");
      } catch (error) {
        showToast(error.message, true);
      }
    },
  );
}
