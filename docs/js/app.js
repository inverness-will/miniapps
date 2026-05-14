const $ = (id) => document.getElementById(id);

const video = $("video");
const videoWrap = $("videoWrap");
const canvas = $("canvas");
const cameraOverlay = $("cameraOverlay");
const capture = $("capture");
const profileName = $("profileName");
const camStatus = $("camStatus");
const enrollStatus = $("enrollStatus");
const profileList = $("profileList");
const listStatus = $("listStatus");
const refreshProfiles = $("refreshProfiles");

let stream = null;
let cameraIdleTimer = null;
const CAMERA_IDLE_MS = 30_000;
const profileNameToId = new Map();

const LS_API_BASE = "patientWatchlistApiBase";

/**
 * Base URL for the Flask proxy (/api/*).
 * - Cursor preview: often http://localhost:8080 → we point at http://127.0.0.1:8765
 * - Desktop Chrome: file://, http://YOUR-HOSTNAME:8080, or http://192.168.x.x:8080 need different rules
 * Override: ?api=http://127.0.0.1:8765 or localStorage.setItem("patientWatchlistApiBase", "http://...")
 */
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
    /* private mode / blocked */
  }

  const proto = window.location.protocol;
  const h = (window.location.hostname || "").toLowerCase();

  if (proto === "file:") {
    return "http://127.0.0.1:8765";
  }
  if (h === "localhost" || h === "127.0.0.1" || h === "[::1]") {
    return "http://127.0.0.1:8765";
  }

  // GitHub Pages / HTTPS deployments: expect API behind same origin (reverse proxy)
  if (h.endsWith("github.io") || proto === "https:") {
    return "";
  }

  // Any other http://HOST:PORT (e.g. machine name or LAN IP + python http.server)
  if (proto === "http:" && h && h !== "localhost" && h !== "127.0.0.1" && h !== "[::1]") {
    return `http://${window.location.hostname}:8765`;
  }

  return "";
}

function setCamMessage(text, kind) {
  camStatus.textContent = text;
  camStatus.className = "inline-status" + (kind ? ` ${kind}` : "");
}

function setEnrollMessage(text, kind) {
  enrollStatus.textContent = text;
  enrollStatus.className = "inline-status enroll-line" + (kind ? ` ${kind}` : "");
}

function initialsFromName(name) {
  const s = (name || "?").trim();
  if (!s) return "?";
  const parts = s.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  return s.slice(0, 2).toUpperCase();
}

function normalizeProfileName(name) {
  return String(name || "")
    .trim()
    .toLowerCase();
}

function showCameraOverlay(show) {
  cameraOverlay.classList.toggle("is-hidden", !show);
}

function clearCameraIdleTimer() {
  if (cameraIdleTimer) {
    window.clearTimeout(cameraIdleTimer);
    cameraIdleTimer = null;
  }
}

function stopCamera(reasonText) {
  clearCameraIdleTimer();
  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
  }
  video.srcObject = null;
  showCameraOverlay(true);
  if (reasonText) {
    setCamMessage(reasonText);
  }
  syncCaptureButton();
}

function markCameraActivity() {
  if (!stream) return;
  clearCameraIdleTimer();
  cameraIdleTimer = window.setTimeout(() => {
    stopCamera("Camera off after 30s idle. Click camera box to enable.");
  }, CAMERA_IDLE_MS);
}

async function removeProfile(profileId, displayName) {
  const label = displayName || profileId;
  if (
    !confirm(
      `Remove "${label}" from the patients watchlist?\n\nThis deletes the face profile in Alta (same as delete_face_profiles.py).`
    )
  ) {
    return;
  }
  listStatus.textContent = "Removing…";
  listStatus.className = "list-foot";
  try {
    const res = await fetch(
      `${apiBase()}/api/profile/${encodeURIComponent(profileId)}?watchlist=patients`,
      { method: "DELETE" }
    );
    const data = await res.json().catch(() => ({ ok: false, error: "Invalid JSON from server" }));
    if (!data.ok) {
      listStatus.textContent = data.error || "Delete failed.";
      listStatus.className = "list-foot err";
      return;
    }
    await loadProfiles();
  } catch (e) {
    listStatus.textContent = e.message || String(e);
    listStatus.className = "list-foot err";
  }
}

async function loadProfiles() {
  profileList.innerHTML = "";
  profileNameToId.clear();
  listStatus.textContent = "Loading…";
  listStatus.className = "list-foot";
  try {
    const res = await fetch(`${apiBase()}/api/patients-profiles?watchlist=patients`);
    const data = await res.json().catch(() => ({ ok: false, error: "Invalid JSON from server" }));
    if (!data.ok) {
      listStatus.textContent = data.error || "Could not load profiles.";
      listStatus.className = "list-foot err";
      return;
    }
    const rows = data.profiles || [];
    listStatus.textContent = `${rows.length} profile(s)`;
    listStatus.className = "list-foot ok";
    if (!rows.length) {
      const li = document.createElement("li");
      li.className = "profile-empty";
      li.textContent = "No profiles on this watchlist yet.";
      profileList.appendChild(li);
      return;
    }
    for (const row of rows) {
      const nKey = normalizeProfileName(row.name || "");
      if (nKey && row.id) {
        profileNameToId.set(nKey, String(row.id));
      }
      const li = document.createElement("li");
      li.className = "profile-row";

      const thumbWrap = document.createElement("div");
      thumbWrap.className = "profile-thumb-wrap";
      const initials = document.createElement("span");
      initials.className = "profile-thumb-fallback";
      initials.textContent = initialsFromName(row.name || row.id);
      thumbWrap.appendChild(initials);

      const img = document.createElement("img");
      img.className = "profile-thumb-img";
      img.alt = "";
      img.width = 56;
      img.height = 56;
      img.loading = "lazy";
      img.src = `${apiBase()}/api/profile/${encodeURIComponent(row.id)}/thumbnail?watchlist=patients`;
      img.addEventListener("load", () => {
        initials.style.display = "none";
      });
      img.addEventListener("error", () => {
        img.remove();
      });
      thumbWrap.appendChild(img);

      const meta = document.createElement("div");
      meta.className = "profile-meta";
      const nameEl = document.createElement("div");
      nameEl.className = "profile-name";
      nameEl.textContent = row.name || row.id;
      meta.appendChild(nameEl);

      const actions = document.createElement("div");
      actions.className = "profile-actions";
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn btn-danger";
      delBtn.textContent = "Remove";
      delBtn.addEventListener("click", () => {
        void removeProfile(row.id, row.name);
      });
      actions.appendChild(delBtn);

      li.appendChild(thumbWrap);
      li.appendChild(meta);
      li.appendChild(actions);
      profileList.appendChild(li);
    }
  } catch (e) {
    listStatus.textContent = e.message || String(e);
    listStatus.className = "list-foot err";
  }
}

function dataUrlFromVideo() {
  const w = video.videoWidth;
  const h = video.videoHeight;
  if (!w || !h) {
    throw new Error("Video not ready yet.");
  }
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, w, h);
  return canvas.toDataURL("image/jpeg", 0.92);
}

async function enroll(dataUrl) {
  const name = profileName.value.trim();
  if (!name) {
    setEnrollMessage("Enter a profile name.", "err");
    return;
  }
  setEnrollMessage("Sending…");
  const res = await fetch(`${apiBase()}/api/enroll`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      profile_name: name,
      image: dataUrl,
      watchlist_name: "patients",
    }),
  });
  const data = await res.json().catch(() => ({ ok: false, error: "Invalid JSON from server" }));
  if (!data.ok) {
    setEnrollMessage(data.error || "Enrollment failed.", "err");
    setCamMessage(data.error || "Enrollment failed.", "err");
    return;
  }
  setEnrollMessage(`Added: ${data.profile_name}`, "ok");
  setCamMessage("Added to patients watchlist.", "ok");
  await loadProfiles();
}

function syncCaptureButton() {
  const nameOk = profileName.value.trim().length > 0;
  capture.disabled = !nameOk || !stream;
}

async function startCamera() {
  setCamMessage("Requesting camera…");
  try {
    stopCamera("");
    showCameraOverlay(false);
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    setCamMessage("Camera active. Enter a name, then capture.", "ok");
    markCameraActivity();
  } catch (e) {
    setCamMessage(e.message || "Camera unavailable.", "err");
    showCameraOverlay(true);
  }
  syncCaptureButton();
}

profileName.addEventListener("input", () => {
  syncCaptureButton();
  markCameraActivity();
});

capture.addEventListener("click", async () => {
  markCameraActivity();
  capture.disabled = true;
  try {
    const dataUrl = dataUrlFromVideo();
    await enroll(dataUrl);
  } catch (e) {
    setEnrollMessage(String(e), "err");
    setCamMessage(String(e), "err");
  } finally {
    markCameraActivity();
    syncCaptureButton();
  }
});

videoWrap.addEventListener("click", () => {
  if (!stream) {
    void startCamera();
  } else {
    markCameraActivity();
  }
});

videoWrap.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    if (!stream) {
      void startCamera();
    } else {
      markCameraActivity();
    }
  }
});

refreshProfiles.addEventListener("click", () => {
  void loadProfiles();
});
void loadProfiles();
void startCamera();
