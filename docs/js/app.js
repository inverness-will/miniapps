const STORAGE_KEY = "patientWatchlistProxyBase";

const $ = (id) => document.getElementById(id);

const video = $("video");
const canvas = $("canvas");
const startCam = $("startCam");
const capture = $("capture");
const profileName = $("profileName");
const proxyBase = $("proxyBase");
const saveProxy = $("saveProxy");
const camStatus = $("camStatus");
const healthStatus = $("healthStatus");
const result = $("result");

let stream = null;

function normalizeBase(url) {
  const u = (url || "").trim().replace(/\/+$/, "");
  return u || "http://127.0.0.1:8765";
}

function loadProxy() {
  const saved = localStorage.getItem(STORAGE_KEY);
  proxyBase.value = saved || "http://127.0.0.1:8765";
}

function setCamMessage(text, kind = "muted") {
  camStatus.textContent = text;
  camStatus.className = `status ${kind}`;
}

function setHealthMessage(text, kind = "muted") {
  healthStatus.textContent = text;
  healthStatus.className = `status ${kind}`;
}

function setResult(obj) {
  result.textContent = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
}

async function checkHealth() {
  const base = normalizeBase(proxyBase.value);
  try {
    const res = await fetch(`${base}/api/health`);
    const data = await res.json();
    if (data.ok && data.alta_configured) {
      setHealthMessage(`Proxy OK · default watchlist: “${data.default_watchlist}”`, "ok");
    } else if (data.ok) {
      setHealthMessage("Proxy reachable but Alta env vars missing on server (.env).", "err");
    } else {
      setHealthMessage("Unexpected health response.", "err");
    }
  } catch (e) {
    setHealthMessage(`Cannot reach proxy at ${base} — start server.py or fix URL.`, "err");
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
  const base = normalizeBase(proxyBase.value);
  const name = profileName.value.trim();
  if (!name) {
    setResult("Enter a display name for the profile.");
    return;
  }
  setResult("Sending…");
  const res = await fetch(`${base}/api/enroll`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      profile_name: name,
      image: dataUrl,
      watchlist_name: "patients",
    }),
  });
  const data = await res.json().catch(() => ({ error: "Invalid JSON from proxy" }));
  setResult(data);
  if (!data.ok) {
    setCamMessage(data.error || "Enrollment failed.", "err");
  } else {
    setCamMessage("Added to patients watchlist.", "ok");
  }
}

startCam.addEventListener("click", async () => {
  setCamMessage("Requesting camera…", "muted");
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
    setCamMessage(e.message || "Camera permission denied or unavailable.", "err");
  }
});

capture.addEventListener("click", async () => {
  capture.disabled = true;
  try {
    const dataUrl = dataUrlFromVideo();
    await enroll(dataUrl);
  } catch (e) {
    setResult(String(e));
    setCamMessage(String(e), "err");
  } finally {
    capture.disabled = !stream;
  }
});

saveProxy.addEventListener("click", () => {
  localStorage.setItem(STORAGE_KEY, normalizeBase(proxyBase.value));
  void checkHealth();
});

proxyBase.addEventListener("change", () => {
  void checkHealth();
});

loadProxy();
void checkHealth();
