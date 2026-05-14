const LS_API_BASE = "patientWatchlistApiBase";

const statusEl = document.getElementById("carTimingStatus");
const listEl = document.getElementById("carTimingList");
const clearBtn = document.getElementById("clearLogBtn");

function apiBase() {
  try {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("api");
    if (q && /^https?:\/\//i.test(q)) {
      return q.replace(/\/+$/, "");
    }
  } catch (_) {
    /* ignore */
  }
  try {
    const saved = localStorage.getItem(LS_API_BASE);
    if (saved && /^https?:\/\//i.test(saved)) {
      return saved.replace(/\/+$/, "");
    }
  } catch (_) {
    /* ignore */
  }

  const proto = window.location.protocol;
  const h = (window.location.hostname || "").toLowerCase();
  if (proto === "file:") return "http://127.0.0.1:8765";
  if (h === "localhost" || h === "127.0.0.1" || h === "[::1]") return "http://127.0.0.1:8765";
  if (h.endsWith("github.io") || proto === "https:") return "";
  if (proto === "http:" && h) return `http://${window.location.hostname}:8765`;
  return "";
}

function setStatus(msg, kind) {
  statusEl.textContent = msg;
  statusEl.className = "inline-status" + (kind ? ` ${kind}` : "");
}

function fmtLocalTime(value) {
  const dt = new Date(value || "");
  if (!Number.isFinite(dt.getTime())) return value || "-";
  return dt.toLocaleString();
}

function renderRows(rows) {
  const safeRows = Array.isArray(rows) ? rows : [];
  listEl.innerHTML = "";
  if (!safeRows.length) {
    const li = document.createElement("li");
    li.className = "car-timing-empty";
    li.textContent = "No completed entry/exit pair yet.";
    listEl.appendChild(li);
    return;
  }

  const newestFirst = [...safeRows].reverse();
  for (const row of newestFirst) {
    const li = document.createElement("li");
    li.className = "car-timing-row";
    const carId = String(row.car_id || "car");
    const entry = fmtLocalTime(row.entry_time || "");
    const exit = fmtLocalTime(row.exit_time || "");
    const elapsed = String(row.duration_label || `${Number(row.duration_seconds || 0).toFixed(3)}s`);
    li.textContent = `${carId} | Entry: ${entry} | Exit: ${exit} | Elapsed: ${elapsed}`;
    listEl.appendChild(li);
  }
}

async function loadLog() {
  try {
    const res = await fetch(`${apiBase()}/api/car-timing-log`);
    const data = await res.json().catch(() => ({ ok: false, error: "Invalid JSON from server." }));
    if (!data.ok) {
      throw new Error(data.error || "Could not load car timing log.");
    }
    renderRows(data.rows || []);
    const count = Array.isArray(data.rows) ? data.rows.length : 0;
    setStatus(`${count} completed pass${count === 1 ? "" : "es"}.`, "ok");
  } catch (err) {
    setStatus(err.message || String(err), "err");
  }
}

async function clearLog() {
  clearBtn.disabled = true;
  try {
    const res = await fetch(`${apiBase()}/api/car-timing-clear`, { method: "POST" });
    const data = await res.json().catch(() => ({ ok: false, error: "Invalid JSON from server." }));
    if (!data.ok) {
      throw new Error(data.error || "Failed to clear car timing log.");
    }
    await loadLog();
  } catch (err) {
    setStatus(err.message || String(err), "err");
  } finally {
    clearBtn.disabled = false;
  }
}

clearBtn.addEventListener("click", () => {
  void clearLog();
});

void loadLog();
window.setInterval(() => {
  void loadLog();
}, 2000);
