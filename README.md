# Patients watchlist (webcam → Alta Video)

Static site for [GitHub Pages](https://pages.github.com/) plus a small **Python proxy** that talks to your Alta Video server using the same API flow as `faces.py` in your Alta tooling repo.

## Why a proxy?

GitHub Pages only serves static files. The Alta API expects a logged-in session and is unlikely to allow browser **CORS** from `*.github.io`. Putting credentials in the static site would expose them publicly. The proxy holds `ALTA_HOST`, `ALTA_USERNAME`, and `ALTA_PASSWORD` in environment variables (or a local `.env` file that is never committed).

## API flow (matches `faces.py`)

1. `POST /api/v1/dologin` with username and password  
2. `GET` one of the watchlist list endpoints until one works (`/api/v1/faceWatchlists`, `/face-watchlists`, `/watchlists`, …)  
3. Resolve the watchlist named **patients** (case-insensitive)  
4. `POST /api/v1/watchlistsProfilesFaces/generate` with base64 image and `format` (`image/jpeg;base64` or `image/png;base64`)  
5. `POST /api/v1/watchlistsProfiles` with `name` and `watchlists: [watchlist_id]`  
6. `POST /api/v1/watchlistsProfilesFaces` with the generated face payload and `profile_id`

## Run locally

### 1. Proxy

```bash
cd proxy
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: ALTA_HOST, ALTA_USERNAME, ALTA_PASSWORD
python server.py
```

Default listen address: `http://127.0.0.1:8765` (loopback only). To open the site from another device or using your machine hostname in Chrome, set:

```env
PROXY_HOST=0.0.0.0
```

Then the static page on `http://THIS-HOST:8080` can reach `http://THIS-HOST:8765`.

### 2. Open the site

Either open `docs/index.html` in a browser (some browsers restrict `file://` camera — prefer a local server), or:

```bash
cd docs && python3 -m http.server 8080
```

Then visit **`http://127.0.0.1:8080`** (or **`http://localhost:8080`**) in Chrome — not `file://` — so the page can call the API and use the webcam reliably.

**If you use another host or port** (e.g. `http://your-computer.local:8080` or `http://192.168.x.x:8080`), the app calls the proxy at **`http://<same-hostname>:8765`**. Set in `proxy/.env`:

```env
PROXY_HOST=0.0.0.0
```

so Flask listens on all interfaces, not only loopback.

**Overrides** (bookmark-friendly):

- Query: `http://localhost:8080/?api=http://127.0.0.1:8765`
- Or in the browser console: `localStorage.setItem("patientWatchlistApiBase", "http://127.0.0.1:8765")` then reload.

For **GitHub Pages** (`https://*.github.io`), the app uses same-origin `/api/...` (reverse-proxy your proxy behind that host).

Use **Start camera**, enter a **Profile name**, then **Capture & add**. The right column lists profiles on the **patients** watchlist (`GET /api/patients-profiles` on the proxy).

## Publish to GitHub

1. Create a new empty repository on GitHub (for example `patient-watchlist-web`).
2. From this folder:

```bash
cd /path/to/patient-watchlist-web
git init
git add .
git commit -m "Add GitHub Pages UI and Alta enrollment proxy"
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

3. In the repo on GitHub: **Settings → Pages → Build and deployment → Source**: **Deploy from a branch**, branch **main**, folder **`/docs`**, Save.

After a minute, the site is at `https://YOUR_USER.github.io/YOUR_REPO/`.

**Important:** GitHub Pages still needs a reachable proxy (VPN, office network, or a small host with **HTTPS** and secrets). The UI uses same-origin `/api/...` when not on localhost; serve the static files and proxy under one HTTPS origin (reverse proxy), or change `apiBase()` in `docs/js/app.js`.

Browsers often **block** an `https://*.github.io` page from calling `http://127.0.0.1` (mixed content / private network rules). For day-to-day use on one PC, run the static `docs/` site over HTTP as well (see above with `python3 -m http.server`) while the proxy runs on `127.0.0.1:8765`. For use **from** the hosted GitHub Pages URL, deploy the proxy behind **HTTPS** (for example a small VPS with a reverse proxy and Let’s Encrypt).

## Optional: deploy the proxy

Any host that can run Python 3.10+ and set environment variables works (Fly.io, Railway, a VM, etc.). Enable HTTPS for production, restrict CORS in `server.py` if you like, and set `ALTA_*` as secrets on the platform.
