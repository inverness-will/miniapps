#!/usr/bin/env python3
"""
Local or server-side proxy for Alta Video face watchlist enrollment.
Mirrors the API flow in alta/faces.py: login → list watchlists → generate face
→ create profile → attach face. Keeps credentials off GitHub Pages.

HTTP API: POST /api/enroll, GET /api/patients-profiles, DELETE /api/profile/<id>, GET /api/health.
Debug: set ALTA_DEBUG=1 to log username and env wiring on stderr; add ALTA_DEBUG_LOG_PASSWORD=1
to log password repr (remove after troubleshooting — credentials in logs are a security risk).
"""

import base64
import json
import os
import re
import sys
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

HOST = os.environ.get("ALTA_HOST", "").strip().rstrip("/")
USERNAME = os.environ.get("ALTA_USERNAME", "").strip()
PASSWORD = os.environ.get("ALTA_PASSWORD", "").strip()
DEFAULT_WATCHLIST = os.environ.get("WATCHLIST_NAME", "patients").strip().lower()


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


def collect_patients_profile_rows(
    session: requests.Session, host: str, watchlist_name: str | None
) -> dict:
    """Resolve watchlist + profile IDs + names + thumbnails for the patients (or named) watchlist."""
    wl_query = (watchlist_name or DEFAULT_WATCHLIST or "patients").strip().lower()
    _endpoint, data = list_face_watchlists(session, host)
    watchlists = _watchlists_from_response(data or [])
    wl_id, wl_display = find_watchlist_id(watchlists, wl_query)
    if not wl_id:
        names = [w.get("name") or w.get("title") or "?" for w in watchlists[:20]]
        return {
            "ok": False,
            "error": f"No watchlist named '{wl_query}'. Sample names: {names}",
            "profiles": [],
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

    profiles = []
    for pid in sorted(pids_set, key=lambda x: (len(x), x)):
        name = id_to_name.get(pid, pid)
        prow = id_to_profile.get(pid)
        thumb = None
        tpl = face_photos.get(pid)
        if tpl:
            raw, mime = tpl
            if len(raw) <= 400_000:
                thumb = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        if not thumb:
            thumb = _profile_thumbnail_data_url(session, host, pid, prow)
        profiles.append({"id": pid, "name": name, "thumbnail_data_url": thumb})

    return {
        "ok": True,
        "watchlist_id": wl_id,
        "watchlist_name": wl_display,
        "profiles": profiles,
        "profiles_endpoint": prof_path,
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


@app.route("/api/profile/<profile_id>", methods=["DELETE"])
def api_delete_profile(profile_id: str):
    """Remove a face profile (DELETE on Alta). Only allowed if profile is on the named watchlist."""
    watchlist = request.args.get("watchlist") or DEFAULT_WATCHLIST or "patients"
    if not HOST or not USERNAME or not PASSWORD:
        return jsonify({"ok": False, "error": "Server missing ALTA_HOST, ALTA_USERNAME, or ALTA_PASSWORD."}), 503
    try:
        session = requests.Session()
        do_login(session, HOST, USERNAME, PASSWORD)
        info = collect_patients_profile_rows(session, HOST, watchlist)
        if not info.get("ok"):
            return jsonify({"ok": False, "error": info.get("error", "Could not verify watchlist.")}), 422
        allowed = {str(p.get("id")) for p in info.get("profiles", []) if p.get("id")}
        if str(profile_id) not in allowed:
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
    if _alta_debug_verbose():
        print(
            "[alta-login] ALTA_DEBUG=1: verbose login traces enabled (stderr).",
            file=sys.stderr,
            flush=True,
        )
    host_bind = os.environ.get("PROXY_HOST", "127.0.0.1")
    port = int(os.environ.get("PROXY_PORT", "8765"))
    app.run(host=host_bind, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")


if __name__ == "__main__":
    main()
