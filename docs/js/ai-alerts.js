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
const cameraSelect = $("cameraSelect");
const openCameraPickerBtn = $("openCameraPickerBtn");
const startCameraSnapshotsBtn = $("startCameraSnapshotsBtn");
const stopCameraSnapshotsBtn = $("stopCameraSnapshotsBtn");
const cameraSelectStatus = $("cameraSelectStatus");
const cameraPickerModal = $("cameraPickerModal");
const cameraPickerCloseBtn = $("cameraPickerCloseBtn");
const cameraPickerStatus = $("cameraPickerStatus");
const cameraPickerSections = $("cameraPickerSections");

const entryModal = $("entryModal");
const entryModalStatus = $("entryModalStatus");
const entryBody = $("entryBody");
const clearEntryBtn = $("clearEntryBtn");
const entryName = $("entryName");
const entryCamera = $("entryCamera");
const entryEvent = $("entryEvent");
const entryTime = $("entryTime");
const entryResolution = $("entryResolution");
const entryVlm = $("entryVlm");
const entryImage = $("entryImage");
const entryFallback = $("entryFallback");
const entrySnapshots = $("entrySnapshots");
const snapSelectButtons = [$("snapSelect0"), $("snapSelect1"), $("snapSelect2")];
let currentSnapshots = [];
let selectedSnapshotIndex = 0;
let lastEventKey = "";

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

function setCameraStatus(text, kind) {
  cameraSelectStatus.textContent = text;
  cameraSelectStatus.className = "inline-status" + (kind ? ` ${kind}` : "");
}

function setCameraPickerStatus(text, kind) {
  cameraPickerStatus.textContent = text;
  cameraPickerStatus.className = "inline-status" + (kind ? ` ${kind}` : "");
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
    entryFallback.textContent = selected.label || `${selectedSnapshotIndex + 1}`;
    entryResolution.textContent = "Resolution: unavailable";
  }
  snapSelectButtons.forEach((btn, idx) => {
    btn.classList.toggle("agentic-snap-btn-active", idx === selectedSnapshotIndex);
  });
}

function renderEntryEmpty() {
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
  entryVlm.textContent = "";
  resetSnapshotTiles();
}

function renderEntryEvent(data) {
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
  if (data.vlm_analysis) {
    entryVlm.textContent = `VLM: ${data.vlm_analysis}`;
  } else if (data.vlm_error) {
    entryVlm.textContent = `VLM error: ${data.vlm_error}`;
  } else if (data.matched_alert_name) {
    entryVlm.textContent = `VLM: no output for alert "${data.matched_alert_name}"`;
  } else {
    entryVlm.textContent = "VLM: no matching AI alert for this webhook name";
  }
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
    btn.disabled = !row;
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
      renderEntryEmpty();
      return;
    }
    renderEntryEvent(data);
  } catch (_) {
    entryModalStatus.textContent = "Webhook status unavailable";
    entryModalStatus.className = "warning-status err";
  }
}

async function clearWebhookEntry() {
  clearEntryBtn.disabled = true;
  try {
    await fetch(`${apiBase()}/api/webhook-entry-clear`, { method: "POST" });
    renderEntryEmpty();
  } catch (_) {
    entryModalStatus.textContent = "Could not clear event";
    entryModalStatus.className = "warning-status err";
    clearEntryBtn.disabled = false;
  }
}

async function loadCameras() {
  cameraSelect.innerHTML = "";
  setCameraStatus("Loading cameras...");
  try {
    const res = await fetch(`${apiBase()}/api/cameras`);
    const data = await res.json().catch(() => ({ ok: false, cameras: [] }));
    if (!data.ok) {
      setCameraStatus(data.error || "Could not load cameras.", "err");
      return;
    }
    const cams = Array.isArray(data.cameras) ? data.cameras : [];
    if (!cams.length) {
      setCameraStatus("No cameras available.", "err");
      return;
    }
    for (const cam of cams) {
      const opt = document.createElement("option");
      opt.value = cam.id;
      opt.textContent = cam.name ? `${cam.name} (${cam.id})` : cam.id;
      cameraSelect.appendChild(opt);
    }
    const monitored = Array.isArray(data.monitored) ? data.monitored : [];
    if (monitored.length && cams.some((c) => c.id === monitored[0])) {
      cameraSelect.value = monitored[0];
    }
    setCameraStatus("Select a camera and start snapshots.", "ok");
  } catch (e) {
    setCameraStatus(e.message || String(e), "err");
  }
}

function closeCameraPicker() {
  cameraPickerModal.classList.add("is-hidden");
  cameraPickerModal.hidden = true;
}

function openCameraPicker() {
  cameraPickerModal.hidden = false;
  cameraPickerModal.classList.remove("is-hidden");
  void loadCameraPicker();
}

function renderCameraPickerSections(sections) {
  cameraPickerSections.innerHTML = "";
  if (!sections.length) {
    const empty = document.createElement("div");
    empty.className = "profile-empty";
    empty.textContent = "No camera snapshots available.";
    cameraPickerSections.appendChild(empty);
    return;
  }
  for (const section of sections) {
    const wrap = document.createElement("section");
    wrap.className = "camera-picker-section";
    const title = document.createElement("h3");
    title.className = "camera-picker-section-title";
    title.textContent = section.title || `${section.site || "Site"} / ${section.group || "Group"}`;
    wrap.appendChild(title);
    const grid = document.createElement("div");
    grid.className = "camera-picker-grid";
    const cameras = Array.isArray(section.cameras) ? section.cameras : [];
    for (const cam of cameras) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "camera-picker-tile";
      const img = document.createElement("img");
      img.className = "camera-picker-img";
      img.alt = cam.name || cam.id || "Camera";
      img.src = cam.image_data_url || "";
      img.loading = "lazy";
      const label = document.createElement("span");
      label.className = "camera-picker-name";
      label.textContent = cam.name || cam.id || "Unknown camera";
      btn.appendChild(img);
      btn.appendChild(label);
      btn.addEventListener("click", () => {
        cameraSelect.value = cam.id || "";
        setCameraStatus(`Selected ${cam.name || cam.id}.`, "ok");
        closeCameraPicker();
      });
      grid.appendChild(btn);
    }
    wrap.appendChild(grid);
    cameraPickerSections.appendChild(wrap);
  }
}

async function loadCameraPicker() {
  cameraPickerSections.innerHTML = "";
  setCameraPickerStatus("Loading camera snapshots...");
  try {
    const res = await fetch(`${apiBase()}/api/camera-picker`);
    const data = await res.json().catch(() => ({ ok: false, sections: [] }));
    if (!data.ok) {
      setCameraPickerStatus(data.error || "Could not load camera picker.", "err");
      return;
    }
    const sections = Array.isArray(data.sections) ? data.sections : [];
    renderCameraPickerSections(sections);
    setCameraPickerStatus(`Loaded ${sections.length} section(s).`, "ok");
  } catch (e) {
    setCameraPickerStatus(e.message || String(e), "err");
  }
}

async function startCameraSnapshots() {
  const cameraId = String(cameraSelect.value || "").trim();
  if (!cameraId) {
    setCameraStatus("Pick a camera first.", "err");
    return;
  }
  startCameraSnapshotsBtn.disabled = true;
  setCameraStatus("Starting live snapshots...");
  try {
    const res = await fetch(`${apiBase()}/api/live-snapshot/select-camera`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ camera_id: cameraId }),
    });
    const data = await res.json().catch(() => ({ ok: false }));
    if (!data.ok) {
      setCameraStatus(data.error || "Could not start snapshots.", "err");
      return;
    }
    setCameraStatus(`Live snapshots active for ${cameraId}. Cache: ${data.cached_snapshots || 0}`, "ok");
  } catch (e) {
    setCameraStatus(e.message || String(e), "err");
  } finally {
    startCameraSnapshotsBtn.disabled = false;
  }
}

async function stopCameraSnapshots() {
  stopCameraSnapshotsBtn.disabled = true;
  setCameraStatus("Stopping live snapshots...");
  try {
    const res = await fetch(`${apiBase()}/api/live-snapshot/stop`, {
      method: "POST",
    });
    const data = await res.json().catch(() => ({ ok: false }));
    if (!data.ok) {
      setCameraStatus(data.error || "Could not stop snapshots.", "err");
      return;
    }
    setCameraStatus("Live snapshots stopped.", "ok");
  } catch (e) {
    setCameraStatus(e.message || String(e), "err");
  } finally {
    stopCameraSnapshotsBtn.disabled = false;
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
alertModal.addEventListener("click", (ev) => {
  if (ev.target === alertModal) {
    closeAlertModal();
  }
});
window.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !alertModal.hidden) {
    closeAlertModal();
  }
  if (ev.key === "Escape" && !cameraPickerModal.hidden) {
    closeCameraPicker();
  }
});
startCameraSnapshotsBtn.addEventListener("click", () => {
  void startCameraSnapshots();
});
stopCameraSnapshotsBtn.addEventListener("click", () => {
  void stopCameraSnapshots();
});
openCameraPickerBtn.addEventListener("click", openCameraPicker);
cameraPickerCloseBtn.addEventListener("click", closeCameraPicker);
cameraPickerModal.addEventListener("click", (ev) => {
  if (ev.target === cameraPickerModal) {
    closeCameraPicker();
  }
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

void loadAlerts();
void loadCameras();
renderEntryEmpty();
void loadWebhookEntry();
window.setInterval(() => {
  void loadWebhookEntry();
}, 3000);
