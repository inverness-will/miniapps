const $ = (id) => document.getElementById(id);

const video = $("video");
const canvas = $("canvas");
const startCam = $("startCam");
const capture = $("capture");
const profileName = $("profileName");
const camStatus = $("camStatus");
const enrollStatus = $("enrollStatus");
const profileList = $("profileList");
const listStatus = $("listStatus");
const refreshProfiles = $("refreshProfiles");

let stream = null;

/** Same host in production; local static server → Flask on 8765. */
function apiBase() {
  const h = window.location.hostname;
  if (h === "localhost" || h === "127.0.0.1") {
    return "http://127.0.0.1:8765";
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
      const li = document.createElement("li");
      li.className = "profile-row";

      const thumbWrap = document.createElement("div");
      thumbWrap.className = "profile-thumb-wrap";
      const initials = document.createElement("span");
      initials.className = "profile-thumb-fallback";
      initials.textContent = initialsFromName(row.name || row.id);
      thumbWrap.appendChild(initials);

      if (row.thumbnail_data_url) {
        const img = document.createElement("img");
        img.className = "profile-thumb-img";
        img.alt = "";
        img.loading = "lazy";
        img.src = row.thumbnail_data_url;
        img.addEventListener("load", () => {
          initials.style.display = "none";
        });
        img.addEventListener("error", () => {
          img.remove();
        });
        thumbWrap.appendChild(img);
      }

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

startCam.addEventListener("click", async () => {
  setCamMessage("Requesting camera…");
  capture.disabled = true;
  try {
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    setCamMessage("Camera active. Position the face, then capture.", "ok");
    capture.disabled = false;
  } catch (e) {
    setCamMessage(e.message || "Camera unavailable.", "err");
  }
});

capture.addEventListener("click", async () => {
  capture.disabled = true;
  try {
    const dataUrl = dataUrlFromVideo();
    await enroll(dataUrl);
  } catch (e) {
    setEnrollMessage(String(e), "err");
    setCamMessage(String(e), "err");
  } finally {
    capture.disabled = !stream;
  }
});

refreshProfiles.addEventListener("click", () => {
  void loadProfiles();
});

void loadProfiles();
