const $ = (id) => document.getElementById(id);

const dashboardStatus = $("dashboardStatus");
const dashboardSummaryGrid = $("dashboardSummaryGrid");
const dashboardDevices = $("dashboardDevices");
const dashboardWatchlists = $("dashboardWatchlists");
const dashboardErrors = $("dashboardErrors");
const dashboardRefreshBtn = $("dashboardRefreshBtn");

const LS_API_BASE = "patientWatchlistApiBase";

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
  if (proto === "http:" && h) return `http://${window.location.hostname}:8765`;
  return "";
}

function setStatus(text, kind) {
  dashboardStatus.textContent = text;
  dashboardStatus.className = "inline-status" + (kind ? ` ${kind}` : "");
}

function statTile(label, value) {
  const wrap = document.createElement("div");
  wrap.className = "warning-snapshot-tile";
  const fallback = document.createElement("span");
  fallback.className = "warning-thumb-fallback";
  fallback.textContent = String(value ?? "-");
  const lbl = document.createElement("span");
  lbl.className = "warning-snapshot-label";
  lbl.textContent = label;
  wrap.appendChild(fallback);
  wrap.appendChild(lbl);
  return wrap;
}

function renderSimpleList(listEl, rows, emptyText, mapFn) {
  listEl.innerHTML = "";
  if (!rows || !rows.length) {
    const li = document.createElement("li");
    li.className = "profile-empty";
    li.textContent = emptyText;
    listEl.appendChild(li);
    return;
  }
  for (const row of rows) {
    const li = document.createElement("li");
    li.className = "ai-alert-row";
    const main = document.createElement("div");
    main.className = "profile-name";
    const sub = document.createElement("div");
    sub.className = "warning-time";
    const mapped = mapFn(row);
    main.textContent = mapped.main;
    sub.textContent = mapped.sub;
    li.appendChild(main);
    li.appendChild(sub);
    listEl.appendChild(li);
  }
}

async function loadDashboard() {
  dashboardRefreshBtn.disabled = true;
  setStatus("Loading dashboard...");
  try {
    const res = await fetch(`${apiBase()}/api/alta-dashboard`);
    const data = await res.json().catch(() => ({ ok: false, error: "Invalid JSON from server." }));
    if (!data.ok) {
      setStatus(data.error || "Could not load Alta dashboard.", "err");
      return;
    }

    const summary = data.summary || {};
    dashboardSummaryGrid.innerHTML = "";
    dashboardSummaryGrid.appendChild(statTile("Total devices", summary.total_devices ?? 0));
    dashboardSummaryGrid.appendChild(statTile("Connected devices", summary.connected_devices ?? 0));
    dashboardSummaryGrid.appendChild(statTile("Offline/unknown", summary.offline_or_unknown_devices ?? 0));
    dashboardSummaryGrid.appendChild(statTile("Active devices", summary.active_devices ?? 0));
    dashboardSummaryGrid.appendChild(statTile("Watchlists", summary.watchlists_total ?? 0));
    dashboardSummaryGrid.appendChild(statTile("Patients profiles", summary.patients_watchlist_profiles ?? 0));

    renderSimpleList(
      dashboardDevices,
      data.devices || [],
      "No devices returned.",
      (row) => ({
        main: row.name || row.id || "Unknown device",
        sub: `Status: ${row.status || "UNKNOWN"}${row.active ? " • Active" : " • Inactive"}`,
      })
    );

    renderSimpleList(
      dashboardWatchlists,
      data.watchlists_sample || [],
      "No watchlists returned.",
      (row) => ({
        main: row.name || "Watchlist",
        sub: row.id ? `ID: ${row.id}` : "",
      })
    );

    renderSimpleList(
      dashboardErrors,
      data.errors || [],
      "No errors reported.",
      (row) => ({
        main: "Info",
        sub: String(row || ""),
      })
    );

    setStatus(`Updated: ${new Date(data.generated_at || Date.now()).toLocaleString()}`, "ok");
  } catch (e) {
    setStatus(e.message || String(e), "err");
  } finally {
    dashboardRefreshBtn.disabled = false;
  }
}

dashboardRefreshBtn.addEventListener("click", () => {
  void loadDashboard();
});

void loadDashboard();
