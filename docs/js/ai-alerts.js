const $ = (id) => document.getElementById(id);

const alertsList = $("alertsList");
const alertsStatus = $("alertsStatus");
const addAlertBtn = $("addAlertBtn");
const alertModal = $("alertModal");
const closeAlertModalBtn = $("closeAlertModalBtn");
const alertNameInput = $("alertNameInput");
const alertPromptInput = $("alertPromptInput");
const saveAlertBtn = $("saveAlertBtn");
const alertFormStatus = $("alertFormStatus");
const responsesList = $("responsesList");
const responsesStatus = $("responsesStatus");
const clearResponsesBtn = $("clearResponsesBtn");

const LS_API_BASE = "patientWatchlistApiBase";

function apiBase() {
  try {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("api");
    if (q && /^https?:\/\//i.test(q)) {
      return q.replace(/\/+$/, "");
    }
  } catch (_) {
    // ignore
  }
  try {
    const saved = localStorage.getItem(LS_API_BASE);
    if (saved && /^https?:\/\//i.test(saved)) {
      return saved.replace(/\/+$/, "");
    }
  } catch (_) {
    // ignore
  }
  const proto = window.location.protocol;
  const h = (window.location.hostname || "").toLowerCase();
  if (proto === "file:" || h === "localhost" || h === "127.0.0.1" || h === "[::1]") {
    return "http://127.0.0.1:8765";
  }
  if (h.endsWith("github.io") || proto === "https:") {
    return "";
  }
  if (proto === "http:" && h && h !== "localhost" && h !== "127.0.0.1" && h !== "[::1]") {
    return `http://${window.location.hostname}:8765`;
  }
  return "";
}

function setFormStatus(text, kind) {
  alertFormStatus.textContent = text;
  alertFormStatus.className = "inline-status" + (kind ? ` ${kind}` : "");
}

function setListStatus(text, kind) {
  alertsStatus.textContent = text;
  alertsStatus.className = "list-foot" + (kind ? ` ${kind}` : "");
}

function setResponsesStatus(text, kind) {
  responsesStatus.textContent = text;
  responsesStatus.className = "list-foot" + (kind ? ` ${kind}` : "");
}

function formatTime(isoValue) {
  if (!isoValue) return "";
  const d = new Date(isoValue);
  return Number.isNaN(d.getTime()) ? String(isoValue) : d.toLocaleString();
}

function openAlertModal() {
  alertModal.hidden = false;
  alertModal.classList.remove("is-hidden");
  setFormStatus("");
  window.setTimeout(() => alertNameInput.focus(), 0);
}

function closeAlertModal() {
  alertModal.classList.add("is-hidden");
  alertModal.hidden = true;
  alertNameInput.value = "";
  alertPromptInput.value = "";
  setFormStatus("");
}

function renderAlerts(rows) {
  alertsList.innerHTML = "";
  if (!rows.length) {
    const li = document.createElement("li");
    li.className = "profile-empty";
    li.textContent = "No AI alerts configured yet.";
    alertsList.appendChild(li);
    return;
  }
  for (const row of rows) {
    const li = document.createElement("li");
    li.className = "ai-alert-row";

    const nameEl = document.createElement("div");
    nameEl.className = "profile-name";
    nameEl.textContent = row.name || "Untitled alert";

    const whenEl = document.createElement("div");
    whenEl.className = "warning-time";
    whenEl.textContent = row.created_at ? `Created: ${new Date(row.created_at).toLocaleString()}` : "";

    const actionsEl = document.createElement("div");
    actionsEl.className = "ai-alerts-actions";
    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "btn btn-ghost";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", () => {
      void deleteAlert(String(row.id || ""), String(row.name || "this alert"), deleteBtn);
    });
    actionsEl.appendChild(deleteBtn);

    li.appendChild(nameEl);
    li.appendChild(whenEl);
    li.appendChild(actionsEl);
    alertsList.appendChild(li);
  }
}

function renderResponses(rows) {
  responsesList.innerHTML = "";
  if (!rows.length) {
    const li = document.createElement("li");
    li.className = "profile-empty";
    li.textContent = "No webhook AI responses yet.";
    responsesList.appendChild(li);
    return;
  }
  for (const row of rows) {
    const li = document.createElement("li");
    li.className = "ai-response-row";

    const thumbWrap = document.createElement("div");
    thumbWrap.className = "ai-response-thumb-wrap";
    const fallback = document.createElement("span");
    fallback.className = "profile-thumb-fallback";
    const ruleName = String(row.rule_name || "Rule");
    fallback.textContent = (ruleName.slice(0, 2) || "?").toUpperCase();
    thumbWrap.appendChild(fallback);
    if (row.thumbnail_data_url) {
      const img = document.createElement("img");
      img.className = "profile-thumb-img";
      img.alt = `${ruleName} snapshot`;
      img.src = String(row.thumbnail_data_url);
      img.addEventListener("load", () => {
        fallback.style.display = "none";
      });
      img.addEventListener("error", () => {
        fallback.style.display = "flex";
      });
      thumbWrap.appendChild(img);
    }

    const meta = document.createElement("div");
    meta.className = "ai-response-meta";
    const nameEl = document.createElement("div");
    nameEl.className = "profile-name";
    nameEl.textContent = ruleName;
    const aiEl = document.createElement("div");
    aiEl.className = "ai-response-text";
    aiEl.textContent = String(row.ai_response || "No AI response.");
    const whenEl = document.createElement("div");
    whenEl.className = "warning-time";
    whenEl.textContent = row.received_at ? `Received: ${formatTime(row.received_at)}` : "";
    meta.appendChild(nameEl);
    meta.appendChild(aiEl);
    meta.appendChild(whenEl);

    li.appendChild(thumbWrap);
    li.appendChild(meta);
    responsesList.appendChild(li);
  }
}

async function deleteAlert(alertId, alertName, buttonEl) {
  if (!alertId) {
    setListStatus("Missing alert id.", "err");
    return;
  }
  if (!window.confirm(`Delete AI alert "${alertName}"?`)) {
    return;
  }
  if (buttonEl) buttonEl.disabled = true;
  setListStatus("Deleting alert...");
  try {
    const res = await fetch(`${apiBase()}/api/ai-alerts/${encodeURIComponent(alertId)}`, {
      method: "DELETE",
    });
    const data = await res.json().catch(() => ({ ok: false, error: "Invalid JSON from server" }));
    if (!data.ok) {
      setListStatus(data.error || "Could not delete alert.", "err");
      return;
    }
    await loadAlerts();
  } catch (e) {
    setListStatus(e.message || String(e), "err");
  } finally {
    if (buttonEl) buttonEl.disabled = false;
  }
}

async function loadAlerts() {
  setListStatus("Loading alerts...");
  try {
    const res = await fetch(`${apiBase()}/api/ai-alerts`);
    const data = await res.json().catch(() => ({ ok: false, alerts: [] }));
    if (!data.ok) {
      setListStatus(data.error || "Could not load alerts.", "err");
      return;
    }
    const rows = Array.isArray(data.alerts) ? data.alerts : [];
    renderAlerts(rows);
    setListStatus(`${rows.length} alert(s)`, "ok");
  } catch (e) {
    setListStatus(e.message || String(e), "err");
  }
}

async function loadResponseLog() {
  setResponsesStatus("Loading responses...");
  try {
    const res = await fetch(`${apiBase()}/api/webhook-entry-log`, { cache: "no-store" });
    const data = await res.json().catch(() => ({ ok: false, rows: [] }));
    if (!data.ok) {
      setResponsesStatus(data.error || "Could not load responses.", "err");
      return;
    }
    const rows = Array.isArray(data.rows) ? data.rows : [];
    renderResponses(rows);
    setResponsesStatus(`${rows.length} response(s)`, "ok");
  } catch (e) {
    setResponsesStatus(e.message || String(e), "err");
  }
}

async function clearResponseLog() {
  clearResponsesBtn.disabled = true;
  setResponsesStatus("Clearing responses...");
  try {
    const res = await fetch(`${apiBase()}/api/webhook-entry-clear`, { method: "POST" });
    const data = await res.json().catch(() => ({ ok: false, error: "Invalid JSON from server" }));
    if (!res.ok) {
      setResponsesStatus(data.error || `Clear failed (HTTP ${res.status})`, "err");
      return;
    }
    if (data && data.ok === false) {
      setResponsesStatus(data.error || "Could not clear responses.", "err");
      return;
    }
    await loadResponseLog();
  } catch (e) {
    setResponsesStatus(e.message || String(e), "err");
  } finally {
    clearResponsesBtn.disabled = false;
  }
}

async function createAlert() {
  const name = alertNameInput.value.trim();
  const prompt = alertPromptInput.value.trim();
  if (!name) {
    setFormStatus("Name is required.", "err");
    return;
  }
  if (!prompt) {
    setFormStatus("Prompt is required.", "err");
    return;
  }
  saveAlertBtn.disabled = true;
  setFormStatus("Saving...");
  try {
    const res = await fetch(`${apiBase()}/api/ai-alerts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, prompt }),
    });
    const data = await res.json().catch(() => ({ ok: false, error: "Invalid JSON from server" }));
    if (!data.ok) {
      setFormStatus(data.error || "Could not save alert.", "err");
      return;
    }
    closeAlertModal();
    await loadAlerts();
  } catch (e) {
    setFormStatus(e.message || String(e), "err");
  } finally {
    saveAlertBtn.disabled = false;
  }
}

addAlertBtn.addEventListener("click", openAlertModal);
closeAlertModalBtn.addEventListener("click", closeAlertModal);
saveAlertBtn.addEventListener("click", () => {
  void createAlert();
});
clearResponsesBtn.addEventListener("click", () => {
  void clearResponseLog();
});
alertModal.addEventListener("click", (ev) => {
  if (ev.target === alertModal) {
    closeAlertModal();
  }
});
window.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !alertModal.hidden) {
    closeAlertModal();
  }
});

void loadAlerts();
void loadResponseLog();
window.setInterval(() => {
  void loadResponseLog();
}, 3000);
