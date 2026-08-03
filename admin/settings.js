import { requestAdminApi } from "./api.js";
import { showToast } from "./ui.js";

const settingsForm = document.getElementById("gateway-settings-form");

let settings = {};

function renderSettings() {
  document.getElementById("max-output-tokens").value =
    settings.maxOutputTokens;
  document.getElementById("max-request-mib").value = (
    Number(settings.maxRequestBytes) / 1048576
  ).toFixed(3);
}

export async function loadSettings(isCurrent) {
  const payload = await requestAdminApi("/admin/api/settings");
  if (!isCurrent()) {
    return;
  }
  settings = payload.item;
  renderSettings();
}

export function initializeSettings() {
  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    try {
      const payload = await requestAdminApi("/admin/api/settings", {
        method: "PATCH",
        body: {
          maxOutputTokens: Number(
            document.getElementById("max-output-tokens").value,
          ),
          maxRequestBytes: Math.round(
            Number(document.getElementById("max-request-mib").value) *
              1048576,
          ),
        },
      });
      settings = payload.item;
      renderSettings();
      showToast("Gateway limits saved");
    } catch (error) {
      showToast(error.message, true);
    }
  });
}
