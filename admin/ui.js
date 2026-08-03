let toastTimer = 0;

const toastElement = document.getElementById("toast");
const confirmationDialog = document.getElementById("confirm-dialog");
const tokenDialog = document.getElementById("token-dialog");

export function formatMoney(value) {
  const number = Number(value || 0);
  return number.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  });
}

export function formatDate(value) {
  if (!value) {
    return "—";
  }

  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) {
    return String(value);
  }

  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function cloneTemplate(templateId) {
  const template = document.getElementById(templateId);
  if (!(template instanceof HTMLTemplateElement)) {
    throw new Error(`Missing HTML template: ${templateId}`);
  }
  return template.content.cloneNode(true);
}

export function createEmptyState(icon, title, message, action = null) {
  const fragment = cloneTemplate("empty-state-template");
  fragment.querySelector("[data-empty-icon]").textContent = icon;
  fragment.querySelector("[data-empty-title]").textContent = title;
  fragment.querySelector("[data-empty-message]").textContent = message;

  const actionContainer = fragment.querySelector("[data-empty-action]");
  if (action) {
    actionContainer.append(action);
  } else {
    actionContainer.remove();
  }

  return fragment;
}

export function showLoadingState(target, label) {
  target.replaceChildren(
    createEmptyState(
      "…",
      `Loading ${label}`,
      "Fetching the latest gateway state",
    ),
  );
}

export function showLoadFailure(target, message) {
  const retryButton = document.createElement("button");
  retryButton.type = "button";
  retryButton.dataset.retry = "";
  retryButton.textContent = "Try again";

  target.replaceChildren(
    createEmptyState(
      "!",
      "Could not load this section",
      message,
      retryButton,
    ),
  );
}

export function showToast(message, error = false) {
  toastElement.textContent = message;
  toastElement.classList.toggle("error", error);
  toastElement.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(
    () => toastElement.classList.remove("visible"),
    3200,
  );
}

export async function showConfirmation(
  title,
  message,
  actionLabel,
  danger = false,
) {
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-message").textContent = message;

  const actionButton = document.getElementById("confirm-action");
  actionButton.textContent = actionLabel;
  actionButton.classList.toggle("danger", danger);

  confirmationDialog.returnValue = "cancel";
  confirmationDialog.showModal();

  return new Promise((resolve) => {
    confirmationDialog.addEventListener(
      "close",
      () => resolve(confirmationDialog.returnValue === "confirm"),
      { once: true },
    );
  });
}

export function showToken(token) {
  document.getElementById("token-value").textContent = token;
  tokenDialog.showModal();
}

export function initializeSharedUi() {
  tokenDialog.addEventListener("close", () => {
    document.getElementById("token-value").textContent = "";
  });

  document.getElementById("copy-token").addEventListener("click", async () => {
    const tokenElement = document.getElementById("token-value");
    try {
      await navigator.clipboard.writeText(tokenElement.textContent);
      showToast("Token copied to clipboard");
    } catch {
      const selection = getSelection();
      const range = document.createRange();
      range.selectNodeContents(tokenElement);
      selection.removeAllRanges();
      selection.addRange(range);
      showToast("Token selected. Copy it with your keyboard.");
    }
  });
}
