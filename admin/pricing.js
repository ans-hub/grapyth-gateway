import { requestAdminApi } from "./api.js";
import {
  cloneTemplate,
  createEmptyState,
  formatDate,
  formatMoney,
  showConfirmation,
  showLoadFailure,
  showLoadingState,
  showToast,
} from "./ui.js";

const pricingListElement = document.getElementById("pricing-list");
const pricingDetailElement = document.getElementById("pricing-detail");
const pricingSearchElement = document.getElementById("pricing-search");
const pricingDialog = document.getElementById("pricing-dialog");

const pricingState = {
  plans: [],
  selectedPlanId: "",
};

let reloadCurrentRoute;

function selectedPlan() {
  return pricingState.plans.find(
    (plan) => plan.id === pricingState.selectedPlanId,
  ) || null;
}

function renderPricingList() {
  const query = pricingSearchElement.value.trim().toLowerCase();
  const plans = pricingState.plans.filter((plan) => {
    const searchableText = `${plan.name} ${plan.model} ${plan.version}`;
    return searchableText.toLowerCase().includes(query);
  });

  if (!plans.length) {
    pricingListElement.replaceChildren(
      createEmptyState(
        "⌕",
        query ? "No matching plans" : "No pricing plans",
        query
          ? "Try a different search term"
          : "Create a versioned plan before assigning a client",
      ),
    );
    return;
  }

  const rows = document.createDocumentFragment();
  for (const plan of plans) {
    const rowFragment = cloneTemplate("entity-row-template");
    const row = rowFragment.querySelector(".entity-row");
    row.dataset.planId = plan.id;
    row.classList.toggle("active", plan.id === pricingState.selectedPlanId);

    rowFragment.querySelector("[data-entity-name]").textContent = plan.name;

    const status = rowFragment.querySelector("[data-entity-status]");
    status.textContent = plan.enabled ? "Active" : "Disabled";
    status.classList.toggle("success", plan.enabled);

    rowFragment.querySelector("[data-entity-primary]").textContent = plan.model;
    rowFragment.querySelector("[data-entity-secondary]").textContent =
      plan.version;
    rows.append(rowFragment);
  }

  pricingListElement.replaceChildren(rows);
}

function renderRateFacts(target, rates) {
  const rateDefinitions = [
    ["Input", "input"],
    ["Cached input", "cached"],
    ["Cache write", "cacheWrite"],
    ["Output", "output"],
  ];
  const facts = document.createDocumentFragment();

  for (const [label, key] of rateDefinitions) {
    const factFragment = cloneTemplate("rate-fact-template");
    factFragment.querySelector("small").textContent = label;
    factFragment.querySelector("strong").textContent = formatMoney(rates[key]);
    facts.append(factFragment);
  }

  target.replaceChildren(facts);
}

function renderPricingDetail() {
  const plan = selectedPlan();
  if (!plan) {
    const hasPlans = pricingState.plans.length > 0;
    let createButton = null;
    if (!hasPlans) {
      createButton = document.createElement("button");
      createButton.type = "button";
      createButton.className = "primary";
      createButton.dataset.createPlan = "";
      createButton.textContent = "New plan";
    }

    pricingDetailElement.replaceChildren(
      createEmptyState(
        "$",
        "Select a pricing plan",
        hasPlans
          ? "Choose a plan from the list"
          : "Create a versioned plan before assigning a client",
        createButton,
      ),
    );
    return;
  }

  const fragment = cloneTemplate("pricing-detail-template");
  fragment.querySelector("[data-plan-name]").textContent = plan.name;
  fragment.querySelector("[data-plan-identity]").textContent =
    `${plan.id} · ${plan.version}`;

  const status = fragment.querySelector("[data-plan-status]");
  status.textContent = plan.enabled ? "Active" : "Disabled";
  status.classList.toggle("success", plan.enabled);
  fragment
    .querySelector("[data-plan-below-cost]")
    .classList.toggle("hidden", !plan.belowCost);

  const editButton = fragment.querySelector("[data-edit-plan]");
  editButton.dataset.editPlan = plan.id;

  fragment.querySelector("[data-plan-model]").textContent = plan.model;
  fragment.querySelector("[data-plan-version]").textContent = plan.version;
  fragment.querySelector("[data-plan-created]").textContent = formatDate(
    plan.createdAt,
  );
  fragment.querySelector("[data-plan-updated]").textContent = formatDate(
    plan.updatedAt,
  );

  renderRateFacts(
    fragment.querySelector("[data-provider-rate-facts]"),
    plan.providerRates,
  );
  renderRateFacts(
    fragment.querySelector("[data-billed-rate-facts]"),
    plan.billedRates,
  );

  const belowCostSection = fragment.querySelector("[data-below-cost-section]");
  belowCostSection.classList.toggle("hidden", !plan.belowCost);
  belowCostSection.querySelector("p").textContent =
    plan.belowCostReason || "";

  fragment.querySelector("[data-plan-access-title]").textContent = plan.enabled
    ? "Disable pricing plan"
    : "Enable pricing plan";
  fragment.querySelector("[data-plan-access-message]").textContent = plan.enabled
    ? "Existing configuration remains visible, but the plan becomes unavailable for assignment"
    : "Make this plan available for assignment again";

  const toggleButton = fragment.querySelector("[data-toggle-plan]");
  toggleButton.dataset.togglePlan = plan.id;
  toggleButton.textContent = plan.enabled ? "Disable" : "Enable";
  toggleButton.classList.toggle("danger", plan.enabled);

  pricingDetailElement.replaceChildren(fragment);
}

function renderPricing() {
  renderPricingList();
  renderPricingDetail();
}

function ratesFromForm(prefix) {
  return {
    input: document.getElementById(`${prefix}-input-rate`).value,
    cached: document.getElementById(`${prefix}-cached-rate`).value,
    cacheWrite: document.getElementById(`${prefix}-cache-write-rate`).value,
    output: document.getElementById(`${prefix}-output-rate`).value,
  };
}

function syncBelowCostField() {
  const allowed = document.getElementById("allow-below").checked;
  document
    .getElementById("below-reason-field")
    .classList.toggle("hidden", !allowed);
  document.getElementById("below-reason").required = allowed;
}

export function showPricingLoading() {
  pricingListElement.replaceChildren();
  showLoadingState(pricingDetailElement, "pricing plans");
}

export function showPricingFailure(message) {
  showLoadFailure(pricingDetailElement, message);
}

export async function loadPricing(isCurrent) {
  const payload = await requestAdminApi("/admin/api/pricing-plans");
  if (!isCurrent()) {
    return;
  }

  pricingState.plans = payload.items;
  if (
    !pricingState.plans.some(
      (plan) => plan.id === pricingState.selectedPlanId,
    )
  ) {
    pricingState.selectedPlanId = pricingState.plans[0]?.id || "";
  }

  document.getElementById("pricing-count").textContent =
    pricingState.plans.length || "";
  renderPricing();
}

export function openPricingDialog(plan = null) {
  document.getElementById("pricing-form").reset();
  pricingDialog.dataset.planId = plan?.id || "";
  document.getElementById("pricing-dialog-title").textContent = plan
    ? "Edit pricing plan"
    : "New pricing plan";
  document.getElementById("pricing-submit").textContent = plan
    ? "Save changes"
    : "Create plan";

  const defaultRates = {
    input: "2.50",
    cached: "0.25",
    cacheWrite: "3.125",
    output: "15",
  };
  const source = plan || {
    providerRates: { ...defaultRates },
    billedRates: { ...defaultRates },
    allowBelowCost: false,
    belowCostReason: "",
  };

  if (plan) {
    document.getElementById("plan-name").value = plan.name;
    document.getElementById("plan-model").value = plan.model;
    document.getElementById("plan-version").value = plan.version;
  } else {
    document.getElementById("plan-model").value = "gpt-5.6-terra";
  }

  for (const [prefix, rates] of [
    ["provider", source.providerRates],
    ["billed", source.billedRates],
  ]) {
    document.getElementById(`${prefix}-input-rate`).value = rates.input;
    document.getElementById(`${prefix}-cached-rate`).value = rates.cached;
    document.getElementById(`${prefix}-cache-write-rate`).value =
      rates.cacheWrite;
    document.getElementById(`${prefix}-output-rate`).value = rates.output;
  }

  document.getElementById("allow-below").checked = source.allowBelowCost;
  document.getElementById("below-reason").value = source.belowCostReason;
  syncBelowCostField();
  pricingDialog.showModal();
}

export function initializePricing(handlers) {
  reloadCurrentRoute = handlers.reloadCurrentRoute;

  pricingSearchElement.addEventListener("input", renderPricingList);
  document
    .getElementById("allow-below")
    .addEventListener("change", syncBelowCostField);

  pricingListElement.addEventListener("click", (event) => {
    const row = event.target.closest("[data-plan-id]");
    if (!row) {
      return;
    }
    pricingState.selectedPlanId = row.dataset.planId;
    renderPricing();
  });

  pricingDetailElement.addEventListener("click", async (event) => {
    if (event.target.closest("[data-create-plan]")) {
      openPricingDialog();
      return;
    }

    const editButton = event.target.closest("[data-edit-plan]");
    const toggleButton = event.target.closest("[data-toggle-plan]");
    const planId =
      editButton?.dataset.editPlan || toggleButton?.dataset.togglePlan;

    if (!planId) {
      return;
    }

    const plan = pricingState.plans.find(
      (candidate) => candidate.id === planId,
    );
    if (editButton) {
      openPricingDialog(plan);
    }

    if (toggleButton) {
      const approved = await showConfirmation(
        `${plan.enabled ? "Disable" : "Enable"} pricing plan?`,
        plan.enabled
          ? "The plan will no longer be available for new client assignments"
          : "The plan will be available for assignment again",
        plan.enabled ? "Disable plan" : "Enable plan",
        plan.enabled,
      );
      if (!approved) {
        return;
      }

      try {
        await requestAdminApi(
          `/admin/api/pricing-plans/${encodeURIComponent(planId)}`,
          {
            method: "PATCH",
            body: { enabled: !plan.enabled },
          },
        );
        await reloadCurrentRoute(
          `Pricing plan ${plan.enabled ? "disabled" : "enabled"}`,
        );
      } catch (error) {
        showToast(error.message, true);
      }
    }
  });

  document.getElementById("pricing-form").addEventListener(
    "submit",
    async (event) => {
      event.preventDefault();
      const planId = pricingDialog.dataset.planId;
      const body = {
        name: document.getElementById("plan-name").value,
        model: document.getElementById("plan-model").value,
        version: document.getElementById("plan-version").value,
        providerRates: ratesFromForm("provider"),
        billedRates: ratesFromForm("billed"),
        allowBelowCost: document.getElementById("allow-below").checked,
        belowCostReason: document.getElementById("below-reason").value,
      };
      const path = planId
        ? `/admin/api/pricing-plans/${encodeURIComponent(planId)}`
        : "/admin/api/pricing-plans";

      try {
        const payload = await requestAdminApi(path, {
          method: planId ? "PATCH" : "POST",
          body,
        });
        pricingDialog.close();
        if (payload.item?.id) {
          pricingState.selectedPlanId = payload.item.id;
        }
        await reloadCurrentRoute(
          planId ? "Pricing plan saved" : "Pricing plan created",
        );
      } catch (error) {
        showToast(error.message, true);
      }
    },
  );
}
