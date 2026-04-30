#!/usr/bin/env python3
"""
Local or server-side proxy for Alta Video face watchlist enrollment.
Mirrors the API flow in alta/faces.py: login → list watchlists → generate face
→ create profile → attach face. Keeps credentials off GitHub Pages.

HTTP API: POST /api/enroll, GET /api/patients-profiles, GET /api/health.
"""

import base64
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

# Some Avigilon Alta cloud hosts return 401 for the default python-requests User-Agent.
_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def apply_alta_session_defaults(session: requests.Session) -> None:
    session.headers.setdefault("Accept", "application/json, text/plain, */*")
    session.headers.setdefault(
        "User-Agent",
        (os.environ.get("ALTA_USER_AGENT") or "").strip() or _DEFAULT_UA,
    )


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


def _login_response_json(resp: requests.Response) -> Any:
    if not resp.content:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def do_login(session: requests.Session, host: str, username: str, password: str) -> Any:
    """
    POST credentials to Alta. Tries /api/v1/dologin first (faces.py / most scripts),
    then /api/v1/login (used on some cloud deployments). Override with ALTA_LOGIN_PATH.
    """
    apply_alta_session_defaults(session)
    base = host.rstrip("/")
    cred = {"username": username, "password": password}

    custom = (os.environ.get("ALTA_LOGIN_PATH") or "").strip()
    if custom:
        paths = [custom if custom.startswith("/") else "/" + custom]
    else:
        paths = ["/api/v1/dologin", "/api/v1/login"]

    last: requests.Response | None = None
    for path in paths:
        url = base + path
        resp = session.post(url, json=cred, timeout=60)
        last = resp
        if resp.status_code == 200:
            return _login_response_json(resp)
        # Try alternate path only for default pair (401/404 on first hop).
        if (
            not custom
            and path == "/api/v1/dologin"
            and resp.status_code in (401, 404)
            and len(paths) > 1
        ):
            continue
        resp.raise_for_status()

    if last is not None:
        last.raise_for_status()
    raise requests.HTTPError("Login failed: no response")


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


def collect_patients_profile_rows(
    session: requests.Session, host: str, watchlist_name: str | None
) -> dict:
    """Resolve watchlist + profile IDs + names for the patients (or named) watchlist."""
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

    profiles = [{"id": pid, "name": id_to_name.get(pid, pid)} for pid in sorted(pids_set, key=lambda x: (len(x), x))]
    return {
        "ok": True,
        "watchlist_id": wl_id,
        "watchlist_name": wl_display,
        "profiles": profiles,
        "profiles_endpoint": prof_path,
    }


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


def main():
    if not HOST:
        print("Warning: ALTA_HOST not set. Copy proxy/.env.example to proxy/.env.", file=sys.stderr)
    host_bind = os.environ.get("PROXY_HOST", "127.0.0.1")
    port = int(os.environ.get("PROXY_PORT", "8765"))
    app.run(host=host_bind, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")


if __name__ == "__main__":
    main()
