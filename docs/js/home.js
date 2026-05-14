const APPS = [
  {
    id: "patient-elopement",
    label: "Patient elopement",
    description: "Tracks potential patient exits and alerts on elopement risk events.",
  },
  {
    id: "ai-alerts",
    label: "AI alerts",
    description: "Create and manage AI-powered rule alerts for selected cameras.",
  },
  {
    id: "alta-dashboard",
    label: "Alta dashboard",
    description: "View key Alta system health and camera overview information.",
  },
  {
    id: "car-timing",
    label: "Car timing",
    description: "Logs car entry and exit times with elapsed duration per pass.",
  },
  {
    id: "advanced-heatmaps",
    label: "Advanced heatmaps",
    description: "Shows high-activity areas using advanced heatmap visualizations.",
  },
  {
    id: "tailgating",
    label: "Tailgating",
    description: "Monitors and reviews possible tailgating security events.",
  },
];

const LS_APP_VISIBILITY = "securityOpsAppVisibility";

const settingsBtn = document.getElementById("homeSettingsBtn");
const settingsModal = document.getElementById("homeSettingsModal");
const settingsCloseBtn = document.getElementById("homeSettingsCloseBtn");
const settingsList = document.getElementById("homeSettingsList");
const appButtons = Array.from(document.querySelectorAll("[data-app-id]"));

function defaultVisibilityMap() {
  const out = {};
  for (const app of APPS) out[app.id] = true;
  return out;
}

function loadVisibilityMap() {
  const defaults = defaultVisibilityMap();
  try {
    const raw = localStorage.getItem(LS_APP_VISIBILITY);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return defaults;
    for (const app of APPS) {
      if (typeof parsed[app.id] === "boolean") {
        defaults[app.id] = parsed[app.id];
      }
    }
  } catch (_) {
    // ignore parse/storage errors; keep defaults
  }
  return defaults;
}

function saveVisibilityMap(map) {
  try {
    localStorage.setItem(LS_APP_VISIBILITY, JSON.stringify(map));
  } catch (_) {
    // ignore storage errors
  }
}

function applyVisibility(map) {
  for (const btn of appButtons) {
    const id = btn.getAttribute("data-app-id") || "";
    const isVisible = map[id] !== false;
    btn.hidden = !isVisible;
    btn.classList.toggle("is-hidden", !isVisible);
    btn.style.display = isVisible ? "inline-flex" : "none";
    btn.setAttribute("aria-hidden", isVisible ? "false" : "true");
  }
}

function renderSettings(map) {
  settingsList.innerHTML = "";
  for (const app of APPS) {
    const row = document.createElement("label");
    row.className = "home-settings-item";
    const description = String(app.description || "").trim();
    if (description) {
      row.title = description;
      row.setAttribute("aria-label", `${app.label}. ${description}`);
    }
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = map[app.id] !== false;
    input.addEventListener("change", () => {
      map[app.id] = Boolean(input.checked);
      saveVisibilityMap(map);
      applyVisibility(map);
    });
    const text = document.createElement("span");
    text.textContent = app.label;
    if (description) {
      text.title = description;
    }
    row.appendChild(input);
    row.appendChild(text);
    settingsList.appendChild(row);
  }
}

function openSettings() {
  settingsModal.hidden = false;
  settingsModal.classList.remove("is-hidden");
}

function closeSettings() {
  settingsModal.classList.add("is-hidden");
  settingsModal.hidden = true;
}

const visibilityMap = loadVisibilityMap();
applyVisibility(visibilityMap);
renderSettings(visibilityMap);

settingsBtn?.addEventListener("click", openSettings);
settingsCloseBtn?.addEventListener("click", closeSettings);
settingsModal?.addEventListener("click", (ev) => {
  if (ev.target === settingsModal) closeSettings();
});
window.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && settingsModal && !settingsModal.hidden) {
    closeSettings();
  }
});
