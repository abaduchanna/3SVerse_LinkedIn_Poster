#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
3SVerse LinkedIn Poster — campaign folder → LinkedIn page posts.

Load a campaign folder (images/day-NN.png + post text from the campaign
Markdown), tick what you want, then:

  · Post Now     — publish the selected post(s) to the LinkedIn company
                   page immediately (image + text, official API).
  · Select All → Schedule… — line the selected posts up on consecutive
                   days at a fixed local time (default 09:00, matching
                   the campaign's recommended posting time). The app then
                   publishes each one automatically while it is running.
                   Anything missed while the app was closed is flagged
                   MISSED — nothing posts twice, nothing posts silently.

Credentials live in Settings: LinkedIn Client ID / Secret, Organization
ID (the numeric id of the 3S Verse company page), and the OAuth connect
button (local callback server, PKCE). See README for the step-by-step.

Developed by www.3SVerse.com (c) 2026
Independent vendor — not affiliated with LinkedIn or VidaPay.
"""

import base64
import hashlib
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import tkinter.font as tkfont

VERSION = "1.0.7"

# ── Brand tokens: the 3sverse.com dark-hero palette (same as License
# Studio — canvas hsl(250 28% 3%) · card hsl(250 20% 6%) · warm-white
# ink · brand ramp cyan/periwinkle/magenta + lime). ──
BG = "#07060b"
PANEL = "#0d0c14"
FIELD = "#12101a"
BORDER = "#232130"
TEXT = "#f5f1e6"
DIM = "#9b97b3"
CYAN = "#6ee7ef"
PERI = "#78a6ff"
MAGENTA = "#e44bd7"
LIME = "#c7ef70"
ACCENT = CYAN
ACCENT_HOVER = "#9deff5"
DANGER = "#ef6a52"
OK = LIME
WARN = "#f5c518"
GRAD = (CYAN, PERI, MAGENTA)
FONT = "Segoe UI"
MONO = "Consolas"
HEAD_FONT = FONT

APP_DIR_NAME = "LinkedInPoster"


def _probe_fonts(root):
    """Brand families Montserrat / DM Mono with graceful fallbacks."""
    global FONT, MONO, HEAD_FONT
    try:
        fams = set(tkfont.families(root))
        if "Montserrat" in fams:
            FONT = HEAD_FONT = "Montserrat"
        if "DM Mono" in fams:
            MONO = "DM Mono"
        elif "JetBrains Mono" in fams:
            MONO = "JetBrains Mono"
    except Exception:
        pass


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    """Mix two hex colors (t=0 → a, t=1 → b)."""
    ca, cb = _hex_rgb(a), _hex_rgb(b)
    return "#%02x%02x%02x" % tuple(
        int(round(ca[i] + (cb[i] - ca[i]) * t)) for i in range(3))


def make_grad_bar(parent, height=3):
    """Signature tri-gradient bar (cyan → periwinkle → magenta)."""
    bar = tk.Canvas(parent, height=height, highlightthickness=0,
                    bg=BG, bd=0)
    bar._grad = GRAD
    def _paint(event=None):
        bar.delete("g")
        w = max(bar.winfo_width(), 1)
        seg = max(w - 1, 1) / 199.0
        for i in range(200):
            t = i / 199.0
            c = (_mix(GRAD[0], GRAD[1], min(t / 0.5, 1.0)) if t < 0.5
                 else _mix(GRAD[1], GRAD[2], min((t - 0.5) / 0.5, 1.0)))
            bar.create_rectangle(int(i * seg), 0, int(i * seg) + seg + 1,
                                 height, outline=c, fill=c, tags="g")
    bar.bind("<Configure>", _paint)
    return bar


def make_eyebrow(parent, text, color=CYAN, bg=PANEL):
    """Small tracked-out brand eyebrow label."""
    row = tk.Frame(parent, bg=bg)
    tk.Label(row, text=text.upper(), bg=bg, fg=color,
             font=(MONO, 7, "bold")).pack(anchor="w")
    return row


# ════════════════════════════════════════════════════════════════════
# Campaign pack parser
# ════════════════════════════════════════════════════════════════════

DAY_HEAD = re.compile(r"^##\s*Day\s*(\d+)\s*[—–-]\s*(.+)$", re.M)
IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
TAG_RE = re.compile(r"^(#[A-Za-z0-9_]+(?:\s+#[A-Za-z0-9_]+)*)\s*$")


def parse_campaign(folder):
    """Parse a campaign folder into post dicts.

    Primary source: a Markdown file with `## Day N — ...` sections
    (image reference, body paragraphs, hashtags). Fallback: day-NN.txt
    sidecars next to (or under) images/. Each post: {day, title, image,
    text, meta{}}. Raises ValueError when nothing usable is found.
    """
    folder = os.path.abspath(folder)
    md_files = [f for f in sorted(os.listdir(folder))
                if f.lower().endswith(".md")]
    posts = {}
    for md in md_files:
        try:
            raw = open(os.path.join(folder, md), encoding="utf-8",
                       errors="replace").read()
        except OSError:
            continue
        blocks = re.split(r"\n-{3,}\s*\n", raw)
        for block in blocks:
            m = DAY_HEAD.search(block)
            if not m:
                continue
            # cut anything above the Day heading (markdown preamble can
            # share the first block before the first "---" divider)
            block = block[m.start():]
            day = int(m.group(1))
            img = None
            im = IMG_RE.search(block)
            if im:
                img = os.path.normpath(os.path.join(folder, im.group(1)))
                if not os.path.exists(img):
                    img = None
            # image fallback: images/day-NN.png
            if img is None:
                for sub in ("images", "."):
                    cand = os.path.join(folder, sub,
                                        "day-%02d.png" % day)
                    if os.path.exists(cand):
                        img = cand
                        break
            body, meta, tags = [], {}, []
            for line in block.splitlines():
                s = line.strip()
                if not s or s.startswith("## ") or s.startswith("!["):
                    continue
                mb = re.match(r"^\*\*(.+?):\*\*\s*(.+)$", s)
                if mb:
                    meta[mb.group(1).lower()] = mb.group(2).strip()
                    continue
                mt = TAG_RE.match(s)
                if mt:
                    tags.append(mt.group(1))
                    continue
                body.append(s)
            hook = meta.get("creative hook", "")
            title = "Day %d: %s" % (day, hook) if hook \
                else (m.group(2).strip())
            text = "\n\n".join(body)
            if tags:
                text = "\n\n".join([text, " ".join(tags[-1:])]) \
                    if text else " ".join(tags[-1:])
            posts[day] = {"day": day, "title": title, "image": img,
                          "text": text, "meta": meta, "file": md}
    # txt sidecar fallback
    if not posts:
        imgs = {}
        for root, _dirs, files in os.walk(folder):
            for f in files:
                m = re.match(r"day[-_]?(\d+)\.(png|jpe?g)$", f, re.I)
                if m:
                    imgs.setdefault(int(m.group(1)),
                                    os.path.join(root, f))
        for day, img in sorted(imgs.items()):
            txt = None
            for cand in (os.path.join(folder, "day-%02d.txt" % day),
                         os.path.join(folder, "posts",
                                      "day-%02d.txt" % day),
                         os.path.splitext(img)[0] + ".txt"):
                if os.path.exists(cand):
                    txt = cand
                    break
            if txt:
                text = open(txt, encoding="utf-8",
                            errors="replace").read().strip()
                posts[day] = {"day": day, "title": "Day %d" % day,
                              "image": img, "text": text,
                              "meta": {}, "file": txt}
    if not posts:
        raise ValueError(
            "No posts found. Need a Markdown with '## Day N —' "
            "sections (image + text) or day-NN.png + day-NN.txt files.")
    missing_text = [d for d, p in posts.items() if not p["text"]]
    missing_img = [d for d, p in posts.items() if not p["image"]]
    if missing_text or missing_img:
        raise ValueError(
            "Incomplete pairing — posts without text: %s; without "
            "image: %s" % (missing_text or "none", missing_img or "none"))
    return [posts[d] for d in sorted(posts)]


# ════════════════════════════════════════════════════════════════════
# LinkedIn API client (official REST — user's own developer app)
# ════════════════════════════════════════════════════════════════════

LI_AUTH = "https://www.linkedin.com/oauth/v2/authorization"
LI_TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
LI_API = "https://api.linkedin.com"
REDIRECT = "http://localhost:8529/callback"
OAUTH_SCOPES = ("openid profile r_organization_social "
                "w_organization_social w_member_social")
MEMBER_SCOPES = "openid profile w_member_social"   # "Share on LinkedIn"
                                                   # apps — personal posts
BASIC_SCOPES = "openid profile"          # OIDC only — connect-only
UA = "3SVerse-LinkedInPoster/%s (seller tool)" % VERSION


class LinkedInError(Exception):
    pass


_LAST_HEADERS = {}


def _last_header(name):
    """Case-insensitive lookup of the most recent response's header."""
    for k, v in _LAST_HEADERS.items():
        if k.lower() == (name or "").lower():
            return v
    return None


def _http(url, method="GET", headers=None, data=None, timeout=45,
          attempts=3):
    """Thin urllib wrapper → (status, text, bytes).

    Transient network failures ("Remote end closed connection without
    response", timeouts, resets — LinkedIn drops non-browser clients
    occasionally, AV/proxies do too) are retried with a small backoff.
    HTTP error responses (4xx/5xx) are returned as-is, never retried.
    Response headers are kept in _LAST_HEADERS (restli create calls
    return the new entity URN in x-restli-id).
    """
    global _LAST_HEADERS
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else \
            json.dumps(data).encode("utf-8")
    for attempt in range(max(1, attempts)):
        try:
            with urllib.request.urlopen(req, body, timeout=timeout) as r:
                _LAST_HEADERS = dict(r.headers.items())
                return r.status, r.read().decode("utf-8", "replace"), None
        except urllib.error.HTTPError as e:
            _LAST_HEADERS = dict(e.headers.items()) if e.headers else {}
            raw = e.read()
            try:
                return e.code, raw.decode("utf-8", "replace"), None
            except Exception:
                return e.code, "", raw
        except Exception as e:  # URLError, timeout, connection reset…
            if attempt + 1 >= max(1, attempts):
                raise LinkedInError("%s %s failed: %s"
                                    % (method, url, e))
            time.sleep(1.5 * (attempt + 1))
    raise LinkedInError("%s %s failed: exhausted retries" % (method, url))


def _api_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
        "LinkedIn-Version": "202607",   # 202510 sunset 2026-10-15
    }


class LinkedInClient:
    """Official-API client bound to one access token + org."""

    def __init__(self, token, org_id="", log=None):
        self.token = (token or "").strip()
        self.org_id = str(org_id or "").strip()
        self._log = log or (lambda *a, **k: None)

    # -- identity / org -------------------------------------------------
    def member_urn(self):
        h = _api_headers(self.token)
        last = "no response"
        for path, key in (("/v2/userinfo", "sub"), ("/v2/me", "id")):
            try:
                st, txt, _ = _http(LI_API + path, headers=h)
            except LinkedInError as exc:
                last = str(exc)[:200]
                continue
            if st == 200:
                sub = (json.loads(txt) or {}).get(key)
                if sub:
                    return "urn:li:person:" + sub
            last = "HTTP %s from %s: %s" % (st, path, txt[:160])
        raise LinkedInError(
            "Cannot resolve member identity (%s). If the error says "
            "403: add the “Sign In with LinkedIn using OpenID "
            "Connect” product to your LinkedIn app (Products tab — "
            "instant approval)." % last)

    def org_info(self, org_id=None):
        oid = str(org_id or self.org_id).strip()
        if not oid:
            raise LinkedInError("Organization ID is not set (Settings)")
        h = _api_headers(self.token)
        st, txt, _ = _http(
            LI_API + "/v2/organizations/" + urllib.parse.quote(oid),
            headers=h)
        if st == 200:
            data = json.loads(txt) or {}
            name = (data.get("localizedName")
                    or data.get("name") or "")
            return {"id": oid, "name": name, "urn":
                    "urn:li:organization:" + oid}
        # ACL probe (works when /organizations/{id} shape differs)
        st2, txt2, _ = _http(
            LI_API + "/v2/organizationAcls?q=organization&organization="
            + urllib.parse.quote("urn:li:organization:" + oid),
            headers=h)
        if st2 == 200:
            return {"id": oid, "name": "", "urn":
                    "urn:li:organization:" + oid}
        raise LinkedInError("Organization check failed (HTTP %s): %s"
                            % (st, txt2[:200] or txt[:200]))

    def find_org_by_vanity(self, vanity):
        """Resolve a numeric org id from the page vanity name."""
        h = _api_headers(self.token)
        q = urllib.parse.quote(vanity.strip().lower())
        st, txt, _ = _http(
            LI_API + "/v2/organizations?q=vanityName&vanityName=" + q,
            headers=h)
        if st != 200:
            raise LinkedInError("Vanity lookup failed (HTTP %s): %s"
                                % (st, txt[:200]))
        els = (json.loads(txt) or {}).get("elements") or []
        if not els:
            raise LinkedInError(
                "No organization found for vanity '%s' — check the "
                "page URL name and the token scopes." % vanity)
        e = els[0]
        urn = e.get("id") or ""
        if isinstance(urn, int):
            urn = str(urn)
        return {"id": urn.split(":")[-1] if ":" in urn else urn,
                "name": e.get("localizedName") or "", "urn": urn}

    # -- image upload ----------------------------------------------------
    def register_asset(self, org_urn, person_urn):
        h = _api_headers(self.token)
        payload = {"registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": org_urn,
            "serviceRelationships": [
                {"relationshipType": "OWNER",
                 "identifier": person_urn}]}}
        st, txt, _ = _http(
            LI_API + "/v2/assets?action=registerUpload", method="POST",
            headers=h, data=payload)
        if st in (403, 422) and org_urn:
            # Some apps must register the asset against the member.
            payload["registerUploadRequest"]["owner"] = person_urn
            st, txt, _ = _http(
                LI_API + "/v2/assets?action=registerUpload",
                method="POST", headers=h, data=payload)
        if st != 200:
            raise LinkedInError("registerUpload failed (HTTP %s): %s"
                                % (st, txt[:300]))
        val = (json.loads(txt) or {}).get("value") or {}
        upload_url = val.get("uploadUrl") or \
            (val.get("permissibleActions") or [{}])[0].get("href")
        asset = val.get("asset") or val.get("digitalmediaAsset")
        if not upload_url or not asset:
            raise LinkedInError("registerUpload response missing "
                                "uploadUrl/asset: " + txt[:300])
        return upload_url, asset

    def upload_image(self, upload_url, path):
        ctype = "image/png" if path.lower().endswith(".png") else \
            "image/jpeg"
        with open(path, "rb") as f:
            raw = f.read()
        st, txt, _ = _http(upload_url, method="PUT",
                           headers={"Content-Type": ctype}, data=raw,
                           timeout=120)
        if st not in (200, 201):
            raise LinkedInError("Image upload failed (HTTP %s): %s"
                                % (st, txt[:300]))

    # -- post ------------------------------------------------------------
    def create_org_post(self, text, asset_urn=None, org_urn=None,
                        person_urn=None, link=None):
        """Publish via the versioned Posts API (/rest/posts) — the
        official replacement for the legacy ugcPosts endpoint. If the
        Posts API is unavailable on the caller's app (older app /
        version), falls back to /v2/ugcPosts automatically."""
        org_urn = org_urn or ("urn:li:organization:" + self.org_id)
        h = _api_headers(self.token)
        payload = {
            "author": org_urn,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED",
                             "targetEntities": [],
                             "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if asset_urn:
            payload["content"] = {"media": {"id": asset_urn}}
        elif link:
            payload["content"] = {"article": {"source": link}}
        st, txt, _ = _http(LI_API + "/rest/posts", method="POST",
                           headers=h, data=payload)
        if st in (200, 201):
            pid = ""
            try:
                pid = (json.loads(txt) or {}).get("id") or ""
            except Exception:
                pass
            return pid or _last_header("x-restli-id") or ""
        st2, txt2, pid = self._create_org_post_legacy(
            text, asset_urn, org_urn, link)
        if pid is not None:
            return pid
        raise LinkedInError(
            "Post failed. Posts API (HTTP %s): %s | ugcPosts (HTTP %s): %s"
            % (st, txt[:200], st2, txt2[:200]))

    def _create_org_post_legacy(self, text, asset_urn, org_urn, link):
        """Legacy ugcPosts endpoint → (status, errtext, post_id|None).
        post_id is None only when the call failed."""
        media_cat = "IMAGE" if asset_urn else (
            "ARTICLE" if link else "NONE")
        share = {"shareCommentary": {"text": text},
                 "shareMediaCategory": media_cat}
        if asset_urn:
            share["media"] = [{"status": "READY",
                               "media": asset_urn}]
        elif link:
            share["media"] = [{"status": "READY",
                               "originalUrl": link}]
        payload = {
            "author": org_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": share},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility":
                           "PUBLIC"},
        }
        h = _api_headers(self.token)
        st, txt, _ = _http(LI_API + "/v2/ugcPosts", method="POST",
                           headers=h, data=payload)
        if st in (200, 201):
            try:
                pid = (json.loads(txt) or {}).get("id", "")
            except Exception:
                pid = ""
            return st, txt, pid or ""
        return st, txt, None

    def post_image(self, image_path, text, as_person=False):
        """Full pipeline: register → upload → publish. Returns post urn.
        as_person=True publishes from the member's own profile
        (w_member_social — “Share on LinkedIn” product, instant) instead
        of the organization page (w_organization_social — CM API)."""
        person = self.member_urn()
        if as_person:
            upload_url, asset = self.register_asset(person, person)
            self._log("uploading image … %d KB"
                      % (os.path.getsize(image_path) // 1024), DIM)
            self.upload_image(upload_url, image_path)
            return self.create_org_post(text, asset_urn=asset,
                                        org_urn=person,
                                        person_urn=person)
        org_urn = "urn:li:organization:" + self.org_id
        upload_url, asset = self.register_asset(org_urn, person)
        self._log("uploading image … %d KB"
                  % (os.path.getsize(image_path) // 1024), DIM)
        self.upload_image(upload_url, image_path)
        return self.create_org_post(text, asset_urn=asset,
                                    org_urn=org_urn, person_urn=person)

    # -- OAuth (PKCE, localhost callback) --------------------------------
    @staticmethod
    def oauth_url(client_id, verifier, state, port=8529, scopes=None):
        redirect = REDIRECT.replace("8529", str(port))
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        q = urllib.parse.urlencode({
            "response_type": "code", "client_id": client_id,
            "redirect_uri": redirect, "state": state,
            "scope": scopes or OAUTH_SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256"})
        return LI_AUTH + "?" + q

    @staticmethod
    def exchange_code(client_id, client_secret, code, verifier,
                      port=8529):
        redirect = REDIRECT.replace("8529", str(port))
        form = urllib.parse.urlencode({
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect, "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier}).encode()
        req = urllib.request.Request(LI_TOKEN, data=form, method="POST")
        req.add_header("Content-Type",
                       "application/x-www-form-urlencoded")
        req.add_header("User-Agent", UA)
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode())
        tok = data.get("access_token")
        if not tok:
            raise LinkedInError("Token exchange failed: %s" % data)
        return {"access_token": tok,
                "expires_at": time.time() +
                float(data.get("expires_in", 5184000))}


# ════════════════════════════════════════════════════════════════════
# Settings + schedule store (%APPDATA%/3SVerse/LinkedInPoster)
# ════════════════════════════════════════════════════════════════════

def store_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "3SVerse", APP_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


class Store:
    """Two JSON files: config.json (settings/token) and schedule.json."""

    def __init__(self):
        self.cfg_path = os.path.join(store_dir(), "config.json")
        self.sch_path = os.path.join(store_dir(), "schedule.json")
        self.cfg = self._load(self.cfg_path, {
            "client_id": "", "client_secret": "", "org_id": "",
            "access_token": "", "token_expires_at": 0,
            "dry_run": False, "last_folder": "", "post_time": "09:00",
            "post_mode": "page", "scopes_granted": "",
        })
        self.schedule = self._load(self.sch_path, {"posts": {}})

    @staticmethod
    def _load(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                default.update(data)
        except Exception:
            pass
        return default

    def save_cfg(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2)

    def save_schedule(self):
        with open(self.sch_path, "w", encoding="utf-8") as f:
            json.dump(self.schedule, f, indent=2)

    # keyed by "folder|day" so multiple campaigns can coexist
    @staticmethod
    def key(folder, day):
        return "%s|%d" % (os.path.normpath(folder), day)

    def entry(self, folder, day):
        return self.schedule["posts"].get(self.key(folder, day), {})

    def set_entry(self, folder, day, **fields):
        k = self.key(folder, day)
        e = self.schedule["posts"].setdefault(k, {})
        e.update(fields)
        e["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.save_schedule()

    def forget_folder(self, folder):
        prefix = self.key(folder, 0)[:-1]
        for k in [k for k in self.schedule["posts"]
                  if k.startswith(prefix)]:
            del self.schedule["posts"][k]
        self.save_schedule()


def _parse_hhmm(s, default="09:00"):
    s = (s or "").strip()
    m = re.match(r"^(\d{1,2})[:.h]?(\d{2})?\s*(am|pm|AM|PM)?$", s)
    if not m:
        return default
    hh, mm, ap = int(m.group(1)), int(m.group(2) or 0), \
        (m.group(3) or "").lower()
    if ap == "pm" and hh < 12:
        hh += 12
    if ap == "am" and hh == 12:
        hh = 0
    return "%02d:%02d" % (hh % 24, mm % 60)


class Scheduler(threading.Thread):
    """Background loop: publishes due posts while the app is running."""

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.stop_flag = threading.Event()

    def run(self):
        while not self.stop_flag.wait(15):
            try:
                self.tick()
            except Exception:
                self.app.log("scheduler: " +
                             traceback.format_exc(limit=2), DANGER)

    def tick(self):
        now = datetime.now()
        store = self.app.store
        fires = []
        for k, e in list(store.schedule["posts"].items()):
            if e.get("status") != "scheduled":
                continue
            when = e.get("when")
            if not when:
                continue
            try:
                wt = datetime.fromisoformat(when)
            except ValueError:
                continue
            delta = (now - wt).total_seconds()
            if 0 <= delta <= 300:
                # due right now (within the 5-minute window) → fire
                fires.append((k, e, wt))
            elif delta > 300:
                # the app was not running at post time → flag it
                e["status"] = "missed"
                store.save_schedule()
                self.app.log("MISSED: %s (was due %s) — app was not "
                             "running. Tick it and Post Now to send."
                             % (e.get("title", k),
                                wt.strftime("%b %d %H:%M")), WARN)
                self.app.refresh_row(k)
        if fires:
            self.app.auto_fire(*fires[0][:2])  # one per tick — gentle

    def fire(self, k, e, auto=False):
        folder, day_s = k.rsplit("|", 1)
        day = int(day_s)
        post = self.app.post_by_key(k)
        if post is None:
            self.app.log("skip %s — campaign not loaded" % k, WARN)
            return False
        client = self.app.client()
        label = post["title"]
        try:
            if self.app.store.cfg.get("dry_run"):
                time.sleep(1.2)  # simulate API latency
                urn = "dry-run-post-urn"
            else:
                urn = client.post_image(
                    post["image"], post["text"],
                    as_person=self.app.store.cfg.get(
                        "post_mode", "page") == "person")
            self.app.store.set_entry(
                folder, day, status="posted",
                posted_at=datetime.now().isoformat(timespec="seconds"),
                post_urn=urn)
            self.app.log(("AUTO" if auto else "") + "POSTED: " + label +
                         ("  → " + urn if urn else ""), OK)
            self.app.refresh_row(k, status="Posted")
            return True
        except Exception as exc:
            self.app.store.set_entry(folder, day, status="failed",
                                     error=str(exc)[:400])
            self.app.log("FAILED: " + label + " — " + str(exc), DANGER)
            self.app.refresh_row(k, status="Failed")
            return False


# ════════════════════════════════════════════════════════════════════
# OAuth local callback server
# ════════════════════════════════════════════════════════════════════

def oauth_wait_code(port=8529, timeout=180, expected_state=""):
    """Run a tiny localhost server to catch the OAuth redirect.
    Returns (code, error). Blocks in a worker thread."""
    import http.server
    import socketserver
    result = {"code": None, "error": None}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.urlparse(self.path)
            if q.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            params = urllib.parse.parse_qs(q.query)
            if params.get("state", [""])[0] != expected_state:
                result["error"] = "state mismatch (OAuth safety check)"
            elif "code" in params:
                result["code"] = params["code"][0]
            else:
                result["error"] = params.get(
                    "error_description", params.get("error",
                                                    ["denied"]))[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = result["code"] and not result["error"]
            msg = ("LinkedIn connected — you can close this tab."
                   if ok else "Connection failed: " +
                   str(result["error"]))
            self.wfile.write(("<html><body style='background:#07060b;"
                              "color:#f5f1e6;font-family:Segoe UI;"
                              "display:grid;place-items:center;height:90vh'>"
                              "<h2 style='color:#6ee7ef'>3SVerse "
                              "LinkedIn Poster</h2><p>%s</p></body>"
                              "</html>" % msg).encode())

        def log_message(self, *a):  # silence
            pass

    class Srv(socketserver.TCPServer):
        allow_reuse_address = True

    try:
        srv = Srv(("127.0.0.1", port), H)
    except OSError as e:
        return None, ("local port %d busy (%s) — close other LinkedIn "
                      "tools and retry" % (port, e))
    srv.timeout = 1
    deadline = time.time() + timeout
    try:
        while not result["code"] and not result["error"] and \
                time.time() < deadline:
            srv.handle_request()
    finally:
        srv.server_close()
    if not result["code"] and not result["error"]:
        result["error"] = "timed out waiting for the browser redirect"
    return result["code"], result["error"]


def _is_scope_err(err):
    """True when LinkedIn rejected the requested scopes (product
    missing on the user's developer app)."""
    e = (err or "").lower()
    return "scope" in e or "not authorized" in e or "invalid_scope" in e


SCOPE_HELP = (
    "Your LinkedIn developer app is missing a PRODUCT.\n\n"
    "PAGE posting needs “Community Management API”. LinkedIn only "
    "accepts that request on a NEW app that has NO other products — "
    "on your existing app the request button looks grayed out / "
    "closed. This is LinkedIn's rule, not an error on your side.\n\n"
    "Fix (official LinkedIn route):\n"
    "  1. developer.linkedin.com/dashboard → Create app — pick the "
    "same company page (you are its admin) → verify the app via "
    "the page\n"
    "  2. On the NEW app: Products tab → Request “Community "
    "Management API” (available there because the app has no other "
    "products; approval usually 1–5 days)\n"
    "  3. Auth tab → add redirect URL  http://localhost:8529/callback"
    "  → copy the new Client ID + Secret into 3SVerse Settings → "
    "Connect again\n\n"
    "Instant products on your CURRENT app (Connect + personal "
    "posting): “Sign In with LinkedIn using OpenID Connect”, "
    "“Share on LinkedIn”.\n\n"
    "While approval is pending you can test PAGE posting end-to-end "
    "on LinkedIn's sandbox pages — Org ID 2414183 (DevTestCo) or "
    "6177438 (Test University) — posts land on LinkedIn's test "
    "pages, not on your page.\n\n"
    "No registered business yet? Switch Settings → Post to: “Personal "
    "profile” — it only needs “Share on LinkedIn” (instant) and posts "
    "from your own profile.\n\n"
    "Go to: developer.linkedin.com/dashboard → your apps → Products.")


def _scope_nag(parent, err):
    messagebox.showwarning(
        "Connect — missing LinkedIn product",
        "LinkedIn said:\n“%s”\n\n%s" % ((err or "")[:300], SCOPE_HELP),
        parent=parent)


# ════════════════════════════════════════════════════════════════════
# GUI
# ════════════════════════════════════════════════════════════════════

STATUS_COLORS = {"Draft": DIM, "Scheduled": PERI, "Posted": OK,
                 "Failed": DANGER, "Missed": WARN}


class App:
    def __init__(self, root):
        self.root = root
        self.store = Store()
        self.posts = []          # parsed campaign posts
        self.folder = ""
        self.rows = {}           # key → {frame, var, status_lbl, when_lbl}
        self.busy = False
        self.log_q = queue.Queue()
        root.title("3SVerse LinkedIn Poster")
        root.configure(bg=BG)
        root.geometry("1000x760")
        root.minsize(880, 620)
        _probe_fonts(root)
        try:
            ico = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "3sverse_icon.ico")
            if os.path.exists(ico):
                root.iconbitmap(default=ico)
        except Exception:
            pass

        # Shared website/WiFi Transfer background. This uses the exact
        # 120s ring spin, 140s orb spin and 12px/13s float timings.
        from branding_runtime import install_branding
        install_branding(root, dark=True, background=BG)

        self._build_header()
        self._build_body()
        self._build_footer()
        self._drain_log()
        self.scheduler = Scheduler(self)
        self.scheduler.start()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        last = self.store.cfg.get("last_folder")
        if last and os.path.isdir(last):
            self.root.after(300, lambda: self.load_folder(last))

    # ── brand background (same ring/orb artwork as License Studio) ──
    def _load_bg_assets(self):
        base = os.path.dirname(os.path.abspath(__file__))
        for name in ("bg_ring.png", "bg_orb.png"):
            try:
                self._bg_imgs.append(
                    tk.PhotoImage(file=os.path.join(base, name)))
            except Exception:
                pass          # asset missing -> fallback rings drawn

    def _draw_bg(self):
        """Redraw the brand art on resize. The ring hugs the right
        edge half off-canvas and the orb tucks into the bottom-left
        corner - same composition as License Studio / the website."""
        c = self._bg_canvas
        c.delete("all")
        w = c.winfo_width() or 1000
        h = c.winfo_height() or 760
        ring = self._bg_imgs[0] if self._bg_imgs else None
        orb = self._bg_imgs[1] if len(self._bg_imgs) > 1 else None
        if ring is not None:
            c.create_image(w + ring.width() * 0.16, h * 0.44,
                           image=ring, anchor="center")
        if orb is not None:
            c.create_image(-orb.width() * 0.22, h - orb.height() * 0.42,
                           image=orb, anchor="center")
        if len(self._bg_imgs) < 2:
            # graceful fallback: thin concentric rings, right side
            cy = h * 0.46
            for r in (120, 185, 250, 315):
                c.create_oval(w - r, cy - r, w + r, cy + r,
                              outline=BORDER, width=1)

    # ── header (center-anchored hero — version NOT here) ─────────────
    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 0))
        row = tk.Frame(head, bg=BG)
        row.pack(anchor="center")
        logo_w = 0
        logo = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "3sverse_logo_header.png")
        if os.path.exists(logo):
            self._hdr_logo = tk.PhotoImage(file=logo)
            tk.Label(row, image=self._hdr_logo, bg=BG, bd=0
                     ).pack(side="left")
            logo_w = self._hdr_logo.width()
        texts = tk.Frame(row, bg=BG)
        texts.pack(side="left", padx=((14, 0) if logo_w else (0, 0)))
        make_eyebrow(texts, "Seller console · LinkedIn automation",
                     color=CYAN, bg=BG).pack(anchor="w")
        trow = tk.Frame(texts, bg=BG)
        trow.pack(anchor="w")
        tk.Label(trow, text="3SVerse LinkedIn ", bg=BG, fg=TEXT,
                 font=(HEAD_FONT, 20)).pack(side="left")
        tk.Label(trow, text="Poster", bg=BG, fg=CYAN,
                 font=(HEAD_FONT, 20)).pack(side="left")
        make_grad_bar(self.root).pack(fill="x", pady=(10, 0))

    # ── body: toolbar + list + log ───────────────────────────────────
    def _build_body(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=18, pady=(12, 4))
        self._btn(bar, "Load Campaign Folder", self.load_folder_dialog,
                  fill=True).pack(side="left")
        self._btn(bar, "Select All", self.select_all).pack(side="left",
                                                           padx=(8, 0))
        self._btn(bar, "Clear", self.clear_sel).pack(side="left",
                                                     padx=(8, 0))
        self._btn(bar, "Schedule…", self.schedule_dialog).pack(
            side="left", padx=(8, 0))
        self._btn(bar, "Post Now", self.post_now, fill=True).pack(
            side="right")
        self._btn(bar, "Settings", self.settings_dialog).pack(
            side="right", padx=(0, 8))

        self.status_lbl = tk.Label(self.root, bg=BG, fg=DIM,
                                   font=(MONO, 8), anchor="w")
        self.status_lbl.pack(fill="x", padx=18, pady=(2, 6))

        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True, padx=18)
        # scrollable post list
        listwrap = tk.Frame(shell, bg=PANEL,
                            highlightbackground=BORDER,
                            highlightthickness=1)
        listwrap.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(listwrap, bg=PANEL, bd=0,
                                highlightthickness=0)
        vs = tk.Scrollbar(listwrap, orient="vertical",
                          command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list = tk.Frame(self.canvas, bg=PANEL)
        self.canvas.create_window((0, 0), window=self.list,
                                  anchor="nw", tags="inner")
        self.list.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            "inner", width=e.width))
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(
            int(-e.delta / 120), "units"))
        # log panel
        logwrap = tk.Frame(shell, bg=PANEL, highlightbackground=BORDER,
                           highlightthickness=1)
        logwrap.pack(side="right", fill="y", padx=(10, 0))
        self.log_box = tk.Text(logwrap, width=44, bg=PANEL, fg=TEXT,
                               bd=0, font=(MONO, 8), wrap="word",
                               state="disabled", height=10)
        self.log_box.pack(fill="both", expand=True, padx=6, pady=6)
        self._empty_note()

    def _btn(self, parent, text, cmd, fill=False):
        bgc = ACCENT if fill else FIELD
        fgc = BG if fill else TEXT
        b = tk.Button(parent, text=text, command=cmd, bd=0,
                      bg=bgc, fg=fgc, font=(FONT, 9, "bold"),
                      activebackground=ACCENT_HOVER if fill
                      else _mix(FIELD, ACCENT, 0.15),
                      activeforeground=BG, cursor="hand2",
                      padx=14, pady=6)
        return b

    def _empty_note(self):
        self.empty_lbl = tk.Label(
            self.list, text="No campaign loaded.\n\nClick “Load Campaign "
            "Folder” and pick the folder that contains the images + post "
            "text (e.g. campaigns/2026-10).",
            bg=PANEL, fg=DIM, font=(FONT, 11), justify="center")
        self.empty_lbl.pack(pady=60)

    # ── footer (brand strip: © · version · independence note) ────────
    def _build_footer(self):
        bar = tk.Frame(self.root, bg=PANEL,
                       highlightbackground=BORDER, highlightthickness=1)
        bar.pack(side="bottom", fill="x")
        inner = tk.Frame(bar, bg=PANEL)
        inner.pack(fill="x", padx=18, pady=7)
        inner.columnconfigure(1, weight=1)
        tk.Label(inner, text="© %d 3S Verse · Developed by "
                            "www.3sverse.com" % time.localtime().tm_year,
                 bg=PANEL, fg=DIM, font=(FONT, 8)).grid(row=0, column=0,
                                                        sticky="w")
        tk.Label(inner, text="LinkedIn Poster v%s" % VERSION,
                 bg=PANEL, fg=DIM, font=(MONO, 8)).grid(row=0, column=1)
        tk.Label(inner, text="Independent vendor — not affiliated with "
                            "LinkedIn or VidaPay", bg=PANEL, fg=DIM,
                 font=(FONT, 8)).grid(row=0, column=2, sticky="e")

    # ── rows ─────────────────────────────────────────────────────────
    def load_folder_dialog(self):
        d = filedialog.askdirectory(
            title="Pick the campaign folder (images + post text)")
        if d:
            self.load_folder(d)

    def load_folder(self, folder):
        try:
            posts = parse_campaign(folder)
        except Exception as exc:
            messagebox.showerror("3SVerse LinkedIn Poster", str(exc))
            return
        self.folder = os.path.normpath(folder)
        self.posts = posts
        self.store.cfg["last_folder"] = self.folder
        self.store.save_cfg()
        try:
            self.empty_lbl.destroy()
        except Exception:
            pass
        for w in self.list.winfo_children():
            w.destroy()
        self.rows = {}
        self._thumbs = {}
        for p in self.posts:
            k = self.store.key(self.folder, p["day"])
            e = self.store.entry(self.folder, p["day"])
            row = tk.Frame(self.list, bg=PANEL,
                           highlightbackground=BORDER,
                           highlightthickness=1)
            row.pack(fill="x", padx=10, pady=5)
            var = tk.BooleanVar(value=False)
            cb = tk.Checkbutton(row, variable=var, bg=PANEL, fg=CYAN,
                                activebackground=PANEL,
                                activeforeground=CYAN,
                                selectcolor=FIELD, bd=0,
                                highlightthickness=0, width=3,
                                cursor="hand2")
            cb.pack(side="left", padx=(8, 0))
            img = p.get("image")
            if img and os.path.exists(img):
                try:
                    ph = tk.PhotoImage(file=img)
                    scale = max(1, ph.width() // 96)
                    th = ph.subsample(scale, scale)
                    self._thumbs[k] = (ph, th)  # keep refs
                    tk.Label(row, image=th, bg=PANEL, bd=0).pack(
                        side="left", padx=8, pady=6)
                except Exception:
                    pass
            mid = tk.Frame(row, bg=PANEL)
            mid.pack(side="left", fill="x", expand=True, padx=6)
            tk.Label(mid, text=p["title"], bg=PANEL, fg=TEXT,
                     font=(FONT, 10, "bold"), anchor="w").pack(
                fill="x", anchor="w")
            n_chars = len(p["text"])
            tags = p["meta"].get("campaign stage", "")
            tk.Label(mid, text="%d chars%s · %s" % (
                n_chars, "  ·  over 3000!" if n_chars > 3000 else "",
                tags), bg=PANEL, fg=DIM, font=(MONO, 8),
                anchor="w").pack(fill="x", anchor="w")
            right = tk.Frame(row, bg=PANEL)
            right.pack(side="right", padx=10)
            status = e.get("status", "draft").capitalize()
            when = e.get("when", "")
            when_txt = datetime.fromisoformat(when).strftime(
                "%b %d · %H:%M") if when else ""
            s_lbl = tk.Label(right, text=status, fg=STATUS_COLORS.get(
                status, DIM), bg=PANEL, font=(MONO, 9, "bold"))
            s_lbl.pack(anchor="e")
            w_lbl = tk.Label(right, text=when_txt, fg=DIM, bg=PANEL,
                             font=(MONO, 8))
            w_lbl.pack(anchor="e")
            self.rows[k] = {"frame": row, "var": var, "status": s_lbl,
                            "when": w_lbl, "post": p}
        self.log("campaign loaded: %d posts from %s"
                 % (len(posts), self.folder), OK)
        self._update_status()

    def _update_status(self):
        n = len(self.posts)
        sched = sum(1 for p in self.posts if self.store.entry(
            self.folder, p["day"]).get("status") == "scheduled")
        posted = sum(1 for p in self.posts if self.store.entry(
            self.folder, p["day"]).get("status") == "posted")
        tok = "token OK" if self.token_valid() else "not connected"
        mode = self.store.cfg.get("post_mode", "page")
        self.status_lbl.config(text="folder: %s   ·   %d posts   ·   "
                               "%d scheduled   ·   %d posted   ·   "
                               "LinkedIn: %s%s%s" % (
                self.folder or "—", n, sched, posted, tok,
                "   ·   DRY RUN" if self.store.cfg.get("dry_run") else "",
                "   ·   PERSONAL PROFILE" if mode == "person" else ""))

    def refresh_row(self, key, status=None):
        """Thread-safe: marshals the UI update onto the main loop."""
        self.root.after(0, lambda: self._refresh_row_ui(key, status))

    def _refresh_row_ui(self, key, status=None):
        row = self.rows.get(key)
        if not row:
            return
        e = self.store.entry(self.folder,
                             row["post"]["day"])
        st = status or e.get("status", "draft").capitalize()
        row["status"].config(text=st,
                             fg=STATUS_COLORS.get(st, DIM))
        when = e.get("when", "")
        row["when"].config(text=datetime.fromisoformat(when).strftime(
            "%b %d · %H:%M") if when else "")
        self._update_status()

    def selected_keys(self):
        return [k for k, r in self.rows.items() if r["var"].get()]

    def select_all(self):
        for r in self.rows.values():
            r["var"].set(True)

    def clear_sel(self):
        for r in self.rows.values():
            r["var"].set(False)

    def post_by_key(self, key):
        for p in self.posts:
            if self.store.key(self.folder, p["day"]) == key:
                return p
        return None

    # ── actions ──────────────────────────────────────────────────────
    def client(self):
        c = self.store.cfg
        return LinkedInClient(c.get("access_token", ""),
                              c.get("org_id", ""),
                              log=lambda m, col=None: self.log(m, col))

    def token_valid(self):
        c = self.store.cfg
        return bool(c.get("access_token")) and \
            float(c.get("token_expires_at", 0)) > time.time() + 60

    def _require_ready(self):
        if not self.posts:
            messagebox.showinfo("3SVerse LinkedIn Poster",
                                "Load a campaign folder first.")
            return False
        if self.busy:
            messagebox.showinfo("Busy", "Another action is running.")
            return False
        if not self.token_valid() or (
                self.store.cfg.get("post_mode", "page") == "page"
                and not self.store.cfg.get("org_id")):
            miss = "" if self.token_valid() else "token — "
            miss += "Organization ID" if self.store.cfg.get(
                "post_mode", "page") == "page" \
                and not self.store.cfg.get("org_id") else ""
            if not messagebox.askyesno(
                    "Connect LinkedIn",
                    "LinkedIn is not ready yet (missing %s).\n\nOpen "
                    "Settings now?" % ("connect/token" if not miss
                                       else miss)):
                return False
            self.settings_dialog()
            return False
        return True

    def post_now(self):
        keys = self.selected_keys()
        if not self._require_ready():
            return
        if not keys:
            messagebox.showinfo("Post Now", "Tick at least one post.")
            return
        if len(keys) > 3 and not messagebox.askyesno(
                "Post Now", "Post %d selected posts to the LinkedIn "
                "page right now?" % len(keys)):
            return
        self._run_in_thread(keys, auto=False)

    def auto_fire(self, key, entry):
        self.log("auto-posting (schedule): " + entry.get("title", ""),
                 PERI)
        self._run_in_thread([key], auto=True)

    def _run_in_thread(self, keys, auto=False):
        def work():
            self.busy = True
            ok = 0
            for k in keys:
                r = self.rows.get(k)
                if not r:
                    continue
                e = self.store.entry(self.folder, r["post"]["day"])
                if e.get("status") == "posted":
                    self.log("skip (already posted): " +
                             r["post"]["title"], WARN)
                    continue
                if self.scheduler.fire(k, e, auto=auto):
                    ok += 1
            self.busy = False
            self.log("done — %d/%d posted" % (ok, len(keys)), OK)
        threading.Thread(target=work, daemon=True).start()

    def schedule_dialog(self):
        if not self.posts:
            messagebox.showinfo("Schedule", "Load a campaign folder "
                                            "first.")
            return
        keys = self.selected_keys()
        if not keys:
            messagebox.showinfo(
                "Schedule", "Tick the posts to schedule (or Select All).")
            return
        days = sorted(self.post_by_key(k)["day"] for k in keys)
        win = tk.Toplevel(self.root)
        win.title("Schedule %d posts" % len(keys))
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()
        box = tk.Frame(win, bg=BG)
        box.pack(fill="both", expand=True, padx=22, pady=16)
        tk.Label(box, text="Schedule %d selected posts" % len(keys),
                 bg=BG, fg=TEXT, font=(HEAD_FONT, 14)).pack(anchor="w")
        make_grad_bar(box, height=2).pack(fill="x", pady=(8, 12))
        grid = tk.Frame(box, bg=BG)
        grid.pack(fill="x")
        fields = []
        defaults = [
            ("Start date (YYYY-MM-DD)", datetime.now().strftime("%Y-%m-%d")),
            ("Posting time (local, HH:MM)", self.store.cfg.get(
                "post_time", "09:00")),
            ("Interval (days between posts)", "1"),
        ]
        for i, (label, val) in enumerate(defaults):
            tk.Label(grid, text=label, bg=BG, fg=DIM,
                     font=(FONT, 9)).grid(row=i, column=0, sticky="w",
                                          pady=4)
            ent = tk.Entry(grid, bg=FIELD, fg=TEXT, bd=0,
                           insertbackground=TEXT, font=(MONO, 10),
                           width=24)
            ent.insert(0, val)
            ent.grid(row=i, column=1, sticky="w", padx=10, pady=4)
            fields.append(ent)
        note = tk.Label(box, text="", bg=BG, fg=PERI, font=(MONO, 9),
                        justify="left", anchor="w")
        note.pack(fill="x", pady=(10, 4))
        hint = tk.Label(
            box, text="Posts publish automatically while this app is "
            "running (minimize is fine — do not close it). If a slot "
                      "is missed, the post is flagged MISSED and you can "
                      "send it with Post Now.",
            bg=BG, fg=DIM, font=(FONT, 8), wraplength=420,
            justify="left")
        hint.pack(fill="x", pady=(0, 10))

        def preview(*_a):
            try:
                start = datetime.strptime(fields[0].get().strip(),
                                          "%Y-%m-%d")
                hhmm = _parse_hhmm(fields[1].get())
                step = max(int(fields[2].get() or "1"), 1)
            except ValueError:
                note.config(text="enter a valid start date")
                return
            first = start
            last = start + timedelta(days=step * (len(days) - 1))
            note.config(text="first: %s %s  ·  last: %s %s  (%d posts, "
                             "one per %d day(s))" % (
                    first.strftime("%a %b %d"), hhmm,
                    last.strftime("%a %b %d"), hhmm, len(days), step))
        for ent in fields:
            ent.bind("<KeyRelease>", preview)
        preview()

        def ok():
            try:
                start = datetime.strptime(fields[0].get().strip(),
                                          "%Y-%m-%d")
                hhmm = _parse_hhmm(fields[1].get())
                hh, mm = hhmm.split(":")
                step = max(int(fields[2].get() or "1"), 1)
                for i, k in enumerate(sorted(
                        keys, key=lambda k: self.post_by_key(k)["day"])):
                    when = start + timedelta(days=step * i)
                    dt = when.replace(hour=int(hh), minute=int(mm))
                    p = self.post_by_key(k)
                    e = self.store.entry(self.folder, p["day"])
                    if e.get("status") == "posted":
                        continue
                    self.store.set_entry(
                        self.folder, p["day"], status="scheduled",
                        when=dt.isoformat(timespec="minutes"),
                        title=p["title"])
                    self.refresh_row(k, status="Scheduled")
                self.store.cfg["post_time"] = hhmm
                self.store.save_cfg()
                self.log("scheduled %d posts (first %s %s)"
                         % (len(keys), start.strftime("%b %d"), hhmm), PERI)
                win.destroy()
            except ValueError as exc:
                messagebox.showerror("Schedule", "Bad value: %s" % exc,
                                     parent=win)
        btns = tk.Frame(box, bg=BG)
        btns.pack(fill="x", pady=(6, 0))
        self._btn(btns, "Schedule", ok, fill=True).pack(side="right")
        self._btn(btns, "Cancel", win.destroy).pack(side="right", padx=8)

    # ── settings ─────────────────────────────────────────────────────
    def settings_dialog(self):
        c = self.store.cfg
        win = tk.Toplevel(self.root)
        win.title("Settings — LinkedIn")
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()
        box = tk.Frame(win, bg=BG)
        box.pack(fill="both", expand=True, padx=22, pady=16)
        tk.Label(box, text="LinkedIn connection", bg=BG, fg=TEXT,
                 font=(HEAD_FONT, 14)).pack(anchor="w")
        make_grad_bar(box, height=2).pack(fill="x", pady=(8, 12))
        entries = {}
        rows = [("Client ID", c.get("client_id", "")),
                ("Client Secret", c.get("client_secret", "")),
                ("Organization ID (numeric)", c.get("org_id", ""))]
        for i, (label, val) in enumerate(rows):
            tk.Label(box, text=label, bg=BG, fg=DIM,
                     font=(FONT, 9)).pack(anchor="w", pady=(6, 0))
            ent = tk.Entry(box, bg=FIELD, fg=TEXT, bd=0,
                           insertbackground=TEXT, font=(MONO, 10),
                           width=52, show="" if "Secret" not in label
                           else "•")
            ent.insert(0, val)
            ent.pack(anchor="w", pady=(2, 0))
            entries[label] = ent
        dry = tk.BooleanVar(value=bool(c.get("dry_run")))
        tk.Checkbutton(box, text="Dry run (simulate posts — no API "
                       "calls)", variable=dry, bg=BG, fg=TEXT,
                       activebackground=BG, selectcolor=FIELD,
                       font=(FONT, 9), bd=0, highlightthickness=0,
                       cursor="hand2").pack(anchor="w", pady=(10, 2))
        mode = tk.StringVar(
            value=c.get("post_mode") if c.get("post_mode") in
            ("page", "person") else "page")
        tk.Label(box, text="Post to:", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(anchor="w", pady=(8, 0))
        tk.Radiobutton(box, text="Company Page — needs “Community "
                       "Management API” (form requires a REGISTERED "
                       "business)", variable=mode, value="page",
                       bg=BG, fg=TEXT, activebackground=BG,
                       selectcolor=FIELD, font=(FONT, 9), bd=0,
                       highlightthickness=0, cursor="hand2").pack(
            anchor="w")
        tk.Radiobutton(box, text="Personal profile — needs “Share on "
                       "LinkedIn” only (instant)", variable=mode,
                       value="person", bg=BG, fg=TEXT,
                       activebackground=BG, selectcolor=FIELD,
                       font=(FONT, 9), bd=0, highlightthickness=0,
                       cursor="hand2").pack(anchor="w")
        tok_state = ("connected, expires %s" %
                     datetime.fromtimestamp(float(
                         c.get("token_expires_at", 0))).strftime(
                             "%b %d %Y")) if self.token_valid() \
            else "not connected"
        self.tok_lbl = tk.Label(box, text="token: " + tok_state,
                                bg=BG, fg=DIM, font=(MONO, 9))
        self.tok_lbl.pack(anchor="w", pady=(6, 2))
        btns = tk.Frame(box, bg=BG)
        btns.pack(fill="x", pady=(8, 0))

        def connect():
            cid = entries["Client ID"].get().strip()
            sec = entries["Client Secret"].get().strip()
            if not cid or not sec:
                messagebox.showerror(
                    "Connect", "Client ID and Client Secret are needed "
                    "first (from your LinkedIn developer app).",
                    parent=win)
                return

            def attempt(scopes):
                verifier = secrets.token_urlsafe(48)
                state = secrets.token_urlsafe(16)
                url = LinkedInClient.oauth_url(cid, verifier, state,
                                               scopes=scopes)
                webbrowser.open(url)
                code, err = oauth_wait_code(expected_state=state)
                return code, err, verifier

            self.log("opening browser for LinkedIn sign-in …", PERI)
            code, err, verifier = attempt(OAUTH_SCOPES)
            granted = OAUTH_SCOPES
            if (err or not code) and _is_scope_err(err):
                # org scopes rejected → try personal-posting scopes
                # ("Share on LinkedIn" product) before going basic.
                self.log("org scopes rejected — retrying with "
                         "personal-posting scopes …", WARN)
                code, err, verifier = attempt(MEMBER_SCOPES)
                granted = MEMBER_SCOPES
            if (err or not code) and _is_scope_err(err):
                self.log("member scope rejected too — retrying with "
                         "basic sign-in scopes …", WARN)
                code, err, verifier = attempt(BASIC_SCOPES)
                granted = BASIC_SCOPES
            if code and not err and granted != OAUTH_SCOPES:
                if granted == MEMBER_SCOPES:
                    self.log("connected — personal-profile posting OK; "
                             "Page posting locked until “Community "
                             "Management API” is approved (Settings → "
                             "Post to: Personal profile)", WARN)
                else:
                    self.log("connected WITHOUT posting scopes — add "
                             "“Share on LinkedIn” (personal posts) or "
                             "“Community Management API” (page posts) "
                             "on your LinkedIn app, then connect again",
                             WARN)
            if err or not code:
                if _is_scope_err(err):
                    _scope_nag(win, err)
                else:
                    messagebox.showerror("Connect", err or "no code",
                                         parent=win)
                return
            try:
                tok = LinkedInClient.exchange_code(cid, sec, code,
                                                   verifier)
            except Exception as exc:
                messagebox.showerror("Connect",
                                     "Token exchange failed: %s" % exc,
                                     parent=win)
                return
            c["access_token"] = tok["access_token"]
            c["token_expires_at"] = tok["expires_at"]
            c["client_id"], c["client_secret"] = cid, sec
            c["scopes_granted"] = granted
            self.store.save_cfg()
            self.tok_lbl.config(text="token: connected, expires %s" %
                                datetime.fromtimestamp(
                                    tok["expires_at"]).strftime(
                                        "%b %d %Y"))
            self.log("LinkedIn connected — token valid 60 days", OK)
            self._update_status()

        def find_org():
            # helper: numeric org id from the page vanity name
            if not self.token_valid():
                messagebox.showinfo("Find Organization ID",
                                    "Connect first (needs a token with "
                                    "r_organization_social).",
                                    parent=win)
                return
            ask = tk.simpledialog.askstring(
                "Find Organization ID",
                "Page vanity name (the word after linkedin.com/company/):",
                parent=win)
            if not ask:
                return
            try:
                info = self.client().find_org_by_vanity(ask)
            except Exception as exc:
                messagebox.showerror("Find Organization ID", str(exc),
                                     parent=win)
                return
            entries["Organization ID (numeric)"].delete(0, "end")
            entries["Organization ID (numeric)"].insert(0, info["id"])
            messagebox.showinfo("Find Organization ID",
                                "Found: %s\nOrganization ID: %s"
                                % (info["name"] or ask, info["id"]),
                                parent=win)

        def test():
            self._save_settings(entries, dry, mode)
            c = self.client()
            person_mode = mode.get() == "person"
            me = org = None
            errs = []
            try:
                me = c.member_urn()
            except Exception as exc:
                errs.append("member: %s" % exc)
            if not person_mode:
                try:
                    org = c.org_info()
                except Exception as exc:
                    errs.append("org: %s" % exc)
            joined = " ".join(errs)
            if me and (person_mode or org):
                if person_mode:
                    self.log("connection OK — personal-profile posting "
                             "ready (%s)" % me, OK)
                    messagebox.showinfo(
                        "Test connection",
                        "Member: %s\n\nPersonal-profile posting ready. "
                        "If actual posts fail with 403, add “Share on "
                        "LinkedIn” to your LinkedIn app (Products tab, "
                        "instant) and connect again." % me, parent=win)
                else:
                    self.log("connection OK — %s posts as %s (%s)"
                             % (me, org["name"] or org["id"],
                                org["urn"]), OK)
                    messagebox.showinfo(
                        "Test connection",
                        "Member: %s\nOrganization: %s (id %s)\n\nReady "
                        "to post." % (me, org["name"] or "(name hidden)",
                                      org["id"]), parent=win)
            elif me or org:
                self.log("connection PARTIAL — %s" % joined, WARN)
                extra = "\n\n%s" % SCOPE_HELP \
                    if (_is_scope_err(joined) or "403" in joined) else ""
                messagebox.showwarning(
                    "Test connection",
                    "Token works, but:\n\n%s%s"
                    % ("\n".join(errs), extra), parent=win)
            else:
                messagebox.showerror("Test connection",
                                     "\n\n".join(errs), parent=win)

        self._btn(btns, "Connect LinkedIn…", connect).pack(side="left")
        self._btn(btns, "Find Org ID (vanity)…", find_org).pack(
            side="left", padx=(8, 0))
        self._btn(btns, "Test connection", test).pack(side="left",
                                                      padx=(8, 0))
        self._btn(btns, "Save", lambda: (self._save_settings(entries,
                                                            dry, mode),
                                         win.destroy()),
                  fill=True).pack(side="right")

        helpbox = tk.Frame(win, bg=PANEL, highlightbackground=BORDER,
                           highlightthickness=1)
        helpbox.pack(fill="x", padx=22, pady=(0, 16))
        tk.Label(helpbox, text="Where do I get these? (step by step)",
                 bg=PANEL, fg=CYAN, font=(FONT, 9, "bold")).pack(
            anchor="w", padx=10, pady=(8, 2))
        steps = (
            "1.  linkedin.com/developers → Create app (you are the 3S "
            "Verse page admin) → verify the app via the page.\n"
            "2.  Products tab → add “Sign In with LinkedIn using OpenID "
            "Connect” + “Share on LinkedIn” (both instant — the second "
            "enables Personal-profile posting). For PAGE posting: "
            "LinkedIn only accepts “Community Management API” on a NEW "
            "app with no other products, and its access form needs a "
            "REGISTERED business name (approval ~1–5 days).\n"
            "3.  Auth tab → Client ID + Client Secret → Redirect URLs: "
            "add  http://localhost:8529/callback\n"
            "4.  Page mode only — Organization ID: open your page → "
            "linkedin.com/company/3sverse → Ctrl+U (view source) → "
            "search “urn:li:organization:” → the number after it is the "
            "Organization ID. (Or connect first and use “Find Org ID "
            "(vanity)” above.) While approval is pending, Org ID "
            "2414183 (DevTestCo) works for testing.\n"
            "5.  Paste everything here → Connect → Test → Save.")
        tk.Label(helpbox, text=steps, bg=PANEL, fg=DIM,
                 font=(MONO, 8), justify="left", wraplength=560).pack(
            anchor="w", padx=10, pady=(0, 10))

    def _save_settings(self, entries, dry_var, mode_var=None):
        c = self.store.cfg
        c["client_id"] = entries["Client ID"].get().strip()
        c["client_secret"] = entries["Client Secret"].get().strip()
        c["org_id"] = entries["Organization ID (numeric)"].get().strip()
        c["dry_run"] = bool(dry_var.get())
        if mode_var is not None:
            c["post_mode"] = mode_var.get()
        self.store.save_cfg()
        self.log("settings saved%s" % (" (dry run)" if c["dry_run"]
                                       else ""), DIM)
        self._update_status()

    # ── log + lifecycle ──────────────────────────────────────────────
    def log(self, msg, color=None):
        self.log_q.put((datetime.now().strftime("%H:%M:%S"), msg,
                        color or TEXT))

    def _drain_log(self):
        try:
            while True:
                ts, msg, color = self.log_q.get_nowait()
                self.log_box.configure(state="normal")
                self.log_box.insert("end", "[%s] %s\n" % (ts, msg),
                                    ("c",))
                self.log_box.tag_configure("c", foreground=color)
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(250, self._drain_log)

    def on_close(self):
        if messagebox.askokcancel(
                "Exit", "Closing the app stops scheduled auto-posting "
                "(already scheduled slots will be flagged MISSED). "
                "Exit?"):
            self.scheduler.stop_flag.set()
            self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
