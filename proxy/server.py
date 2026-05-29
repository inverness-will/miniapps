#!/usr/bin/env python3
"""
Local or server-side proxy for Alta Video face watchlist enrollment.
Mirrors the API flow in alta/faces.py: login → list watchlists → generate face
→ create profile → attach face. Keeps credentials off GitHub Pages.

HTTP API: POST /api/enroll, GET /api/patients-profiles, GET /api/profile/<id>/thumbnail,
DELETE /api/profile/<id>, POST /api/webhook and POST /webhook (debug log + store warning),
GET /api/webhook-latest, POST /api/webhook-clear, GET /api/webhook-entry-latest,
GET /api/webhook-entry-log, POST /api/webhook-entry-clear, POST /api/webhook-car-crossing, GET /api/car-timing-log,
POST /api/car-timing-clear, GET /api/health.
Debug: set ALTA_DEBUG=1 to log username and env wiring on stderr; add ALTA_DEBUG_LOG_PASSWORD=1
to log password repr (remove after troubleshooting — credentials in logs are a security risk).
"""

import base64
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from uuid import uuid4

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

HOST = os.environ.get("ALTA_HOST", "").strip().rstrip("/")
USERNAME = os.environ.get("ALTA_USERNAME", "").strip()
PASSWORD = os.environ.get("ALTA_PASSWORD", "").strip()
DEFAULT_WATCHLIST = os.environ.get("WATCHLIST_NAME", "patients").strip().lower()
PLAYBACK_STREAM_URL_TEMPLATE = os.environ.get("ALTA_PLAYBACK_STREAM_URL_TEMPLATE", "").strip()
LIVE_SNAPSHOT_INTERVAL_SECONDS = float((os.environ.get("LIVE_SNAPSHOT_INTERVAL_SECONDS") or "5").strip() or "5")
LIVE_SNAPSHOT_MAX_AGE_SECONDS = float((os.environ.get("LIVE_SNAPSHOT_MAX_AGE_SECONDS") or "20").strip() or "20")
LIVE_SNAPSHOT_SOURCE_ID = (os.environ.get("LIVE_SNAPSHOT_SOURCE_ID") or "1").strip() or "1"
LIVE_SNAPSHOT_HTTP_TIMEOUT_SECONDS = float((os.environ.get("LIVE_SNAPSHOT_HTTP_TIMEOUT_SECONDS") or "10").strip() or "10")
LIVE_SNAPSHOT_CAMERAS = [
    c.strip() for c in (os.environ.get("LIVE_SNAPSHOT_CAMERAS") or "").split(",") if c.strip()
]
VLM_API_URL = (os.environ.get("VLM_API_URL") or "https://api.openai.com/v1/responses").strip()
VLM_API_KEY = (os.environ.get("VLM_API_KEY") or "").strip()
VLM_MODEL = (os.environ.get("VLM_MODEL") or "gpt-4.1-mini").strip()
_WEBHOOK_LOCK = Lock()
_EMPTY_WEBHOOK: dict[str, Any] = {
    "ok": True,
    "has_warning": False,
    "event": "",
    "patient_name": "",
    "patient_id": "",
    "location": "",
    "image_data_url": "",
    "received_at": "",
}
_LATEST_WEBHOOK: dict[str, Any] = dict(_EMPTY_WEBHOOK)
_EMPTY_CAMERA_WEBHOOK: dict[str, Any] = {
    "ok": True,
    "has_warning": False,
    "event": "",
    "patient_name": "",
    "patient_id": "",
    "location": "",
    "image_data_url": "",
    "received_at": "",
    "camera": "",
    "trigger_time": "",
    "snapshots": [],
    "debug_trace": [],
    "matched_alert_id": "",
    "matched_alert_name": "",
    "vlm_analysis": "",
    "vlm_error": "",
}
_LATEST_CAMERA_WEBHOOK: dict[str, Any] = dict(_EMPTY_CAMERA_WEBHOOK)
_CAMERA_WEBHOOK_HISTORY: list[dict[str, Any]] = []
_CAMERA_WEBHOOK_HISTORY_MAX = 100
_EMPTY_CAR_CROSSING_WEBHOOK: dict[str, Any] = {
    "ok": True,
    "has_event": False,
    "car_id": "",
    "entering": False,
    "leaving": False,
    "event_time": "",
    "received_at": "",
}
_LATEST_CAR_CROSSING_WEBHOOK: dict[str, Any] = dict(_EMPTY_CAR_CROSSING_WEBHOOK)
_CAR_TIMING_PENDING: dict[str, str] = {}
_CAR_TIMING_LOG: list[dict[str, Any]] = []
_LIVE_SNAPSHOT_LOCK = Lock()
_LIVE_SNAPSHOT_CACHE: dict[str, list[dict[str, Any]]] = {}
_MONITORED_CAMERAS: set[str] = set(LIVE_SNAPSHOT_CAMERAS)
_AI_ALERTS_LOCK = Lock()
_AI_ALERTS_PATH = Path(__file__).resolve().parent / "ai_alerts.json"


def _load_ai_alerts() -> list[dict[str, str]]:
    if not _AI_ALERTS_PATH.exists():
        return []
    try:
        data = json.loads(_AI_ALERTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        alert_id = str(row.get("id") or "").strip()
        name = str(row.get("name") or "").strip()
        prompt = str(row.get("prompt") or "").strip()
        created_at = str(row.get("created_at") or "").strip()
        if not alert_id or not name or not prompt:
            continue
        out.append(
            {
                "id": alert_id,
                "name": name,
                "prompt": prompt,
                "created_at": created_at,
            }
        )
    return out


def _save_ai_alerts(alerts: list[dict[str, str]]) -> None:
    _AI_ALERTS_PATH.write_text(json.dumps(alerts, indent=2), encoding="utf-8")


def _normalize_key(s: str) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def _find_alert_by_name(alert_name: str) -> dict[str, str] | None:
    wanted = _normalize_key(alert_name)
    if not wanted:
        return None
    with _AI_ALERTS_LOCK:
        rows = _load_ai_alerts()
    for row in rows:
        if _normalize_key(row.get("name", "")) == wanted:
            return row
    return None


def _snapshot_data_urls(snapshots: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in snapshots[:1]:
        if not isinstance(row, dict):
            continue
        v = str(row.get("image_data_url") or "").strip()
        if v:
            out.append(v)
    return out


def _call_cloud_vlm(prompt: str, image_data_urls: list[str]) -> tuple[str, str]:
    if not VLM_API_KEY:
        return "", "VLM_API_KEY is not configured."
    if not VLM_API_URL:
        return "", "VLM_API_URL is not configured."
    if not VLM_MODEL:
        return "", "VLM_MODEL is not configured."
    if not prompt.strip():
        return "", "Prompt is empty."
    if not image_data_urls:
        return "", "No snapshots available for VLM."

    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for data_url in image_data_urls[:3]:
        content.append({"type": "input_image", "image_url": data_url})

    body = {
        "model": VLM_MODEL,
        "input": [{"role": "user", "content": content}],
    }
    headers = {
        "Authorization": f"Bearer {VLM_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(VLM_API_URL, headers=headers, json=body, timeout=120)
    except requests.RequestException as ex:
        return "", f"VLM request failed: {ex}"
    if resp.status_code >= 400:
        preview = (resp.text or "")[:500]
        return "", f"VLM HTTP {resp.status_code}: {preview}"
    try:
        data = resp.json()
    except Exception:
        return "", "VLM response was not JSON."

    text = str(data.get("output_text") or "").strip()
    if text:
        return text, ""
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content_rows = item.get("content")
            if not isinstance(content_rows, list):
                continue
            for c in content_rows:
                if not isinstance(c, dict):
                    continue
                if c.get("type") in ("output_text", "text"):
                    t = str(c.get("text") or "").strip()
                    if t:
                        return t, ""
    return "", "VLM response contained no text output."


def _webhook_value(payload: dict[str, Any], *keys: str) -> str:
    """
    Extract first non-empty scalar value for any candidate key.
    Handles common webhook variants:
    - snake/camel/kebab/space key formats
    - nested objects/lists (depth-first search)
    """
    wanted = {_normalize_key(k) for k in keys if str(k).strip()}
    if not wanted:
        return ""

    def _walk(node: Any) -> str:
        if isinstance(node, dict):
            # Prefer direct scalar matches at this level first.
            for raw_key, raw_val in node.items():
                if _normalize_key(raw_key) in wanted and raw_val is not None and not isinstance(raw_val, (dict, list)):
                    value = str(raw_val).strip()
                    if value:
                        return value
            # Then recurse through nested structures.
            for raw_val in node.values():
                out = _walk(raw_val)
                if out:
                    return out
            return ""
        if isinstance(node, list):
            for item in node:
                out = _walk(item)
                if out:
                    return out
        return ""

    return _walk(payload)


def _normalize_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    event = _webhook_value(payload, "event", "type", "alert", "event_type", "alarm")
    patient_name = _webhook_value(
        payload,
        "patient_name",
        "patient name",
        "name",
        "profile_name",
        "subject_name",
        "person_name",
    )
    patient_id = _webhook_value(
        payload,
        "patient_id",
        "profile_id",
        "profileId",
        "watchlists_profile_id",
        "watchlistsProfileId",
        "subject_id",
        "person_id",
        "personId",
        "id",
    )
    location = _webhook_value(payload, "location", "zone", "camera", "site", "camera_name")
    image_raw = _webhook_value(payload, "image", "thumbnail", "face", "snapshot", "crop")
    image_data_url = ""
    if image_raw:
        if image_raw.startswith("data:image/"):
            image_data_url = image_raw
        else:
            # Webhooks usually send base64 jpeg payload without a data URL prefix.
            image_data_url = f"data:image/jpeg;base64,{image_raw}"
    has_warning = bool(event or patient_name or patient_id or location or image_data_url)
    return {
        "ok": True,
        "has_warning": has_warning,
        "event": event or "Warning",
        "patient_name": patient_name or "Unknown patient",
        "patient_id": patient_id,
        "location": location or "Unknown location",
        "image_data_url": image_data_url,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }


def _alta_debug_verbose() -> bool:
    v = os.environ.get("ALTA_DEBUG", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _login_log(msg: str, *, always: bool = False) -> None:
    """stderr traces for login. Set ALTA_DEBUG=1 for full detail; failed logins always get one line."""
    if always or _alta_debug_verbose():
        print(f"[alta-login] {msg}", file=sys.stderr, flush=True)


def _alta_debug_log_password() -> bool:
    v = os.environ.get("ALTA_DEBUG_LOG_PASSWORD", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _log_login_credentials(host: str, username: str, password: str) -> None:
    """When ALTA_DEBUG=1, show exactly what is used for login (repr shows hidden spaces)."""
    if not _alta_debug_verbose():
        return
    raw_h = os.environ.get("ALTA_HOST", "<missing>")
    if "ALTA_USERNAME" in os.environ:
        raw_u = os.environ["ALTA_USERNAME"]
        raw_u_note = f"present, len={len(raw_u)}, repr={raw_u!r}"
    else:
        raw_u_note = "<ALTA_USERNAME not set in environment>"
    if "ALTA_PASSWORD" in os.environ:
        raw_p = os.environ["ALTA_PASSWORD"]
        raw_p_note = f"present, len={len(raw_p)}"
    else:
        raw_p = ""
        raw_p_note = "<ALTA_PASSWORD not set in environment>"
    _login_log("--- login credentials debug (ALTA_DEBUG=1) ---", always=True)
    _login_log(f"env ALTA_HOST: {raw_h!r}", always=True)
    _login_log(f"effective host (after strip): {host!r}", always=True)
    _login_log(f"env ALTA_USERNAME: {raw_u_note}", always=True)
    _login_log(f"value sent as JSON username: {username!r} (len={len(username)})", always=True)
    _login_log(f"env ALTA_PASSWORD: {raw_p_note}", always=True)
    if _alta_debug_log_password():
        _login_log(
            "WARNING: ALTA_DEBUG_LOG_PASSWORD=1 — printing password repr; disable after debugging.",
            always=True,
        )
        if "ALTA_PASSWORD" in os.environ:
            _login_log(f"env ALTA_PASSWORD raw repr: {os.environ['ALTA_PASSWORD']!r}", always=True)
        _login_log(f"value sent as JSON password repr: {password!r} (len={len(password)})", always=True)
    else:
        _login_log(
            "password repr not printed (set ALTA_DEBUG_LOG_PASSWORD=1 to log env + JSON password).",
            always=True,
        )
    _login_log("--- end credentials debug ---", always=True)


def _safe_headers_for_log(session: requests.Session) -> str:
    h = dict(session.headers)
    # avoid dumping huge auth headers if any
    for k in list(h.keys()):
        if k.lower() == "authorization":
            h[k] = "<redacted>"
    return repr(h)


# Some Avigilon Alta cloud hosts return 401 for the default python-requests User-Agent.
_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def apply_alta_session_defaults(session: requests.Session) -> None:
    # Session() pre-populates User-Agent (python-requests/…) and Accept (*/*).
    # setdefault does NOT override those, so we must assign to match browser clients.
    session.headers["Accept"] = "application/json, text/plain, */*"
    session.headers["User-Agent"] = (os.environ.get("ALTA_USER_AGENT") or "").strip() or _DEFAULT_UA


def aware_get(session: requests.Session, url: str, params=None) -> Any:
    apply_alta_session_defaults(session)
    resp = session.get(url, params=params, timeout=60)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return None


def aware_post(session: requests.Session, url: str, data: dict) -> Any:
    apply_alta_session_defaults(session)
    resp = session.post(url, json=data, timeout=60)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return None


def aware_delete(session: requests.Session, url: str) -> None:
    apply_alta_session_defaults(session)
    resp = session.delete(url, timeout=60)
    resp.raise_for_status()


def _login_response_json(resp: requests.Response) -> Any:
    if not resp.content:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def do_login(session: requests.Session, host: str, username: str, password: str) -> Any:
    """
    POST JSON {"username", "password"} to Alta login.

    Default path is /api/v1/dologin (same as faces.py). Set ALTA_LOGIN_PATH only if your
    server uses a different path; use /api/v1/dologin or the shorthand dologin.
    """
    apply_alta_session_defaults(session)
    _log_login_credentials(host, username, password)
    base = host.rstrip("/")
    cred = {"username": username, "password": password}

    raw = (os.environ.get("ALTA_LOGIN_PATH") or "").strip()
    if raw:
        if raw.startswith("/"):
            path = raw
        elif "/" not in raw:
            path = "/api/v1/" + raw
        else:
            path = "/" + raw.lstrip("/")
    else:
        path = "/api/v1/dologin"

    url = base + path
    ua = session.headers.get("User-Agent", "")
    if _alta_debug_verbose():
        _login_log(
            f"POST {url} | User-Agent[:80]={ua[:80]!r}…",
            always=True,
        )
        _login_log(f"request headers: {_safe_headers_for_log(session)}", always=True)
        _login_log(
            "dologin request JSON (contains password — only with ALTA_DEBUG=1): "
            + json.dumps(cred, ensure_ascii=False),
            always=True,
        )

    resp = session.post(url, json=cred, timeout=60)

    ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
    body_preview = (resp.text or "")[:600].replace("\r", " ").replace("\n", " ")
    set_cookie = resp.headers.get("Set-Cookie")
    _login_log(
        f"response status={resp.status_code} {resp.reason!r} | Content-Type={ct!r} | "
        f"Content-Length={resp.headers.get('Content-Length', '?')} | Set-Cookie present={bool(set_cookie)}",
        always=True,
    )
    if resp.status_code != 200 or _alta_debug_verbose():
        _login_log(f"response body preview: {body_preview!r}", always=(resp.status_code != 200))
    if _alta_debug_verbose():
        try:
            cj = list(session.cookies.keys())
            _login_log(f"session.cookies after login: {cj}")
        except Exception as ex:
            _login_log(f"session.cookies read failed: {ex}")

    resp.raise_for_status()
    return _login_response_json(resp)


def _http_error_payload(exc: requests.HTTPError) -> tuple[dict, int]:
    """Build JSON body and HTTP status for upstream Alta errors."""
    resp = exc.response
    detail: Any = ""
    if resp is not None:
        try:
            detail = resp.json()
        except Exception:
            detail = (resp.text or "")[:800]
    code = resp.status_code if resp is not None else 502
    msg = str(exc)
    if code == 401 and resp is not None and "login" in (resp.url or "").lower():
        msg = (
            "Alta rejected login (401 Unauthorized). "
            "Use the same username and password as the Alta web UI (often your full email); "
            "check proxy/.env for typos or trailing spaces after ALTA_PASSWORD."
        )
    return {"ok": False, "error": msg, "detail": detail, "upstream_status": code}, 502


def list_face_watchlists(session: requests.Session, host: str):
    base = host.rstrip("/")
    candidates = [
        "/api/v1/faceWatchlists",
        "/api/v1/face-watchlists",
        "/api/v1/watchlists",
        "/api/v1/faceWatchList",
    ]
    for path in candidates:
        url = base + path
        try:
            data = aware_get(session, url)
            if data is not None:
                return path, data
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                continue
            raise
    return None, None


def _watchlists_from_response(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        out = (
            data.get("watchlists")
            or data.get("items")
            or data.get("faceWatchlists")
            or data.get("data")
            or []
        )
        return out if isinstance(out, list) else [data]
    return []


def find_watchlist_id(watchlists: list, name_query: str) -> tuple[str | None, str | None]:
    """Return (id, display_name) for first watchlist whose name matches name_query (case-insensitive)."""
    q = (name_query or "patients").strip().lower()
    for wl in watchlists:
        name = wl.get("name") or wl.get("title") or wl.get("label") or ""
        if name.strip().lower() == q:
            wid = wl.get("id") or wl.get("watchlistId") or wl.get("guid")
            if wid:
                return str(wid), name
    return None, None


def find_watchlist_dict(watchlists: list, wl_id: str) -> dict | None:
    for wl in watchlists:
        wid = wl.get("id") or wl.get("watchlistId") or wl.get("guid")
        if wid is not None and str(wid) == str(wl_id):
            return wl
    return None


def _profiles_from_response(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        out = (
            data.get("profiles")
            or data.get("watchlistsProfiles")
            or data.get("items")
            or data.get("data")
            or []
        )
        return out if isinstance(out, list) else []
    return []


def list_all_profiles(session: requests.Session, host: str) -> tuple[str | None, list]:
    base = host.rstrip("/")
    candidates = ["/api/v1/watchlistsProfiles", "/api/v1/watchlists-profiles"]
    for path in candidates:
        url = base + path
        try:
            data = aware_get(session, url)
            if data is not None:
                profiles = _profiles_from_response(data)
                return path, profiles
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                continue
            raise
    return None, []


def parse_data_url(data_url: str) -> tuple[bytes, str]:
    """
    Accept raw base64 or data URL like data:image/jpeg;base64,XXXX.
    Returns (raw_bytes, format_string for Alta API e.g. image/jpeg;base64).
    """
    s = (data_url or "").strip()
    if s.startswith("data:"):
        m = re.match(r"data:image/(jpeg|jpg|png);base64,(.+)", s, re.I | re.DOTALL)
        if not m:
            raise ValueError("Expected data:image/jpeg;base64,... or data:image/png;base64,...")
        kind = m.group(1).lower()
        b64 = m.group(2).replace("\n", "").replace("\r", "")
        raw = base64.b64decode(b64, validate=True)
        fmt = "image/jpeg;base64" if kind in ("jpeg", "jpg") else "image/png;base64"
        return raw, fmt
    raw = base64.b64decode(s, validate=True)
    return raw, "image/jpeg;base64"


def _get_bytes_if_image_response(session: requests.Session, url: str) -> tuple[bytes | None, str | None]:
    apply_alta_session_defaults(session)
    try:
        r = session.get(url, timeout=60)
    except requests.RequestException:
        return None, None
    if r.status_code != 200 or not r.content:
        return None, None
    ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ct.startswith("image/"):
        return r.content, ct
    return None, None


_JPEG = b"\xff\xd8\xff"
_PNG = b"\x89PNG\r\n\x1a\n"


def _decode_photo_string(s: str) -> tuple[bytes | None, str | None]:
    """Decode data URL or raw base64; return None if not a JPEG/PNG blob."""
    s = (s or "").strip()
    if not s:
        return None, None
    if s.startswith("data:image"):
        try:
            raw, fmt = parse_data_url(s)
            mime = "image/jpeg" if "jpeg" in fmt or "jpg" in fmt else "image/png"
            return raw, mime
        except Exception:
            return None, None
    if len(s) < 120:
        return None, None
    try:
        raw = base64.b64decode(s, validate=False)
    except Exception:
        return None, None
    if len(raw) < 200:
        return None, None
    if raw[:3] == _JPEG[:3]:
        return raw, "image/jpeg"
    if raw[:8] == _PNG:
        return raw, "image/png"
    return None, None


def _image_from_obj_strings(obj: dict) -> tuple[bytes | None, str | None]:
    """Parse image from profile/face JSON. Enrollment photos first; generic thumbnail last."""
    best: tuple[bytes | None, str | None] = (None, None)
    best_sz = 0
    for key in (
        "source_image",
        "sourceImage",
        "display_image",
        "displayImage",
        "full_image",
        "fullImage",
        "original_image",
        "originalImage",
        "enrollment_image",
        "face_image",
        "image",
        "imageUrl",
        "portrait",
        "portraitUrl",
        "photo",
        "preview",
        "avatar",
        "thumbnail",
        "thumbnailUrl",
    ):
        val = obj.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        v = val.strip()
        if v.startswith("http"):
            continue
        raw, mime = _decode_photo_string(v)
        if not raw:
            continue
        if key in ("source_image", "sourceImage", "display_image", "displayImage", "full_image", "fullImage"):
            min_sz = 120
        elif key in ("thumbnail", "thumbnailUrl", "preview", "avatar"):
            min_sz = 3500
        else:
            min_sz = 800
        if len(raw) < min_sz:
            continue
        if len(raw) > best_sz:
            best, best_sz = (raw, mime), len(raw)
    return best


def list_all_watchlists_profiles_faces(session: requests.Session, host: str) -> list:
    """GET global face rows (includes profile_id) when the deployment exposes this list."""
    base = host.rstrip("/")
    for path in ("/api/v1/watchlistsProfilesFaces", "/api/v1/watchlists-profiles-faces"):
        apply_alta_session_defaults(session)
        try:
            r = session.get(base + path, timeout=90)
        except requests.RequestException:
            continue
        if r.status_code != 200 or not r.content:
            continue
        try:
            j = r.json()
        except Exception:
            continue
        rows: list = []
        if isinstance(j, list):
            rows = j
        elif isinstance(j, dict):
            rows = (
                j.get("faces")
                or j.get("watchlistsProfilesFaces")
                or j.get("items")
                or j.get("data")
                or []
            )
        if isinstance(rows, list) and rows:
            return [x for x in rows if isinstance(x, dict)]
    return []


def index_profile_face_photos(session: requests.Session, host: str) -> dict[str, tuple[bytes, str]]:
    """
    profile_id -> (image bytes, mime) from watchlistsProfilesFaces list.
    Picks the largest valid image per profile (enrollment photo is usually the biggest).
    """
    rows = list_all_watchlists_profiles_faces(session, host)
    best: dict[str, tuple[bytes, str, int]] = {}
    for face in rows:
        pid = face.get("profile_id") or face.get("profileId")
        if pid is None:
            continue
        pid_s = str(pid)
        raw, mime = _image_from_obj_strings(face)
        if not raw or not mime:
            continue
        sz = len(raw)
        prev = best.get(pid_s)
        if prev is None or sz > prev[2]:
            best[pid_s] = (raw, mime, sz)
    return {k: (v[0], v[1]) for k, v in best.items()}


def fetch_faces_for_profile(session: requests.Session, host: str, profile_id: str) -> list:
    """GET face rows for a single profile (when a global list is not available)."""
    base = host.rstrip("/")
    for path in (
        f"/api/v1/watchlistsProfiles/{profile_id}/faces",
        f"/api/v1/watchlists-profiles/{profile_id}/faces",
    ):
        apply_alta_session_defaults(session)
        try:
            r = session.get(base + path, timeout=60)
        except requests.RequestException:
            continue
        if r.status_code != 200 or not r.content:
            continue
        try:
            j = r.json()
        except Exception:
            continue
        rows: list = []
        if isinstance(j, list):
            rows = j
        elif isinstance(j, dict):
            rows = (
                j.get("faces")
                or j.get("watchlistsProfilesFaces")
                or j.get("items")
                or j.get("data")
                or []
            )
        if isinstance(rows, list) and rows:
            return [x for x in rows if isinstance(x, dict)]
    return []


def merge_face_photos_from_profile_endpoints(
    session: requests.Session, host: str, face_photos: dict[str, tuple[bytes, str]], pids: set[str]
) -> dict[str, tuple[bytes, str]]:
    """Fill missing profile IDs using per-profile /faces responses."""
    out = dict(face_photos)
    for pid in pids:
        if pid in out:
            continue
        rows = fetch_faces_for_profile(session, host, pid)
        best: tuple[bytes | None, str | None] = (None, None)
        best_sz = 0
        for face in rows:
            raw, mime = _image_from_obj_strings(face)
            if raw and mime and len(raw) > best_sz:
                best, best_sz = (raw, mime), len(raw)
        if best[0] and best[1]:
            out[pid] = (best[0], best[1])
    return out


def _profile_thumbnail_data_url(
    session: requests.Session, host: str, profile_id: str, profile_list_row: dict | None
) -> str | None:
    """Fallback when watchlistsProfilesFaces list is unavailable: profile JSON / photo URLs."""
    base = host.rstrip("/")
    max_bytes = 400_000
    for dpath in (
        f"/api/v1/watchlistsProfiles/{profile_id}",
        f"/api/v1/watchlists-profiles/{profile_id}",
    ):
        apply_alta_session_defaults(session)
        try:
            r = session.get(base + dpath, timeout=60)
        except requests.RequestException:
            continue
        if r.status_code != 200 or not r.content:
            continue
        try:
            j = r.json()
        except Exception:
            continue
        if not isinstance(j, dict):
            continue
        raw, mime = _image_from_obj_strings(j)
        if raw and len(raw) <= max_bytes:
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"
        for nest_key in ("face", "faces", "primaryFace"):
            nest = j.get(nest_key)
            if isinstance(nest, dict):
                raw, mime = _image_from_obj_strings(nest)
                if raw and len(raw) <= max_bytes:
                    b64 = base64.b64encode(raw).decode("ascii")
                    return f"data:{mime};base64,{b64}"
            if isinstance(nest, list):
                for item in nest:
                    if isinstance(item, dict):
                        raw, mime = _image_from_obj_strings(item)
                        if raw and len(raw) <= max_bytes:
                            b64 = base64.b64encode(raw).decode("ascii")
                            return f"data:{mime};base64,{b64}"
        host_tail = host.split("//", 1)[-1].split("/")[0]
        for _key, val in j.items():
            if isinstance(val, str) and val.startswith("https://") and host_tail in val:
                u = val.split("?", 1)[0]
                raw, mime = _get_bytes_if_image_response(session, u)
                if raw and len(raw) <= max_bytes:
                    b64 = base64.b64encode(raw).decode("ascii")
                    return f"data:{mime};base64,{b64}"
    # Binary resources (omit /thumbnail — often not the enrollment portrait)
    for suffix in ("/photo", "/image", "/portrait", "/picture"):
        raw, mime = _get_bytes_if_image_response(
            session, f"{base}/api/v1/watchlistsProfiles/{profile_id}{suffix}"
        )
        if raw and len(raw) <= max_bytes:
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"
    if profile_list_row and isinstance(profile_list_row, dict):
        raw, mime = _image_from_obj_strings(profile_list_row)
        if raw and len(raw) <= max_bytes:
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"
    return None


def patient_roster_data(
    session: requests.Session, host: str, watchlist_name: str | None
) -> dict:
    """
    Shared context for patients watchlist: ids, names, profile rows, merged face photos.
    On failure returns {"ok": False, "error": "..."}.
    """
    wl_query = (watchlist_name or DEFAULT_WATCHLIST or "patients").strip().lower()
    _endpoint, data = list_face_watchlists(session, host)
    watchlists = _watchlists_from_response(data or [])
    wl_id, wl_display = find_watchlist_id(watchlists, wl_query)
    if not wl_id:
        names = [w.get("name") or w.get("title") or "?" for w in watchlists[:20]]
        return {
            "ok": False,
            "error": f"No watchlist named '{wl_query}'. Sample names: {names}",
        }

    wl_row = find_watchlist_dict(watchlists, wl_id) or {}
    raw_ids = wl_row.get("profile_ids") or wl_row.get("profileIds") or []
    if not isinstance(raw_ids, list):
        raw_ids = []
    pids_set = {str(p) for p in raw_ids if p is not None}

    prof_path, all_profiles = list_all_profiles(session, host)
    id_to_name: dict[str, str] = {}
    id_to_profile: dict[str, dict] = {}
    for p in all_profiles:
        if not isinstance(p, dict):
            continue
        pid = p.get("id") or p.get("profileId") or p.get("guid")
        if not pid:
            continue
        pid_s = str(pid)
        nm = p.get("name") or p.get("title") or pid_s
        id_to_name[pid_s] = str(nm)
        wls = p.get("watchlists") or p.get("watchlist_ids") or p.get("watchlistIds") or []
        if isinstance(wls, list) and str(wl_id) in {str(x) for x in wls}:
            pids_set.add(pid_s)
        if pid_s in pids_set:
            id_to_profile[pid_s] = p

    face_photos = index_profile_face_photos(session, host)
    face_photos = merge_face_photos_from_profile_endpoints(session, host, face_photos, pids_set)

    return {
        "ok": True,
        "watchlist_id": wl_id,
        "watchlist_name": wl_display,
        "profiles_endpoint": prof_path,
        "pids_set": pids_set,
        "id_to_name": id_to_name,
        "id_to_profile": id_to_profile,
        "face_photos": face_photos,
    }


def _bytes_from_thumbnail_data_url(data_url: str) -> tuple[bytes | None, str | None]:
    if not data_url or not str(data_url).startswith("data:"):
        return None, None
    try:
        raw, fmt = parse_data_url(str(data_url))
        mime = "image/jpeg" if "jpeg" in fmt or "jpg" in fmt else "image/png"
        return raw, mime
    except Exception:
        return None, None


def _parse_webhook_time(value: str) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    # Accept numeric epoch in string form, including fractional seconds
    # e.g. "1778328198.0150001049" from some webhook systems.
    try:
        f = float(s)
        if f > 1_000_000_000_000:
            return datetime.fromtimestamp(f / 1000.0, tz=timezone.utc)
        if f > 1_000_000_000:
            return datetime.fromtimestamp(f, tz=timezone.utc)
    except (ValueError, OSError):
        pass
    if s.isdigit():
        try:
            n = int(s)
            if n > 1_000_000_000_000:
                return datetime.fromtimestamp(n / 1000, tz=timezone.utc)
            return datetime.fromtimestamp(n, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _image_data_url_from_response(resp: requests.Response) -> str | None:
    ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if resp.content and ct.startswith("image/"):
        b64 = base64.b64encode(resp.content).decode("ascii")
        return f"data:{ct};base64,{b64}"
    try:
        payload = resp.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    img = _webhook_value(payload, "image", "thumbnail", "snapshot", "frame")
    if not img:
        return None
    if img.startswith("data:image/"):
        return img
    return f"data:image/jpeg;base64,{img}"


def _jpeg_dimensions(raw: bytes) -> tuple[int, int]:
    i = 2
    n = len(raw)
    while i + 9 < n:
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = (raw[i + 5] << 8) + raw[i + 6]
            w = (raw[i + 7] << 8) + raw[i + 8]
            return w, h
        if marker in (0xD8, 0xD9):
            i += 2
            continue
        seg_len = (raw[i + 2] << 8) + raw[i + 3]
        i += 2 + seg_len
    return 0, 0


def _render_playback_url(template: str, camera_id: str, when_utc: datetime, source_id: str) -> str:
    ts = when_utc.astimezone(timezone.utc)
    ts_iso = ts.isoformat().replace("+00:00", "Z")
    ts_s = int(ts.timestamp())
    ts_ms = int(ts.timestamp() * 1000)
    return template.format(
        camera_id=camera_id,
        source_id=source_id,
        time_iso=ts_iso,
        time_s=ts_s,
        time_ms=ts_ms,
        host=HOST,
    )


def _extract_playback_frame_data_url(
    camera_id: str,
    when_utc: datetime,
    source_id: str,
    debug_trace: list[dict[str, Any]] | None = None,
) -> str | None:
    if not PLAYBACK_STREAM_URL_TEMPLATE:
        if debug_trace is not None:
            debug_trace.append(
                {
                    "step": "playback-stream-skip",
                    "reason": "ALTA_PLAYBACK_STREAM_URL_TEMPLATE not set",
                }
            )
        return None
    try:
        playback_url = _render_playback_url(PLAYBACK_STREAM_URL_TEMPLATE, camera_id, when_utc, source_id)
    except Exception as ex:
        if debug_trace is not None:
            debug_trace.append({"step": "playback-stream-template-error", "error": str(ex)})
        return None

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        playback_url,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    if debug_trace is not None:
        debug_trace.append({"step": "playback-stream-start", "url": playback_url, "command": cmd})
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=20, check=False)
    except Exception as ex:
        if debug_trace is not None:
            debug_trace.append({"step": "playback-stream-error", "error": str(ex), "url": playback_url})
        return None
    if proc.returncode != 0 or not proc.stdout:
        if debug_trace is not None:
            debug_trace.append(
                {
                    "step": "playback-stream-failed",
                    "url": playback_url,
                    "returncode": proc.returncode,
                    "stderr": (proc.stderr or b"")[:2000].decode("utf-8", errors="replace"),
                }
            )
        return None
    w, h = _jpeg_dimensions(proc.stdout)
    if debug_trace is not None:
        debug_trace.append(
            {
                "step": "playback-stream-frame",
                "url": playback_url,
                "bytes": len(proc.stdout),
                "width": w,
                "height": h,
            }
        )
    b64 = base64.b64encode(proc.stdout).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _extract_first_str(node: Any, keys: tuple[str, ...]) -> str:
    wanted = {k.lower() for k in keys}
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() in wanted and isinstance(v, str) and v.strip():
                    return v.strip()
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return ""


def _resolve_camera_site_and_source(session: requests.Session, host: str, camera_id: str) -> tuple[str, str]:
    site_id = ""
    source_id = LIVE_SNAPSHOT_SOURCE_ID
    apply_alta_session_defaults(session)
    try:
        dev_resp = session.get(
            f"{host.rstrip('/')}/api/v2/devices/{camera_id}",
            timeout=LIVE_SNAPSHOT_HTTP_TIMEOUT_SECONDS,
        )
        if dev_resp.status_code == 200 and dev_resp.content:
            dev_json = dev_resp.json()
            site_id = _extract_first_str(
                dev_json,
                ("server_group_id", "serverGroupId", "site_id", "siteId", "group_id"),
            )
            src = _extract_first_str(dev_json, ("source_id", "sourceId"))
            if src:
                source_id = src
    except Exception:
        pass
    return site_id, source_id


def _fetch_live_snapshot_data_url(session: requests.Session, host: str, camera_id: str) -> tuple[str | None, dict[str, Any]]:
    site_id, source_id = _resolve_camera_site_and_source(session, host, camera_id)
    tries: list[dict[str, Any]] = []
    urls: list[str] = []
    base = host.rstrip("/")
    source_candidates = [source_id, "1", "2", "3", "4"]
    seen_sources: set[str] = set()
    normalized_sources: list[str] = []
    for src in source_candidates:
        s = str(src or "").strip()
        if not s or s in seen_sources:
            continue
        seen_sources.add(s)
        normalized_sources.append(s)
    if site_id:
        for src in normalized_sources:
            urls.append(f"{base}/api/v1/sites/{site_id}/devices/{camera_id}/sources/{src}/currentThumbnail")
    for src in normalized_sources:
        urls.append(f"{base}/api/v1/devices/{camera_id}/sources/{src}/currentThumbnail")

    for url in urls:
        apply_alta_session_defaults(session)
        try:
            resp = session.get(url, timeout=LIVE_SNAPSHOT_HTTP_TIMEOUT_SECONDS)
        except requests.RequestException as ex:
            tries.append({"url": url, "status": "request-error", "error": str(ex)})
            continue
        ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        tries.append({"url": url, "status": resp.status_code, "content_type": ct, "bytes": len(resp.content or b"")})
        if resp.status_code >= 400:
            continue
        data_url = _image_data_url_from_response(resp)
        if data_url:
            return data_url, {"tries": tries, "site_id": site_id, "source_id": source_id, "url": url}
    return None, {"tries": tries, "site_id": site_id, "source_id": source_id}


def _prune_snapshot_cache(now_utc: datetime) -> None:
    cutoff = now_utc - timedelta(seconds=LIVE_SNAPSHOT_MAX_AGE_SECONDS)
    remove: list[str] = []
    for camera_id, rows in _LIVE_SNAPSHOT_CACHE.items():
        kept = [r for r in rows if isinstance(r.get("captured_at_dt"), datetime) and r["captured_at_dt"] >= cutoff]
        if kept:
            _LIVE_SNAPSHOT_CACHE[camera_id] = kept
        else:
            remove.append(camera_id)
    for camera_id in remove:
        _LIVE_SNAPSHOT_CACHE.pop(camera_id, None)


def _capture_live_snapshot_for_camera(camera_id: str) -> None:
    if not HOST or not USERNAME or not PASSWORD or not camera_id:
        return
    session = requests.Session()
    print(
        f"[live-snapshot] camera={camera_id} capture attempt start",
        file=sys.stderr,
        flush=True,
    )
    try:
        apply_alta_session_defaults(session)
        login_url = HOST.rstrip("/") + "/api/v1/dologin"
        login_body = {"username": USERNAME, "password": PASSWORD}
        login_resp = session.post(login_url, json=login_body, timeout=LIVE_SNAPSHOT_HTTP_TIMEOUT_SECONDS)
        login_resp.raise_for_status()
        data_url, detail = _fetch_live_snapshot_data_url(session, HOST, camera_id)
    except Exception:
        print(
            f"[live-snapshot] camera={camera_id} login/fetch exception",
            file=sys.stderr,
            flush=True,
        )
        return
    tries_json = json.dumps(detail.get("tries", []), default=str)
    if not data_url:
        print(
            "[live-snapshot] camera="
            + camera_id
            + " no image returned; tries="
            + tries_json,
            file=sys.stderr,
            flush=True,
        )
        return
    now_utc = datetime.now(timezone.utc)
    raw, _mime = _bytes_from_thumbnail_data_url(data_url)
    w, h = _jpeg_dimensions(raw or b"")
    print(
        f"[live-snapshot] camera={camera_id} captured_at={now_utc.isoformat()} "
        f"url={detail.get('url','')} bytes={len(raw or b'')} resolution={w}x{h}",
        file=sys.stderr,
        flush=True,
    )
    print(
        f"[live-snapshot] camera={camera_id} request tries={tries_json}",
        file=sys.stderr,
        flush=True,
    )
    row = {
        "captured_at": now_utc.isoformat(),
        "captured_at_dt": now_utc,
        "image_data_url": data_url,
        "detail": detail,
    }
    with _LIVE_SNAPSHOT_LOCK:
        rows = _LIVE_SNAPSHOT_CACHE.setdefault(camera_id, [])
        rows.append(row)
        rows.sort(key=lambda r: r.get("captured_at_dt") or now_utc, reverse=True)
        _prune_snapshot_cache(now_utc)
        _LIVE_SNAPSHOT_CACHE[camera_id] = rows[:12]
        print(
            f"[live-snapshot] camera={camera_id} cache depth={len(_LIVE_SNAPSHOT_CACHE[camera_id])}",
            file=sys.stderr,
            flush=True,
        )


def _live_snapshot_worker() -> None:
    print(
        "[live-snapshot] worker started",
        file=sys.stderr,
        flush=True,
    )
    while True:
        with _LIVE_SNAPSHOT_LOCK:
            cameras = list(_MONITORED_CAMERAS)
        print(
            f"[live-snapshot] worker tick monitored={len(cameras)} cameras={cameras}",
            file=sys.stderr,
            flush=True,
        )
        if not cameras:
            print(
                "[live-snapshot] idle: no monitored cameras yet",
                file=sys.stderr,
                flush=True,
            )
        for camera_id in cameras:
            _capture_live_snapshot_for_camera(camera_id)
        sleep_s = LIVE_SNAPSHOT_INTERVAL_SECONDS if LIVE_SNAPSHOT_INTERVAL_SECONDS > 0 else 5.0
        import time

        time.sleep(sleep_s)


def _warm_live_snapshot_camera(camera_id: str, rounds: int = 3, delay_s: float = 1.0) -> None:
    import time
    for _ in range(max(0, rounds)):
        _capture_live_snapshot_for_camera(camera_id)
        if delay_s > 0:
            time.sleep(delay_s)


def _get_recent_live_snapshots(camera_id: str) -> list[dict[str, Any]]:
    now_utc = datetime.now(timezone.utc)
    with _LIVE_SNAPSHOT_LOCK:
        _prune_snapshot_cache(now_utc)
        rows = list(_LIVE_SNAPSHOT_CACHE.get(camera_id, []))
    rows.sort(key=lambda r: r.get("captured_at_dt") or now_utc, reverse=True)
    return rows


def _query_devices_rows() -> list[dict[str, Any]]:
    if not HOST or not USERNAME or not PASSWORD:
        return []
    session = requests.Session()
    try:
        do_login(session, HOST, USERNAME, PASSWORD)
    except Exception:
        return []
    # Mirror known-working script behavior first: /api/v1/query:devices with empty payload.
    # Fall back to v2 and active-only payload if needed.
    query_attempts: tuple[tuple[str, dict[str, Any]], ...] = (
        ("/api/v1/query:devices", {}),
        ("/api/v2/query:devices", {}),
        ("/api/v1/query:devices", {"active": True}),
        ("/api/v2/query:devices", {"active": True}),
    )
    for path, payload in query_attempts:
        url = HOST.rstrip("/") + path
        try:
            apply_alta_session_defaults(session)
            resp = session.post(url, json=payload, timeout=60)
        except requests.RequestException:
            continue
        if resp.status_code >= 400 or not resp.content:
            continue
        try:
            j = resp.json()
        except Exception:
            continue
        if isinstance(j, list):
            return [r for r in j if isinstance(r, dict)]
        if isinstance(j, dict):
            rows = j.get("devices") or j.get("items") or j.get("data") or []
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    return []


def _list_cameras_for_ui() -> list[dict[str, str]]:
    rows = _query_devices_rows()
    out: list[dict[str, str]] = []
    for row in rows:
        # Mirror list_cameras.py online filter: active and live.status == CONNECTED.
        if not bool(row.get("active", False)):
            continue
        live = row.get("live") if isinstance(row.get("live"), dict) else {}
        status = str(live.get("status") or "").strip().upper()
        if status != "CONNECTED":
            continue
        dev_id = str(
            row.get("guid")
            or row.get("id")
            or row.get("device_id")
            or row.get("deviceId")
            or ""
        ).strip()
        if not dev_id:
            continue
        name = str(row.get("name") or row.get("title") or row.get("display_name") or dev_id).strip()
        out.append({"id": dev_id, "name": name})
    if out:
        return out
    with _LIVE_SNAPSHOT_LOCK:
        monitored = sorted(_MONITORED_CAMERAS)
    return [{"id": c, "name": c} for c in monitored]


def _camera_picker_sections() -> list[dict[str, Any]]:
    rows = _query_devices_rows()
    cameras: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("active", False)):
            continue
        live = row.get("live") if isinstance(row.get("live"), dict) else {}
        status = str(live.get("status") or "").strip().upper()
        if status != "CONNECTED":
            continue
        dev_id = str(
            row.get("guid")
            or row.get("id")
            or row.get("device_id")
            or row.get("deviceId")
            or ""
        ).strip()
        if not dev_id:
            continue
        name = str(row.get("name") or row.get("title") or row.get("display_name") or dev_id).strip()
        site = _extract_first_str(row, ("site_name", "siteName", "server_group_name", "serverGroupName", "site_id", "siteId"))
        group = _extract_first_str(row, ("group_name", "groupName", "device_group_name", "deviceGroupName"))
        cameras.append(
            {
                "id": dev_id,
                "name": name,
                "site": site or "Site",
                "group": group or "Group",
                "status": status,
            }
        )

    if not cameras:
        return []

    snapshots_by_id: dict[str, str] = {}
    site_by_id: dict[str, str] = {}
    source_by_id: dict[str, str] = {}
    if HOST and USERNAME and PASSWORD:
        session = requests.Session()
        try:
            do_login(session, HOST, USERNAME, PASSWORD)
            for cam in cameras:
                data_url, detail = _fetch_live_snapshot_data_url(session, HOST, cam["id"])
                snapshots_by_id[cam["id"]] = data_url or ""
                site_by_id[cam["id"]] = str(detail.get("site_id") or "").strip()
                source_by_id[cam["id"]] = str(detail.get("source_id") or "").strip()
        except Exception:
            pass

    sections: dict[str, dict[str, Any]] = {}
    for cam in cameras:
        image_data_url = snapshots_by_id.get(cam["id"], "")
        # Camera picker should only show online cameras that currently return a snapshot.
        if not image_data_url:
            continue
        # Resolve site/source from device snapshot metadata when query:devices lacks labels.
        if (not cam.get("site") or cam.get("site") == "Site") and site_by_id.get(cam["id"]):
            cam["site"] = f"Site {site_by_id[cam['id']]}"
        if (not cam.get("group") or cam.get("group") == "Group") and source_by_id.get(cam["id"]):
            cam["group"] = f"Source {source_by_id[cam['id']]}"
        title = f"{cam['site']} / {cam['group']}"
        bucket = sections.setdefault(title, {"title": title, "site": cam["site"], "group": cam["group"], "cameras": []})
        bucket["cameras"].append(
            {
                "id": cam["id"],
                "name": cam["name"],
                "status": cam["status"],
                "image_data_url": image_data_url,
            }
        )

    out = [sec for sec in sections.values() if sec.get("cameras")]
    out.sort(key=lambda s: (str(s.get("site") or ""), str(s.get("group") or ""), str(s.get("title") or "")))
    for sec in out:
        rows2 = sec.get("cameras") or []
        rows2.sort(key=lambda r: str(r.get("name") or ""))
    return out


def _discover_live_snapshot_cameras() -> list[str]:
    """Return explicitly configured startup cameras only."""
    if LIVE_SNAPSHOT_CAMERAS:
        return [c for c in LIVE_SNAPSHOT_CAMERAS if c]
    return []


def _fetch_camera_snapshot_data_url(
    session: requests.Session,
    host: str,
    camera_id: str,
    when_utc: datetime,
    debug_trace: list[dict[str, Any]] | None = None,
) -> str | None:
    base = host.rstrip("/")
    ts_iso = when_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    ts_epoch_ms = str(int(when_utc.timestamp() * 1000))

    site_id, source_id = _resolve_camera_site_and_source(session, host, camera_id)
    if debug_trace is not None:
        debug_trace.append({"step": "resolve-device-result", "site_id": site_id, "source_id": source_id})

    candidate_times = (
        f"{base}/api/v1/sites/{site_id}/devices/{camera_id}/sources/{source_id}/thumbnail",
        f"{base}/api/v1/sites/{site_id}/devices/{camera_id}/sources/1/thumbnail",
    ) if site_id else tuple()

    resolved_time_ms = ts_epoch_ms
    resolved_candidate = "0"
    # Ask Alta playback where this timestamp maps on the timeline.
    for src in (source_id, "1"):
        p_url = f"{base}/api/v1/devices/{camera_id}/sources/{src}/playbackLocation"
        p_body = {"start_time": ts_iso, "prefer_backup": False}
        apply_alta_session_defaults(session)
        try:
            p_resp = session.post(p_url, json=p_body, timeout=60)
        except requests.RequestException:
            if debug_trace is not None:
                debug_trace.append(
                    {
                        "step": "playback-location-error",
                        "method": "POST",
                        "url": p_url,
                        "json": p_body,
                    }
                )
            continue
        p_ct = (p_resp.headers.get("Content-Type") or "").split(";")[0].strip()
        if debug_trace is not None:
            debug_trace.append(
                {
                    "step": "playback-location",
                    "method": "POST",
                    "url": p_url,
                    "json": p_body,
                    "status": p_resp.status_code,
                    "content_type": p_ct,
                    "bytes": len(p_resp.content or b""),
                }
            )
        if p_resp.status_code >= 400 or not p_resp.content:
            continue
        try:
            p_json = p_resp.json()
        except Exception:
            continue
        if isinstance(p_json, dict):
            # HAR shows response like {"location":"direct","time":"2026-...Z"}
            p_time = str(p_json.get("time") or "").strip()
            dt = _parse_webhook_time(p_time)
            if dt is not None:
                resolved_time_ms = str(int(dt.timestamp() * 1000))
            loc = str(p_json.get("location") or "").strip()
            if loc and loc.lower() != "direct":
                loc_dt = _parse_webhook_time(loc)
                if loc_dt is not None:
                    resolved_candidate = str(int(loc_dt.timestamp() * 1000))
                else:
                    resolved_candidate = loc
            break

    for url in candidate_times:
        for params in (
            {"time": resolved_time_ms},
            {"time": resolved_time_ms, "candidate": resolved_candidate},
            {"time": resolved_time_ms, "candidate": "0"},
        ):
            apply_alta_session_defaults(session)
            try:
                resp = session.get(url, params=params, timeout=60)
            except requests.RequestException:
                if debug_trace is not None:
                    debug_trace.append(
                        {
                            "step": "thumbnail-request-error",
                            "method": "GET",
                            "url": url,
                            "params": params,
                        }
                    )
                continue
            if debug_trace is not None:
                debug_trace.append(
                    {
                        "step": "thumbnail-request",
                        "method": "GET",
                        "url": url,
                        "params": params,
                        "status": resp.status_code,
                        "content_type": (resp.headers.get("Content-Type") or "").split(";")[0].strip(),
                        "bytes": len(resp.content or b""),
                    }
                )
            if resp.status_code >= 400:
                continue
            data_url = _image_data_url_from_response(resp)
            if data_url:
                if debug_trace is not None:
                    debug_trace.append(
                        {
                            "step": "thumbnail-hit",
                            "url": url,
                            "params": params,
                        }
                    )
                return data_url

    # Fallbacks for deployments exposing non-site-based endpoints.
    for path in (
        f"/api/v1/devices/{camera_id}/sources/{source_id}/thumbnail",
        f"/api/v1/devices/{camera_id}/sources/1/thumbnail",
        f"/api/v1/devices/{camera_id}/sources/{source_id}/playbackLocation",
    ):
        for params in (
            {"time": ts_epoch_ms},
            {"time": ts_iso},
            {"start_time": ts_iso, "prefer_backup": "false"},
        ):
            apply_alta_session_defaults(session)
            try:
                resp = session.get(base + path, params=params, timeout=60)
            except requests.RequestException:
                if debug_trace is not None:
                    debug_trace.append(
                        {
                            "step": "fallback-request-error",
                            "method": "GET",
                            "url": base + path,
                            "params": params,
                        }
                    )
                continue
            if debug_trace is not None:
                debug_trace.append(
                    {
                        "step": "fallback-request",
                        "method": "GET",
                        "url": base + path,
                        "params": params,
                        "status": resp.status_code,
                        "content_type": (resp.headers.get("Content-Type") or "").split(";")[0].strip(),
                        "bytes": len(resp.content or b""),
                    }
                )
            if resp.status_code >= 400:
                continue
            data_url = _image_data_url_from_response(resp)
            if data_url:
                if debug_trace is not None:
                    debug_trace.append(
                        {
                            "step": "fallback-hit",
                            "url": base + path,
                            "params": params,
                        }
                    )
                return data_url
    if debug_trace is not None:
        debug_trace.append({"step": "no-snapshot-found"})
    return None


def _build_camera_webhook_payload(body: dict[str, Any]) -> dict[str, Any]:
    rule_name = str(body.get("name") or "").strip()
    camera_id = str(body.get("camera") or "").strip()
    time_raw = str(body.get("time") or "").strip()
    trigger_dt = _parse_webhook_time(time_raw)
    trigger_iso = trigger_dt.isoformat() if trigger_dt else ""

    print(
        f"[webhook:/webhook] params name={rule_name!r} camera={camera_id!r} time={time_raw!r}",
        file=sys.stderr,
        flush=True,
    )

    snapshots: list[dict[str, Any]] = []
    debug_trace: list[dict[str, Any]] = [
        {"step": "incoming-body", "body": body},
        {
            "step": "parsed-fields",
            "rule_name": rule_name,
            "camera_id": camera_id,
            "time_raw": time_raw,
            "trigger_iso": trigger_iso,
        },
    ]
    if HOST and USERNAME and PASSWORD and camera_id and trigger_dt is not None:
        debug_trace.append(
            {
                "step": "single-snapshot-fetch",
                "camera_id": camera_id,
                "note": "Fetching one snapshot for the webhook trigger time.",
            }
        )
        session = requests.Session()
        try:
            do_login(session, HOST, USERNAME, PASSWORD)
            debug_trace.append({"step": "login-ok"})
            per_snapshot_trace: list[dict[str, Any]] = []
            # Preferred path for VLM-quality frames: ffmpeg extraction from playback stream URL template.
            data_url = _extract_playback_frame_data_url(
                camera_id,
                trigger_dt,
                "1",
                per_snapshot_trace,
            )
            if not data_url:
                data_url = _fetch_camera_snapshot_data_url(
                    session, HOST, camera_id, trigger_dt, per_snapshot_trace
                )
            snapshots.append(
                {
                    "label": "T",
                    "offset_seconds": 0,
                    "requested_at": trigger_dt.isoformat(),
                    "image_data_url": data_url or "",
                    "found": bool(data_url),
                    "debug": per_snapshot_trace,
                }
            )
        except Exception as ex:
            debug_trace.append({"step": "login-or-fetch-error", "error": str(ex)})
            snapshots = [
                {
                    "label": "T",
                    "offset_seconds": 0,
                    "requested_at": trigger_dt.isoformat(),
                    "image_data_url": "",
                    "error": str(ex),
                    "found": False,
                }
            ]
    else:
        debug_trace.append(
            {
                "step": "precondition-failed",
                "has_host": bool(HOST),
                "has_username": bool(USERNAME),
                "has_password": bool(PASSWORD),
                "camera_id": camera_id,
                "trigger_time_parsed": bool(trigger_dt is not None),
            }
        )
        snapshots = [
            {
                "label": "T",
                "offset_seconds": 0,
                "requested_at": trigger_dt.isoformat() if trigger_dt else "",
                "image_data_url": "",
                "error": "Missing camera/time or Alta credentials not configured.",
                "found": False,
            }
        ]

    matched_alert = _find_alert_by_name(rule_name)
    matched_alert_id = matched_alert.get("id", "") if matched_alert else ""
    matched_alert_name = matched_alert.get("name", "") if matched_alert else ""
    matched_alert_prompt = matched_alert.get("prompt", "") if matched_alert else ""
    if matched_alert:
        debug_trace.append(
            {
                "step": "matched-ai-alert",
                "alert_id": matched_alert_id,
                "alert_name": matched_alert_name,
            }
        )
    else:
        debug_trace.append({"step": "matched-ai-alert", "result": "none"})

    vlm_analysis = ""
    vlm_error = ""
    vlm_image_data_urls: list[str] = []
    if matched_alert:
        vlm_image_data_urls = _snapshot_data_urls(snapshots)
        vlm_analysis, vlm_error = _call_cloud_vlm(matched_alert_prompt, vlm_image_data_urls)
        debug_trace.append(
            {
                "step": "vlm-analysis",
                "images_sent": len(vlm_image_data_urls),
                "analysis_ok": bool(vlm_analysis),
                "error": vlm_error,
            }
        )

    has_any = bool(rule_name or camera_id or trigger_iso or any(s.get("image_data_url") for s in snapshots))
    out = {
        "ok": True,
        "has_warning": has_any,
        "event": rule_name or "Camera rule triggered",
        "patient_name": rule_name or "Camera rule",
        "patient_id": "",
        "location": camera_id or "Unknown camera",
        "camera": camera_id,
        "trigger_time": trigger_iso or time_raw,
        "image_data_url": snapshots[0].get("image_data_url", "") if snapshots else "",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "snapshots": snapshots,
        "debug_trace": debug_trace,
        "matched_alert_id": matched_alert_id,
        "matched_alert_name": matched_alert_name,
        "vlm_analysis": vlm_analysis,
        "vlm_error": vlm_error,
        "vlm_image_data_urls": vlm_image_data_urls,
    }
    print(
        "[webhook:/webhook] snapshot debug:\n" + json.dumps(out.get("debug_trace", []), indent=2, default=str),
        file=sys.stderr,
        flush=True,
    )
    return out


def _to_camera_webhook_history_row(payload: dict[str, Any]) -> dict[str, Any]:
    vlm_image_data_urls = payload.get("vlm_image_data_urls")
    ai_thumbnail_data_url = ""
    if isinstance(vlm_image_data_urls, list):
        for v in vlm_image_data_urls:
            s = str(v or "").strip()
            if s:
                ai_thumbnail_data_url = s
                break
    snapshots = payload.get("snapshots")
    image_data_url = str(payload.get("image_data_url") or "").strip()
    if not image_data_url and isinstance(snapshots, list):
        for row in snapshots:
            if not isinstance(row, dict):
                continue
            v = str(row.get("image_data_url") or "").strip()
            if v:
                image_data_url = v
                break
    rule_name = str(payload.get("patient_name") or payload.get("event") or "Unknown rule").strip()
    ai_response = str(payload.get("vlm_analysis") or "").strip()
    ai_error = str(payload.get("vlm_error") or "").strip()
    if not ai_response and ai_error:
        ai_response = f"Error: {ai_error}"
    return {
        "id": uuid4().hex,
        "rule_name": rule_name,
        "camera": str(payload.get("camera") or payload.get("location") or "").strip(),
        "trigger_time": str(payload.get("trigger_time") or "").strip(),
        "received_at": str(payload.get("received_at") or datetime.now(timezone.utc).isoformat()),
        "thumbnail_data_url": ai_thumbnail_data_url or image_data_url,
        "ai_response": ai_response,
        "matched_alert_name": str(payload.get("matched_alert_name") or "").strip(),
    }


def resolve_profile_thumbnail_bytes(
    session: requests.Session, host: str, profile_id: str, roster: dict
) -> tuple[bytes | None, str | None]:
    """Resolve raw image bytes for one profile using roster context."""
    prow = roster.get("id_to_profile", {}).get(profile_id)
    tpl = roster.get("face_photos", {}).get(profile_id)
    if tpl:
        raw, mime = tpl
        if raw and mime and len(raw) <= 400_000:
            return raw, mime
    data_url = _profile_thumbnail_data_url(session, host, profile_id, prow)
    return _bytes_from_thumbnail_data_url(data_url or "")


def collect_patients_profile_rows(
    session: requests.Session, host: str, watchlist_name: str | None
) -> dict:
    """List profiles on the watchlist (id + name only). Thumbnails: GET /api/profile/<id>/thumbnail."""
    roster = patient_roster_data(session, host, watchlist_name)
    if not roster.get("ok"):
        return {
            "ok": False,
            "error": roster.get("error", "Unknown error"),
            "profiles": [],
        }

    profiles = [
        {"id": pid, "name": roster["id_to_name"].get(pid, pid)}
        for pid in sorted(roster["pids_set"], key=lambda x: (len(x), x))
    ]
    return {
        "ok": True,
        "watchlist_id": roster["watchlist_id"],
        "watchlist_name": roster["watchlist_name"],
        "profiles": profiles,
        "profiles_endpoint": roster.get("profiles_endpoint"),
    }


def generate_face(session: requests.Session, host: str, image_bytes: bytes, fmt: str) -> dict | None:
    url = host.rstrip("/") + "/api/v1/watchlistsProfilesFaces/generate"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data = aware_post(session, url, {"image": b64, "format": fmt})
    if isinstance(data, list) and len(data) > 0:
        return data[0]
    return data if isinstance(data, dict) else None


def create_profile(session: requests.Session, host: str, profile_name: str, watchlist_ids: list) -> dict | None:
    url = host.rstrip("/") + "/api/v1/watchlistsProfiles"
    try:
        return aware_post(session, url, {"name": profile_name, "watchlists": watchlist_ids})
    except requests.HTTPError:
        return None


def add_face_to_profile(session: requests.Session, host: str, profile_id: str, face_data: dict) -> bool:
    url = host.rstrip("/") + "/api/v1/watchlistsProfilesFaces"
    payload = dict(face_data)
    payload["profile_id"] = profile_id
    if "embedding_version" not in payload:
        payload["embedding_version"] = "5"
    if "image_format" not in payload:
        payload["image_format"] = ""
    if "low_face_quality" not in payload:
        payload["low_face_quality"] = False
    if "quality" not in payload:
        payload["quality"] = float(payload.get("acceptability", 0.5))
    if "source_image" not in payload:
        payload["source_image"] = ""
    try:
        aware_post(session, url, payload)
        return True
    except requests.HTTPError:
        return False


def enroll(profile_name: str, image_data_url: str, watchlist_name: str | None) -> dict:
    if not HOST or not USERNAME or not PASSWORD:
        return {"ok": False, "error": "Server missing ALTA_HOST, ALTA_USERNAME, or ALTA_PASSWORD in environment."}

    try:
        raw, fmt = parse_data_url(image_data_url)
    except Exception as e:
        return {"ok": False, "error": f"Invalid image payload: {e}"}

    wl_query = (watchlist_name or DEFAULT_WATCHLIST or "patients").strip().lower()
    session = requests.Session()
    do_login(session, HOST, USERNAME, PASSWORD)

    _endpoint, data = list_face_watchlists(session, HOST)
    watchlists = _watchlists_from_response(data or [])
    wl_id, wl_display = find_watchlist_id(watchlists, wl_query)
    if not wl_id:
        names = [w.get("name") or w.get("title") or "?" for w in watchlists[:20]]
        return {
            "ok": False,
            "error": f"No watchlist named '{wl_query}' (case-insensitive). Found sample names: {names}",
        }

    face = generate_face(session, HOST, raw, fmt)
    if not face or "embedding" not in face:
        return {"ok": False, "error": "No face detected in image or generate API returned an unexpected response."}

    profile = create_profile(session, HOST, profile_name, [wl_id])
    if not profile or not profile.get("id"):
        return {"ok": False, "error": "Create profile failed or returned no id."}

    if not add_face_to_profile(session, HOST, str(profile["id"]), face):
        return {"ok": False, "error": "Adding face to profile failed."}

    return {
        "ok": True,
        "profile_id": str(profile.get("id")),
        "profile_name": profile_name,
        "watchlist_id": wl_id,
        "watchlist_name": wl_display,
    }


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify(
        {
            "ok": True,
            "alta_configured": bool(HOST and USERNAME and PASSWORD),
            "default_watchlist": DEFAULT_WATCHLIST or "patients",
        }
    )


@app.route("/", methods=["GET", "HEAD"])
def root():
    return jsonify({"ok": True, "service": "miniapps-proxy"})


def _webhook_debug_log() -> None:
    """Log incoming webhook POST to stderr (JSON pretty-print or raw body preview)."""
    print(f"[webhook] POST {request.path}", file=sys.stderr, flush=True)
    print(f"[webhook] Content-Type: {request.content_type!r}", file=sys.stderr, flush=True)
    raw = request.get_data(cache=True, as_text=False)
    parsed: Any = None
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            parsed = None
    if isinstance(parsed, (dict, list)):
        try:
            print(
                "[webhook] JSON payload:\n" + json.dumps(parsed, indent=2, default=str),
                file=sys.stderr,
                flush=True,
            )
        except (TypeError, ValueError):
            print(f"[webhook] JSON payload (repr): {parsed!r}", file=sys.stderr, flush=True)
    elif parsed is not None:
        print(f"[webhook] JSON payload (scalar): {parsed!r}", file=sys.stderr, flush=True)
    else:
        preview = raw[:8000].decode("utf-8", errors="replace") if raw else ""
        print(
            f"[webhook] body not JSON or empty (bytes={len(raw)}), preview:\n{preview}",
            file=sys.stderr,
            flush=True,
        )


def _coerce_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on", "enter", "entering", "in"}


def _format_elapsed_seconds(total_seconds: float) -> str:
    total_seconds = max(0.0, float(total_seconds))
    mins, secs = divmod(total_seconds, 60.0)
    if mins >= 1:
        return f"{int(mins)}m {secs:06.3f}s"
    return f"{secs:.3f}s"


@app.route("/api/webhook", methods=["POST"])
def api_webhook():
    """Accept webhook POSTs on /api/webhook."""
    _webhook_debug_log()
    raw = request.get_data(cache=True, as_text=False)
    parsed: Any = None
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            parsed = None
    if isinstance(parsed, dict):
        normalized = _normalize_webhook(parsed)
        with _WEBHOOK_LOCK:
            _LATEST_WEBHOOK.update(normalized)
    return jsonify({"ok": True})


@app.route("/webhook", methods=["POST"])
def api_webhook_entry():
    _webhook_debug_log()
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "Expected JSON object payload."}), 400
    payload = _build_camera_webhook_payload(body)
    with _WEBHOOK_LOCK:
        _LATEST_CAMERA_WEBHOOK.clear()
        _LATEST_CAMERA_WEBHOOK.update(payload)
        _CAMERA_WEBHOOK_HISTORY.insert(0, _to_camera_webhook_history_row(payload))
        if len(_CAMERA_WEBHOOK_HISTORY) > _CAMERA_WEBHOOK_HISTORY_MAX:
            del _CAMERA_WEBHOOK_HISTORY[_CAMERA_WEBHOOK_HISTORY_MAX:]
    return jsonify({"ok": True, "has_warning": payload.get("has_warning", False)})


@app.route("/api/webhook-entry-latest", methods=["GET"])
def api_webhook_entry_latest():
    with _WEBHOOK_LOCK:
        return jsonify(dict(_LATEST_CAMERA_WEBHOOK))


@app.route("/api/webhook-entry-log", methods=["GET"])
def api_webhook_entry_log():
    with _WEBHOOK_LOCK:
        rows = list(_CAMERA_WEBHOOK_HISTORY)
    return jsonify({"ok": True, "rows": rows})


@app.route("/api/webhook-latest", methods=["GET"])
def api_webhook_latest():
    """Return latest normalized webhook warning for frontend display."""
    with _WEBHOOK_LOCK:
        return jsonify(dict(_LATEST_WEBHOOK))


@app.route("/api/webhook-clear", methods=["POST"])
def api_webhook_clear():
    """Clear latest webhook warning state."""
    with _WEBHOOK_LOCK:
        _LATEST_WEBHOOK.clear()
        _LATEST_WEBHOOK.update(dict(_EMPTY_WEBHOOK))
        return jsonify(dict(_LATEST_WEBHOOK))


@app.route("/api/webhook-entry-clear", methods=["POST"])
def api_webhook_entry_clear():
    """Clear latest /webhook entry-point state."""
    with _WEBHOOK_LOCK:
        _LATEST_CAMERA_WEBHOOK.clear()
        _LATEST_CAMERA_WEBHOOK.update(dict(_EMPTY_CAMERA_WEBHOOK))
        _CAMERA_WEBHOOK_HISTORY.clear()
        return jsonify({"ok": True, "rows": [], "latest": dict(_LATEST_CAMERA_WEBHOOK)})


@app.route("/api/webhook-car-crossing", methods=["POST"])
def api_webhook_car_crossing():
    _webhook_debug_log()
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "Expected JSON object payload."}), 400

    entering = _coerce_bool(body.get("entering"))
    leaving = _coerce_bool(body.get("leaving"))
    if entering == leaving:
        return jsonify({"ok": False, "error": "Exactly one of 'entering' or 'leaving' must be true."}), 400

    car_id = str(body.get("car_id") or body.get("vehicle_id") or body.get("id") or "").strip().lower() or "car"
    event_time_raw = str(body.get("event_time") or body.get("time") or body.get("timestamp") or "").strip()
    event_dt = _parse_webhook_time(event_time_raw) or datetime.now(timezone.utc)
    event_time = event_dt.isoformat()
    received_at = datetime.now(timezone.utc).isoformat()
    latest_payload = {
        "ok": True,
        "has_event": True,
        "car_id": car_id,
        "entering": entering,
        "leaving": leaving,
        "event_time": event_time,
        "received_at": received_at,
    }

    completed_row: dict[str, Any] | None = None
    with _WEBHOOK_LOCK:
        _LATEST_CAR_CROSSING_WEBHOOK.clear()
        _LATEST_CAR_CROSSING_WEBHOOK.update(latest_payload)
        if entering:
            _CAR_TIMING_PENDING[car_id] = event_time
        elif leaving:
            entry_time = _CAR_TIMING_PENDING.pop(car_id, "")
            if entry_time:
                duration_seconds = 0.0
                try:
                    entry_dt = datetime.fromisoformat(entry_time)
                    duration_seconds = max(0.0, (event_dt - entry_dt).total_seconds())
                except ValueError:
                    duration_seconds = 0.0
                completed_row = {
                    "car_id": car_id,
                    "entry_time": entry_time,
                    "exit_time": event_time,
                    "duration_seconds": round(duration_seconds, 3),
                    "duration_label": _format_elapsed_seconds(duration_seconds),
                }
                _CAR_TIMING_LOG.append(completed_row)
                if len(_CAR_TIMING_LOG) > 300:
                    del _CAR_TIMING_LOG[: len(_CAR_TIMING_LOG) - 300]
    return jsonify({"ok": True, "latest": latest_payload, "completed_row": completed_row})


@app.route("/api/car-crossing-latest", methods=["GET"])
def api_car_crossing_latest():
    with _WEBHOOK_LOCK:
        return jsonify(dict(_LATEST_CAR_CROSSING_WEBHOOK))


@app.route("/api/car-timing-log", methods=["GET"])
def api_car_timing_log():
    with _WEBHOOK_LOCK:
        rows = list(_CAR_TIMING_LOG)
        pending = [{"car_id": k, "entry_time": v} for k, v in _CAR_TIMING_PENDING.items()]
    return jsonify({"ok": True, "rows": rows, "pending": pending})


@app.route("/api/car-timing-clear", methods=["POST"])
def api_car_timing_clear():
    with _WEBHOOK_LOCK:
        _LATEST_CAR_CROSSING_WEBHOOK.clear()
        _LATEST_CAR_CROSSING_WEBHOOK.update(dict(_EMPTY_CAR_CROSSING_WEBHOOK))
        _CAR_TIMING_PENDING.clear()
        _CAR_TIMING_LOG.clear()
        return jsonify({"ok": True})


@app.route("/api/cameras", methods=["GET"])
def api_cameras():
    cameras = _list_cameras_for_ui()
    with _LIVE_SNAPSHOT_LOCK:
        monitored = sorted(_MONITORED_CAMERAS)
    return jsonify({"ok": True, "cameras": cameras, "monitored": monitored})


@app.route("/api/camera-picker", methods=["GET"])
def api_camera_picker():
    sections = _camera_picker_sections()
    return jsonify({"ok": True, "sections": sections})


@app.route("/api/live-snapshot/select-camera", methods=["POST"])
def api_live_snapshot_select_camera():
    body = request.get_json(silent=True) or {}
    camera_id = str(body.get("camera_id") or "").strip()
    if not camera_id:
        return jsonify({"ok": False, "error": "camera_id is required"}), 400
    with _LIVE_SNAPSHOT_LOCK:
        # UI selects one camera source; keep polling focused on that camera.
        _MONITORED_CAMERAS.clear()
        _MONITORED_CAMERAS.add(camera_id)
    # Prime cache in background so API response is immediate even on slow cameras.
    Thread(target=_warm_live_snapshot_camera, args=(camera_id, 3, 1.0), daemon=True).start()
    rows = _get_recent_live_snapshots(camera_id)
    return jsonify({"ok": True, "camera_id": camera_id, "cached_snapshots": len(rows)})


@app.route("/api/live-snapshot/stop", methods=["POST"])
def api_live_snapshot_stop():
    with _LIVE_SNAPSHOT_LOCK:
        _MONITORED_CAMERAS.clear()
        _LIVE_SNAPSHOT_CACHE.clear()
    return jsonify({"ok": True})


@app.route("/api/ai-alerts", methods=["GET"])
def api_ai_alerts_list():
    with _AI_ALERTS_LOCK:
        rows = _load_ai_alerts()
    # Do not return prompt bodies in the list response.
    safe_rows = [{"id": r["id"], "name": r["name"], "created_at": r.get("created_at", "")} for r in rows]
    return jsonify({"ok": True, "alerts": safe_rows})


@app.route("/api/ai-alerts", methods=["POST"])
def api_ai_alerts_create():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name is required."}), 400
    if not prompt:
        return jsonify({"ok": False, "error": "prompt is required."}), 400
    new_row = {
        "id": uuid4().hex,
        "name": name,
        "prompt": prompt,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _AI_ALERTS_LOCK:
        rows = _load_ai_alerts()
        rows.append(new_row)
        _save_ai_alerts(rows)
    return jsonify({"ok": True, "alert": {"id": new_row["id"], "name": new_row["name"], "created_at": new_row["created_at"]}})


@app.route("/api/ai-alerts/<alert_id>", methods=["DELETE"])
def api_ai_alerts_delete(alert_id: str):
    alert_id = str(alert_id or "").strip()
    if not alert_id:
        return jsonify({"ok": False, "error": "alert_id is required."}), 400
    with _AI_ALERTS_LOCK:
        rows = _load_ai_alerts()
        kept = [r for r in rows if str(r.get("id") or "").strip() != alert_id]
        if len(kept) == len(rows):
            return jsonify({"ok": False, "error": "Alert not found."}), 404
        _save_ai_alerts(kept)
    return jsonify({"ok": True, "deleted_id": alert_id})


@app.route("/api/alta-dashboard", methods=["GET"])
def api_alta_dashboard():
    if not HOST or not USERNAME or not PASSWORD:
        return jsonify({"ok": False, "error": "Alta credentials are not configured."}), 422

    errors: list[str] = []
    session = requests.Session()
    try:
        do_login(session, HOST, USERNAME, PASSWORD)
    except Exception as ex:
        return jsonify({"ok": False, "error": f"Alta login failed: {ex}"}), 502

    devices: list[dict[str, Any]] = []
    rows = _query_devices_rows()
    total_devices = len(rows)
    active_devices = 0
    connected_devices = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        dev_id = str(row.get("guid") or row.get("id") or row.get("device_id") or row.get("deviceId") or "").strip()
        if not dev_id:
            continue
        name = str(row.get("name") or row.get("title") or row.get("display_name") or dev_id).strip()
        active = bool(row.get("active", False))
        live = row.get("live") if isinstance(row.get("live"), dict) else {}
        status = str(live.get("status") or "").strip().upper() or "UNKNOWN"
        if active:
            active_devices += 1
        if status == "CONNECTED":
            connected_devices += 1
        devices.append(
            {
                "id": dev_id,
                "name": name,
                "active": active,
                "status": status,
            }
        )

    watchlists_total = 0
    watchlists_sample: list[dict[str, str]] = []
    try:
        _endpoint, wl_data = list_face_watchlists(session, HOST)
        watchlists = _watchlists_from_response(wl_data or [])
        watchlists_total = len(watchlists)
        for wl in watchlists[:8]:
            if not isinstance(wl, dict):
                continue
            wid = str(wl.get("id") or wl.get("watchlistId") or wl.get("guid") or "").strip()
            name = str(wl.get("name") or wl.get("title") or wid or "Watchlist").strip()
            watchlists_sample.append({"id": wid, "name": name})
    except Exception as ex:
        errors.append(f"Could not list watchlists: {ex}")

    patients_total = 0
    try:
        patients = collect_patients_profile_rows(session, HOST, DEFAULT_WATCHLIST)
        if patients.get("ok"):
            patients_total = len(patients.get("profiles") or [])
        else:
            errors.append(str(patients.get("error") or "Could not load patients watchlist profiles."))
    except Exception as ex:
        errors.append(f"Could not load patients watchlist profiles: {ex}")

    data = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_devices": total_devices,
            "active_devices": active_devices,
            "connected_devices": connected_devices,
            "offline_or_unknown_devices": max(0, total_devices - connected_devices),
            "watchlists_total": watchlists_total,
            "patients_watchlist_profiles": patients_total,
        },
        "devices": devices,
        "watchlists_sample": watchlists_sample,
        "errors": errors,
    }
    return jsonify(data)


@app.route("/api/enroll", methods=["POST"])
def api_enroll():
    body = request.get_json(silent=True) or {}
    profile_name = (body.get("profile_name") or "").strip()
    image = body.get("image") or body.get("data_url") or ""
    watchlist_name = body.get("watchlist_name")

    if not profile_name:
        return jsonify({"ok": False, "error": "profile_name is required."}), 400
    if not image:
        return jsonify({"ok": False, "error": "image (base64 or data URL) is required."}), 400

    try:
        result = enroll(profile_name, str(image), watchlist_name)
    except requests.HTTPError as e:
        body, status = _http_error_payload(e)
        return jsonify(body), status
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    status = 200 if result.get("ok") else 422
    return jsonify(result), status


@app.route("/api/patients-profiles", methods=["GET"])
def api_patients_profiles():
    """List profiles on the configured patients watchlist (query ?watchlist=name optional)."""
    watchlist = request.args.get("watchlist") or DEFAULT_WATCHLIST or "patients"
    if not HOST or not USERNAME or not PASSWORD:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Server missing ALTA_HOST, ALTA_USERNAME, or ALTA_PASSWORD.",
                    "profiles": [],
                }
            ),
            503,
        )
    try:
        session = requests.Session()
        do_login(session, HOST, USERNAME, PASSWORD)
        result = collect_patients_profile_rows(session, HOST, watchlist)
    except requests.HTTPError as e:
        body, status = _http_error_payload(e)
        body["profiles"] = []
        return jsonify(body), status
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "profiles": []}), 500

    status = 200 if result.get("ok") else 422
    return jsonify(result), status


@app.route("/api/profile/<profile_id>/thumbnail", methods=["GET"])
def api_profile_thumbnail(profile_id: str):
    """Return raw image bytes for one profile (only if on the named watchlist)."""
    watchlist = request.args.get("watchlist") or DEFAULT_WATCHLIST or "patients"
    if not HOST or not USERNAME or not PASSWORD:
        return Response(status=503)
    try:
        session = requests.Session()
        do_login(session, HOST, USERNAME, PASSWORD)
        roster = patient_roster_data(session, HOST, watchlist)
        if not roster.get("ok"):
            return Response(status=404)
        if str(profile_id) not in roster["pids_set"]:
            return Response(status=404)
        raw, mime = resolve_profile_thumbnail_bytes(session, HOST, str(profile_id), roster)
        if not raw or not mime:
            return Response(status=404)
        return Response(
            raw,
            mimetype=mime,
            headers={"Cache-Control": "private, max-age=120"},
        )
    except requests.HTTPError:
        return Response(status=502)
    except Exception:
        return Response(status=500)


@app.route("/api/profile/<profile_id>", methods=["DELETE"])
def api_delete_profile(profile_id: str):
    """Remove a face profile (DELETE on Alta). Only allowed if profile is on the named watchlist."""
    watchlist = request.args.get("watchlist") or DEFAULT_WATCHLIST or "patients"
    if not HOST or not USERNAME or not PASSWORD:
        return jsonify({"ok": False, "error": "Server missing ALTA_HOST, ALTA_USERNAME, or ALTA_PASSWORD."}), 503
    try:
        session = requests.Session()
        do_login(session, HOST, USERNAME, PASSWORD)
        roster = patient_roster_data(session, HOST, watchlist)
        if not roster.get("ok"):
            return jsonify({"ok": False, "error": roster.get("error", "Could not verify watchlist.")}), 422
        if str(profile_id) not in roster["pids_set"]:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Profile is not on this watchlist; delete not allowed.",
                    }
                ),
                403,
            )
        url = f"{HOST}/api/v1/watchlistsProfiles/{profile_id}"
        aware_delete(session, url)
        return jsonify({"ok": True, "profile_id": profile_id})
    except requests.HTTPError as e:
        body, status = _http_error_payload(e)
        return jsonify(body), status
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def main():
    if not HOST:
        print("Warning: ALTA_HOST not set. Copy proxy/.env.example to proxy/.env.", file=sys.stderr)
    else:
        print(f"ALTA_HOST: {HOST}", file=sys.stderr, flush=True)

    if _alta_debug_verbose():
        print(
            "[alta-login] ALTA_DEBUG=1: verbose login traces enabled (stderr).",
            file=sys.stderr,
            flush=True,
        )
    discovered = _discover_live_snapshot_cameras()
    if discovered:
        with _LIVE_SNAPSHOT_LOCK:
            _MONITORED_CAMERAS.update(discovered)
        print(
            f"[proxy] startup discovered live snapshot cameras: {discovered}",
            file=sys.stderr,
            flush=True,
        )
    if _MONITORED_CAMERAS:
        print(
            f"[proxy] live snapshot cameras: {sorted(_MONITORED_CAMERAS)}",
            file=sys.stderr,
            flush=True,
        )
    else:
        print(
            "[proxy] live snapshot cameras: <none yet> (start from UI via /api/live-snapshot/select-camera)",
            file=sys.stderr,
            flush=True,
        )
    print(
        "[proxy] live snapshot background worker is disabled; /webhook captures one snapshot per event.",
        file=sys.stderr,
        flush=True,
    )
    host_bind_env = (os.environ.get("PROXY_HOST") or "").strip()
    if host_bind_env:
        host_bind = host_bind_env
    else:
        # In hosted environments (e.g. Render), bind publicly by default.
        host_bind = "0.0.0.0" if os.environ.get("RENDER") or os.environ.get("PORT") else "127.0.0.1"
    port = int(os.environ.get("PORT") or os.environ.get("PROXY_PORT", "8765"))
    lan_ip = ""
    try:
        # Best-effort local/LAN IP detection without sending traffic.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        try:
            lan_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            lan_ip = ""

    print(f"[proxy] binding on {host_bind}:{port}", file=sys.stderr, flush=True)
    print(f"[proxy] local URL:   http://127.0.0.1:{port}", file=sys.stderr, flush=True)
    print(f"[proxy] webhook URL: http://127.0.0.1:{port}/api/webhook", file=sys.stderr, flush=True)
    print(f"[proxy] webhook URL: http://127.0.0.1:{port}/webhook", file=sys.stderr, flush=True)
    print(f"[proxy] webhook URL: http://127.0.0.1:{port}/api/webhook-car-crossing", file=sys.stderr, flush=True)
    if lan_ip and not lan_ip.startswith("127."):
        print(f"[proxy] detected LAN: http://{lan_ip}:{port}", file=sys.stderr, flush=True)
        print(f"[proxy] webhook URL: http://{lan_ip}:{port}/api/webhook", file=sys.stderr, flush=True)
        print(f"[proxy] webhook URL: http://{lan_ip}:{port}/webhook", file=sys.stderr, flush=True)
        print(f"[proxy] webhook URL: http://{lan_ip}:{port}/api/webhook-car-crossing", file=sys.stderr, flush=True)
    else:
        print("[proxy] detected LAN: <unavailable>", file=sys.stderr, flush=True)

    if host_bind in ("127.0.0.1", "localhost", "::1"):
        print(
            "[proxy] NOTE: loopback-only bind. Set PROXY_HOST=0.0.0.0 to accept webhooks from other machines.",
            file=sys.stderr,
            flush=True,
        )
    elif host_bind in ("0.0.0.0", "::"):
        print(
            f"[proxy] webhook URL example: http://<this-machine-ip>:{port}/api/webhook",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"[proxy] webhook URL example: http://<this-machine-ip>:{port}/webhook",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"[proxy] webhook URL example: http://<this-machine-ip>:{port}/api/webhook-car-crossing",
            file=sys.stderr,
            flush=True,
        )
    else:
        print(f"[proxy] bound URL:    http://{host_bind}:{port}", file=sys.stderr, flush=True)
        print(f"[proxy] webhook URL: http://{host_bind}:{port}/api/webhook", file=sys.stderr, flush=True)
        print(f"[proxy] webhook URL: http://{host_bind}:{port}/webhook", file=sys.stderr, flush=True)
        print(f"[proxy] webhook URL: http://{host_bind}:{port}/api/webhook-car-crossing", file=sys.stderr, flush=True)
    app.run(host=host_bind, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")


if __name__ == "__main__":
    main()
