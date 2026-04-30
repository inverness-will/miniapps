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
      const name = document.createElement("span");
      name.className = "profile-name";
      name.textContent = row.name || row.id;
      const idSpan = document.createElement("span");
      idSpan.className = "profile-id";
      idSpan.textContent = row.id;
      li.appendChild(name);
      li.appendChild(idSpan);
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
  setEnrollMessage(`Added: ${data.profile_name} (${data.profile_id})`, "ok");
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
