const $ = (id) => document.getElementById(id);

const LS_API_BASE = "patientWatchlistApiBase";
const entryStatus = $("entryStatus");
const refreshWebhookBtn = $("refreshWebhookBtn");
const entryModal = $("entryModal");
const entryModalStatus = $("entryModalStatus");
const entryBody = $("entryBody");
const clearEntryBtn = $("clearEntryBtn");
const entryName = $("entryName");
const entryCamera = $("entryCamera");
const entryEvent = $("entryEvent");
const entryTime = $("entryTime");
const entryResolution = $("entryResolution");
const entryImage = $("entryImage");
const entryFallback = $("entryFallback");
const entrySnapshots = $("entrySnapshots");
const snapSelectButtons = [$("snapSelect0"), $("snapSelect1"), $("snapSelect2")];
let currentSnapshots = [];
let selectedSnapshotIndex = 0;
let lastEventKey = "";

function apiBase() {
  try {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("api");
    if (q && /^https?:\/\//i.test(q)) return q.replace(/\/+$/, "");
  } catch (_) {}
  try {
    const saved = localStorage.getItem(LS_API_BASE);
    if (saved && /^https?:\/\//i.test(saved)) return saved.replace(/\/+$/, "");
  } catch (_) {}
  const proto = window.location.protocol;
  const h = (window.location.hostname || "").toLowerCase();
  if (proto === "file:" || h === "localhost" || h === "127.0.0.1" || h === "[::1]") return "http://127.0.0.1:8765";
  if (h.endsWith("github.io") || proto === "https:") return "";
  if (proto === "http:" && h && h !== "localhost" && h !== "127.0.0.1" && h !== "[::1]") {
    return `http://${window.location.hostname}:8765`;
  }
  return "";
}

function formatTime(isoValue) {
  if (!isoValue) return "";
  const d = new Date(isoValue);
  return Number.isNaN(d.getTime()) ? String(isoValue) : d.toLocaleString();
}

function resetSnapshotTiles() {
  entrySnapshots.classList.add("is-hidden");
  currentSnapshots = [];
  selectedSnapshotIndex = 0;
  lastEventKey = "";
  snapSelectButtons.forEach((btn, i) => {
    btn.disabled = true;
    btn.textContent = String(i + 1);
    btn.classList.remove("agentic-snap-btn-active");
  });
}

function renderSelectedSnapshot() {
  const selected = currentSnapshots[selectedSnapshotIndex] || {};
  const hasImage = Boolean(selected.image_data_url);
  if (hasImage) {
    entryImage.src = selected.image_data_url;
    entryImage.addEventListener(
      "load",
      () => {
        entryFallback.style.display = "none";
        entryResolution.textContent = `Resolution: ${entryImage.naturalWidth} x ${entryImage.naturalHeight}`;
      },
      { once: true }
    );
    entryImage.addEventListener(
      "error",
      () => {
        entryImage.removeAttribute("src");
        entryFallback.style.display = "flex";
        entryResolution.textContent = "Resolution: unavailable";
      },
      { once: true }
    );
  } else {
    entryImage.removeAttribute("src");
    entryFallback.style.display = "flex";
    entryResolution.textContent = "Resolution: unavailable";
  }
  snapSelectButtons.forEach((btn, idx) => {
    btn.classList.toggle("agentic-snap-btn-active", idx === selectedSnapshotIndex);
  });
}

function renderEmpty() {
  entryStatus.textContent = "Waiting for webhook...";
  entryStatus.className = "inline-status";
  entryModalStatus.textContent = "Waiting for webhook...";
  entryModalStatus.className = "warning-status";
  entryModal.classList.add("is-hidden");
  entryModal.hidden = true;
  entryBody.classList.add("is-hidden");
  clearEntryBtn.disabled = true;
  entryImage.removeAttribute("src");
  entryFallback.style.display = "flex";
  entryFallback.textContent = "?";
  entryResolution.textContent = "";
  resetSnapshotTiles();
}

function renderEvent(data) {
  entryStatus.textContent = "Event received";
  entryStatus.className = "inline-status ok";
  entryModalStatus.textContent = "Event received";
  entryModalStatus.className = "warning-status warn";
  entryModal.classList.remove("is-hidden");
  entryModal.hidden = false;
  entryBody.classList.remove("is-hidden");
  clearEntryBtn.disabled = false;

  const ruleName = String(data.patient_name || data.event || "Unknown rule");
  const camera = String(data.camera || data.location || "Unknown camera");
  entryName.textContent = ruleName;
  entryCamera.textContent = `Camera: ${camera}`;
  entryEvent.textContent = `Rule: ${data.event || "Camera rule triggered"}`;
  entryTime.textContent = data.trigger_time ? `Trigger: ${formatTime(data.trigger_time)}` : "";
  entryFallback.textContent = ruleName.slice(0, 2).toUpperCase() || "?";
  const eventKey = `${data.camera || data.location || ""}|${data.trigger_time || ""}|${data.received_at || ""}`;
  const isSameEvent = lastEventKey === eventKey;

  const snapshots = Array.isArray(data.snapshots) ? data.snapshots.slice(0, 3) : [];
  if (!snapshots.length) {
    currentSnapshots = [{ image_data_url: data.image_data_url || "", label: "1" }];
    if (!isSameEvent) selectedSnapshotIndex = 0;
    renderSelectedSnapshot();
    entrySnapshots.classList.add("is-hidden");
    lastEventKey = eventKey;
    return;
  }
  entrySnapshots.classList.remove("is-hidden");
  currentSnapshots = snapshots;
  if (!isSameEvent) selectedSnapshotIndex = 0;
  if (!currentSnapshots[selectedSnapshotIndex]) selectedSnapshotIndex = 0;
  snapSelectButtons.forEach((btn, i) => {
    const row = snapshots[i] || {};
    const label = row.label || `${i + 1}`;
    btn.textContent = `${i + 1}: ${label}`;
    btn.disabled = !row.image_data_url;
  });
  if (snapSelectButtons[selectedSnapshotIndex]?.disabled) {
    const firstEnabled = snapSelectButtons.findIndex((b) => !b.disabled);
    selectedSnapshotIndex = firstEnabled >= 0 ? firstEnabled : 0;
  }
  renderSelectedSnapshot();
  lastEventKey = eventKey;
}

async function loadWebhookEntry() {
  try {
    const res = await fetch(`${apiBase()}/api/webhook-entry-latest`);
    const data = await res.json().catch(() => ({ ok: false, has_warning: false }));
    if (!data || !data.has_warning) {
      renderEmpty();
      return;
    }
    renderEvent(data);
  } catch (_) {
    entryStatus.textContent = "Webhook status unavailable";
    entryStatus.className = "inline-status err";
  }
}

async function clearWebhookEntry() {
  clearEntryBtn.disabled = true;
  try {
    await fetch(`${apiBase()}/api/webhook-entry-clear`, { method: "POST" });
    renderEmpty();
  } catch (_) {
    entryModalStatus.textContent = "Could not clear event";
    entryModalStatus.className = "warning-status err";
    clearEntryBtn.disabled = false;
  }
}

refreshWebhookBtn.addEventListener("click", () => {
  void loadWebhookEntry();
});
clearEntryBtn.addEventListener("click", () => {
  void clearWebhookEntry();
});
snapSelectButtons.forEach((btn, i) => {
  btn.addEventListener("click", () => {
    selectedSnapshotIndex = i;
    renderSelectedSnapshot();
  });
});

renderEmpty();
void loadWebhookEntry();
window.setInterval(() => {
  void loadWebhookEntry();
}, 3000);
