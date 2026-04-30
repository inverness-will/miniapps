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

Default listen address: `http://127.0.0.1:8765`.

### 2. Open the site

Either open `docs/index.html` in a browser (some browsers restrict `file://` camera — prefer a local server), or:

```bash
cd docs && python3 -m http.server 8080
```

Then visit `http://127.0.0.1:8080` and set **API base** to `http://127.0.0.1:8765` if it is not already.

Use **Start camera**, enter a **Display name**, then **Capture & add to patients**.

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

**Important:** Pages users still need a reachable proxy (same machine, VPN, or a small host you deploy with TLS and secrets). Point **API base** in the UI to that URL.

## Optional: deploy the proxy

Any host that can run Python 3.10+ and set environment variables works (Fly.io, Railway, a VM, etc.). Enable HTTPS for production, restrict CORS in `server.py` if you like, and set `ALTA_*` as secrets on the platform.
