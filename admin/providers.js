import { requestAdminApi } from "./api.js";
import {
  cloneTemplate,
  createEmptyState,
  formatDate,
  showConfirmation,
  showLoadFailure,
  showLoadingState,
  showToast,
} from "./ui.js";

const providerListElement = document.getElementById("provider-list");
const providerDetailElement = document.getElementById("provider-detail");
const providerSearchElement = document.getElementById("provider-search");
const providerDialog = document.getElementById("provider-dialog");
const providerTestDialog = document.getElementById("provider-test-dialog");

const providerState = {
  providers: [],
  plans: [],
  selectedProviderId: "",
};

let reloadCurrentRoute;

function selectedProvider() {
  return providerState.providers.find(
    (provider) => provider.id === providerState.selectedProviderId,
  ) || null;
}

function updateNavigationCounts() {
  document.getElementById("provider-count").textContent =
    providerState.providers.length || "";
  document.getElementById("pricing-count").textContent =
    providerState.plans.length || "";
}

function renderProviderList() {
  const query = providerSearchElement.value.trim().toLowerCase();
  const providers = providerState.providers.filter((provider) => {
    const searchableText =
      `${provider.name} ${provider.id} ${provider.keyHint}`;
    return searchableText.toLowerCase().includes(query);
  });

  if (!providers.length) {
    providerListElement.replaceChildren(
      createEmptyState(
        "⌕",
        query ? "No matching providers" : "No providers yet",
        query
          ? "Try a different search term"
          : "Add an encrypted credential for managed AI",
      ),
    );
    return;
  }

  const rows = document.createDocumentFragment();
  for (const provider of providers) {
    const rowFragment = cloneTemplate("entity-row-template");
    const row = rowFragment.querySelector(".entity-row");
    row.dataset.providerId = provider.id;
    row.classList.toggle(
      "active",
      provider.id === providerState.selectedProviderId,
    );

    rowFragment.querySelector("[data-entity-name]").textContent = provider.name;

    const status = rowFragment.querySelector("[data-entity-status]");
    status.textContent = provider.enabled ? "Active" : "Disabled";
    status.classList.toggle("success", provider.enabled);

    rowFragment.querySelector("[data-entity-primary]").textContent =
      `••••${provider.keyHint}`;
    rowFragment.querySelector("[data-entity-secondary]").textContent =
      formatDate(provider.updatedAt);
    rows.append(rowFragment);
  }

  providerListElement.replaceChildren(rows);
}

function renderProviderDetail() {
  const provider = selectedProvider();
  if (!provider) {
    const hasProviders = providerState.providers.length > 0;
    let createButton = null;
    if (!hasProviders) {
      createButton = document.createElement("button");
      createButton.type = "button";
      createButton.className = "primary";
      createButton.dataset.createProvider = "";
      createButton.textContent = "Add credential";
    }

    providerDetailElement.replaceChildren(
      createEmptyState(
        "◇",
        "Select a provider",
        hasProviders
          ? "Choose a credential from the list"
          : "Add an encrypted OpenAI key before creating production clients",
        createButton,
      ),
    );
    return;
  }

  const fragment = cloneTemplate("provider-detail-template");
  fragment.querySelector("[data-provider-name]").textContent = provider.name;
  fragment.querySelector("[data-provider-id]").textContent = provider.id;

  const status = fragment.querySelector("[data-provider-status]");
  status.textContent = provider.enabled ? "Active" : "Disabled";
  status.classList.toggle("success", provider.enabled);

  const testButton = fragment.querySelector("[data-test-provider]");
  testButton.dataset.testProvider = provider.id;
  testButton.disabled = !provider.enabled;

  const rotateButton = fragment.querySelector("[data-rotate-provider]");
  rotateButton.dataset.rotateProvider = provider.id;

  fragment.querySelector("[data-provider-key-hint]").textContent =
    `••••${provider.keyHint}`;
  fragment.querySelector("[data-provider-detail-status]").textContent =
    provider.enabled ? "Active" : "Disabled";
  fragment.querySelector("[data-provider-created]").textContent = formatDate(
    provider.createdAt,
  );
  fragment.querySelector("[data-provider-updated]").textContent = formatDate(
    provider.updatedAt,
  );

  fragment.querySelector("[data-provider-access-title]").textContent =
    provider.enabled ? "Disable credential" : "Enable credential";
  fragment.querySelector("[data-provider-access-message]").textContent =
    provider.enabled
      ? "Assigned clients will no longer reach the provider"
      : "Assigned clients can use this credential again";

  const toggleButton = fragment.querySelector("[data-toggle-provider]");
  toggleButton.dataset.toggleProvider = provider.id;
  toggleButton.textContent = provider.enabled ? "Disable" : "Enable";
  toggleButton.classList.toggle("danger", provider.enabled);

  providerDetailElement.replaceChildren(fragment);
}

function renderProviders() {
  renderProviderList();
  renderProviderDetail();
}

export function showProvidersLoading() {
  providerListElement.replaceChildren();
  showLoadingState(providerDetailElement, "providers");
}

export function showProvidersFailure(message) {
  showLoadFailure(providerDetailElement, message);
}

export async function loadProviders(isCurrent) {
  const [providers, plans] = await Promise.all([
    requestAdminApi("/admin/api/provider-credentials"),
    requestAdminApi("/admin/api/pricing-plans"),
  ]);

  if (!isCurrent()) {
    return;
  }

  providerState.providers = providers.items;
  providerState.plans = plans.items;

  if (
    !providerState.providers.some(
      (provider) => provider.id === providerState.selectedProviderId,
    )
  ) {
    providerState.selectedProviderId = providerState.providers[0]?.id || "";
  }

  updateNavigationCounts();
  renderProviders();
}

export function openProviderDialog(provider = null) {
  document.getElementById("provider-form").reset();
  providerDialog.dataset.credentialId = provider?.id || "";

  const rotating = Boolean(provider);
  document.getElementById("provider-dialog-title").textContent = rotating
    ? "Rotate provider key"
    : "Add credential";
  document.getElementById("provider-dialog-description").textContent = rotating
    ? `Replace the encrypted key for ${provider.name}`
    : "The key is encrypted before it is stored";

  const nameField = document.getElementById("provider-name-field");
  nameField.classList.toggle("hidden", rotating);
  document.getElementById("provider-name").required = !rotating;
  document.getElementById("provider-submit").textContent = rotating
    ? "Rotate key"
    : "Save credential";
  providerDialog.showModal();
}

export function openProviderTest(provider, model = "") {
  providerTestDialog.dataset.credentialId = provider.id;
  document.getElementById("provider-test-name").textContent = provider.name;
  document.getElementById("provider-test-model").value =
    model ||
    providerState.plans.find((plan) => plan.enabled)?.model ||
    "gpt-5.6-terra";

  const result = document.getElementById("provider-test-result");
  result.className = "inline-result hidden";
  result.textContent = "";
  providerTestDialog.showModal();
}

export function initializeProviders(handlers) {
  reloadCurrentRoute = handlers.reloadCurrentRoute;

  providerSearchElement.addEventListener("input", renderProviderList);

  providerListElement.addEventListener("click", (event) => {
    const row = event.target.closest("[data-provider-id]");
    if (!row) {
      return;
    }
    providerState.selectedProviderId = row.dataset.providerId;
    renderProviders();
  });

  providerDetailElement.addEventListener("click", async (event) => {
    if (event.target.closest("[data-create-provider]")) {
      openProviderDialog();
      return;
    }

    const testButton = event.target.closest("[data-test-provider]");
    const rotateButton = event.target.closest("[data-rotate-provider]");
    const toggleButton = event.target.closest("[data-toggle-provider]");
    const providerId =
      testButton?.dataset.testProvider ||
      rotateButton?.dataset.rotateProvider ||
      toggleButton?.dataset.toggleProvider;

    if (!providerId) {
      return;
    }

    const provider = providerState.providers.find(
      (candidate) => candidate.id === providerId,
    );

    if (testButton) {
      openProviderTest(provider);
    }
    if (rotateButton) {
      openProviderDialog(provider);
    }
    if (toggleButton) {
      const approved = await showConfirmation(
        `${provider.enabled ? "Disable" : "Enable"} credential?`,
        provider.enabled
          ? "Clients assigned to this credential will not be able to reach the provider"
          : "Assigned clients can use this credential again",
        provider.enabled ? "Disable credential" : "Enable credential",
        provider.enabled,
      );
      if (!approved) {
        return;
      }

      try {
        await requestAdminApi(
          `/admin/api/provider-credentials/${encodeURIComponent(providerId)}`,
          {
            method: "PATCH",
            body: { enabled: !provider.enabled },
          },
        );
        await reloadCurrentRoute(
          `Credential ${provider.enabled ? "disabled" : "enabled"}`,
        );
      } catch (error) {
        showToast(error.message, true);
      }
    }
  });

  document.getElementById("provider-form").addEventListener(
    "submit",
    async (event) => {
      event.preventDefault();
      const providerId = providerDialog.dataset.credentialId;
      const path = providerId
        ? `/admin/api/provider-credentials/${encodeURIComponent(providerId)}`
        : "/admin/api/provider-credentials";
      const body = providerId
        ? { apiKey: document.getElementById("provider-key").value }
        : {
            name: document.getElementById("provider-name").value,
            apiKey: document.getElementById("provider-key").value,
          };

      try {
        const payload = await requestAdminApi(path, {
          method: providerId ? "PATCH" : "POST",
          body,
        });
        providerDialog.close();
        if (payload.item?.id) {
          providerState.selectedProviderId = payload.item.id;
        }
        await reloadCurrentRoute(
          providerId
            ? "Provider key rotated"
            : "Provider credential added",
        );
      } catch (error) {
        showToast(error.message, true);
      }
    },
  );

  document.getElementById("provider-test-form").addEventListener(
    "submit",
    async (event) => {
      event.preventDefault();
      const result = document.getElementById("provider-test-result");
      result.className = "inline-result";
      result.textContent = "Checking connection…";

      try {
        const payload = await requestAdminApi(
          `/admin/api/provider-credentials/${encodeURIComponent(providerTestDialog.dataset.credentialId)}/test`,
          {
            method: "POST",
            body: {
              model: document.getElementById("provider-test-model").value,
            },
          },
        );
        const test = payload.item;
        result.classList.add(test.ok ? "success" : "error");
        result.textContent = test.ok
          ? `Connected · ${test.resolvedModel} · ${test.latencyMs} ms`
          : `Failed · ${test.message} (${test.errorCode})`;
      } catch (error) {
        result.classList.add("error");
        result.textContent = error.message;
      }
    },
  );
}
