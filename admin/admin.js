import {
  initializeAudit,
  loadAudit,
  showAuditFailure,
  showAuditLoading,
} from "./audit.js";
import {
  initializeClients,
  loadClients,
  openClientDialog,
  showClientsFailure,
  showClientsLoading,
} from "./clients.js";
import {
  initializePricing,
  loadPricing,
  openPricingDialog,
  showPricingFailure,
  showPricingLoading,
} from "./pricing.js";
import {
  initializeProviders,
  loadProviders,
  openProviderDialog,
  showProvidersFailure,
  showProvidersLoading,
} from "./providers.js";
import {
  initializeSettings,
  loadSettings,
} from "./settings.js";
import { initializeSharedUi, showToast } from "./ui.js";

const viewTitleElement = document.getElementById("view-title");
const viewDescriptionElement = document.getElementById("view-description");
const viewActionElement = document.getElementById("view-action");
const pageStatusElement = document.getElementById("page-status");

const routes = {
  clients: {
    title: "Clients",
    description: "Installation access, policy and usage",
    actionLabel: "New client",
    action: openClientDialog,
    load: loadClients,
    showLoading: showClientsLoading,
    showFailure: showClientsFailure,
  },
  providers: {
    title: "Providers",
    description: "Encrypted credentials used for managed AI",
    actionLabel: "Add credential",
    action: openProviderDialog,
    load: loadProviders,
    showLoading: showProvidersLoading,
    showFailure: showProvidersFailure,
  },
  pricing: {
    title: "Pricing",
    description: "Versioned provider costs and client billing rates",
    actionLabel: "New plan",
    action: openPricingDialog,
    load: loadPricing,
    showLoading: showPricingLoading,
    showFailure: showPricingFailure,
  },
  gateway: {
    title: "Gateway",
    description: "Global request safety limits",
    actionLabel: "",
    action: null,
    load: loadSettings,
  },
  audit: {
    title: "Audit",
    description: "Recent administrative changes",
    actionLabel: "Refresh",
    action: () => reloadCurrentRoute("Audit refreshed"),
    load: loadAudit,
    showLoading: showAuditLoading,
    showFailure: showAuditFailure,
  },
};

let activeRouteName = "clients";
let currentLoadVersion = 0;

function showPageError(message = "") {
  pageStatusElement.textContent = message;
  pageStatusElement.classList.toggle("hidden", !message);
}

function navigate(routeName) {
  const destination = routes[routeName] ? routeName : "clients";
  if (location.hash.slice(1) === destination) {
    activateRoute(destination);
  } else {
    location.hash = destination;
  }
}

async function loadRoute(routeName) {
  const route = routes[routeName];
  const loadVersion = ++currentLoadVersion;
  const isCurrent = () =>
    loadVersion === currentLoadVersion && routeName === activeRouteName;

  route.showLoading?.();

  try {
    await route.load(isCurrent);
  } catch (error) {
    if (!isCurrent()) {
      return;
    }
    showPageError(error.message);
    route.showFailure?.(error.message);
  }
}

async function activateRoute(routeName) {
  activeRouteName = routes[routeName] ? routeName : "clients";
  const route = routes[activeRouteName];

  viewTitleElement.textContent = route.title;
  viewDescriptionElement.textContent = route.description;
  viewActionElement.textContent = route.actionLabel;
  viewActionElement.classList.toggle("hidden", !route.actionLabel);

  for (const button of document.querySelectorAll("[data-route]")) {
    const active = button.dataset.route === activeRouteName;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  }

  for (const pane of document.querySelectorAll("[data-pane]")) {
    pane.classList.toggle("hidden", pane.dataset.pane !== activeRouteName);
  }

  showPageError();
  await loadRoute(activeRouteName);
}

async function reloadCurrentRoute(message = "") {
  await loadRoute(activeRouteName);
  if (message) {
    showToast(message);
  }
}

function initializeNavigation() {
  for (const button of document.querySelectorAll("[data-route]")) {
    button.addEventListener("click", () => navigate(button.dataset.route));
  }

  window.addEventListener("hashchange", () => {
    activateRoute(location.hash.slice(1));
  });

  viewActionElement.addEventListener("click", () => {
    routes[activeRouteName].action?.();
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-dialog-close]")) {
      event.target.closest("dialog")?.close();
    }
    if (event.target.closest("[data-retry]")) {
      loadRoute(activeRouteName);
    }
  });
}

initializeSharedUi();
initializeNavigation();
initializeClients({ navigate, reloadCurrentRoute });
initializeProviders({ reloadCurrentRoute });
initializePricing({ reloadCurrentRoute });
initializeSettings();
initializeAudit();

navigate(location.hash.slice(1) || "clients");
