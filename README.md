# 3SVerse LinkedIn Poster

**Developed by www.3SVerse.com (c) 2026** · Independent vendor — not affiliated with LinkedIn or VidaPay.

Seller tool that publishes a **campaign folder** (day-by-day images + post text) to the **3S Verse LinkedIn company page** via the official LinkedIn API — with **Post Now** for immediate publishing and **Select All → Schedule** for a hands-off, one-post-per-day cadence.

The repo ships with the October 2026 30-day campaign in `campaigns/2026-10/` (30 ready-to-post 1200×627 PNGs + post text).

---

## How it works

1. **Load Campaign Folder** — pick a folder. The parser reads the campaign Markdown (`## Day N —` sections: image, body, hashtags) and pairs each `images/day-NN.png` with its text. Fallback: `day-NN.png` + `day-NN.txt` sidecars.
2. **Tick posts** (or **Select All**).
3. **Post Now** — publishes the ticked posts to the company page immediately (image upload + text via `ugcPosts`).
4. **Schedule…** — assigns the ticked posts to consecutive days at a fixed local time (default **09:00** — the campaign's recommended posting time). While the app is running it publishes each slot automatically (checks every 15 s). If the app was closed at post time, the slot is flagged **MISSED** — nothing posts twice, nothing silently disappears; tick it and hit **Post Now**.

Settings (stored in `%APPDATA%\3SVerse\LinkedInPoster\config.json`): Client ID / Secret, Organization ID, token (60-day), **Dry run** mode to rehearse the whole flow with zero API calls.

---

## One-time setup (LinkedIn developer app)

You need three things from linkedin.com/developers: **Client ID**, **Client Secret**, and the page's **Organization ID**.

### Step 1 — Create the app

1. Go to <https://www.linkedin.com/developers> and sign in with the account that is a **super admin of the 3S Verse page**.
2. **Create app** → fill name (`3SVerse Poster`), add a logo (use `3sverse_logo_header.png`), and select the **3S Verse** company page — this is how LinkedIn knows you may post as that page.
3. Verify the app (LinkedIn emails a verification link to the page admin).

### Step 2 — Request the products

On the app's **Products** tab:

- **Sign In with LinkedIn using OpenID Connect** — instant; identifies you (`openid profile`). Needed for **Connect** and **Test connection**.
- **Share on LinkedIn** — instant.
- **Community Management API** — **required to post to your Page** (`r_organization_social` + `w_organization_social`). LinkedIn reviews the request (usually 1–5 days) — Page posting works only after it shows **Approved**.

> The Poster requests the full scope set when connecting. If your app doesn't have the org products yet, it automatically retries with the basic sign-in scopes so Connect still succeeds — Page posting unlocks the moment LinkedIn approves **Community Management API** (just connect again afterwards).

### Step 3 — Auth credentials + redirect

On the **Auth** tab:

1. Copy **Client ID** and **Client Secret**.
2. Under **OAuth 2.0 redirect URLs**, add exactly:

   ```
   http://localhost:8529/callback
   ```

   (the Poster's built-in local callback — used by the **Connect LinkedIn…** button).

### Step 4 — Find the Organization ID (step by step)

The Organization ID is the **numeric id of the 3S Verse company page**. Easiest ways:

**Method A — view source (no tools needed)**

1. Open the page: `https://www.linkedin.com/company/3sverse/`
2. Press **Ctrl+U** (View page source).
3. Press **Ctrl+F** and search: `urn:li:organization:`
4. You will see `urn:li:organization:XXXXXXXXX` inside the page JSON — **the number after the last `:` is the Organization ID**.

**Method B — from the page URL (admin view)**

1. Open `https://www.linkedin.com/company/3sverse/admin/` (as page admin).
2. View source (Ctrl+U) and search `organizationId` — the digits are the id.

**Method C — let the Poster find it**

1. Paste Client ID + Secret in Settings → **Connect LinkedIn…** → once connected, click **Find Org ID (vanity)…**, type `3sverse`, and the app resolves the numeric id from the API (`GET /v2/organizations?q=vanityName`).

### Step 5 — Connect

Open the Poster → **Settings** → paste Client ID / Secret / Organization ID → **Connect LinkedIn…** (browser sign-in, token valid ~60 days) → **Test connection** (checks member + organization) → **Save**.

> When the token expires (2 months), just click **Connect LinkedIn…** again.

---

## Scheduling notes

- The scheduler uses the **PC's local clock**. The campaign's recommended slot is **9:00 AM Central** — keep the PC timezone set accordingly (America/Chicago).
- Scheduling requires the app to be **running** at post time (minimized is fine). Closed app → slots flagged **MISSED**, never lost, never double-posted.
- Already-`Posted` posts are skipped everywhere (idempotent).

## Compliancy

Posts go through LinkedIn's **official REST API** with your own developer-app token — this is the sanctioned posting path (no scraping, no fake browser automation). Content of the campaign pack is for 3SVerse marketing.

## Troubleshooting

**“Scope … is not authorized for your application”** (on Connect) — your LinkedIn app is missing the product that scope belongs to. Fix: Products tab → request **Community Management API** (the org scopes) and add **Sign In with LinkedIn using OpenID Connect** (instant). The Poster auto-retries with basic scopes so it still connects; Page posting unlocks once LinkedIn approves Community Management API (connect again afterwards).

**“GET https://api.linkedin.com/v2/userinfo failed: Remote end closed connection without response”** (on Test connection) — a network-level drop (AV/proxy/VPN or a transient LinkedIn hiccup). The Poster now retries 3× automatically. If it persists: allow `3SVerse_LinkedIn_Poster.exe` in your AV, disable VPN/proxy for the test, then run **Test connection** again.

**403 on Test / “Cannot resolve member identity”** — add **Sign In with LinkedIn using OpenID Connect** on the Products tab (instant) and connect again.

## Build

GitHub Actions (`.github/workflows/build-poster.yml`) builds a **single portable .exe** with Nuitka `--onefile` and attaches the exe itself to the release `poster-v<VERSION>` (VERSION is read from `linkedin_poster.py`). No zip folder — the release asset IS the runnable exe. Release assets are the distribution — no artifact storage used.

```
python -m nuitka --onefile --enable-plugin=tk-inter \
  --windows-console-mode=disable --windows-icon-from-ico=3sverse_icon.ico \
  --include-data-file=3sverse_logo_header.png=3sverse_logo_header.png \
  linkedin_poster.py
```

> **Antivirus / SentinelOne note (v1.0.2+):** the exe unpacks its runtime (python + tk DLLs) **once** into a stable folder — `%LOCALAPPDATA%\3SVerse LinkedIn Poster\<version>` — and reuses it on every launch (no random temp folders, no repeated DLL drops). If your AV (SentinelOne, Defender, etc.) asks on first run, choose **Allow / Trust**; afterwards nothing is re-extracted and the trigger loop stops. SentinelOne console admins can add a one-line exclusion for `3SVerse_LinkedIn_Poster.exe` if policy requires. The exe is built from this repo's own source via GitHub Actions — nothing external.

Developed by www.3SVerse.com — v1.0.4
