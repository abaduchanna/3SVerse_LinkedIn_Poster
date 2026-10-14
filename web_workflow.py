#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
3SVerse LinkedIn Poster — Browser Mode (web workflow).

Faithful Python/Selenium port of the 3SVerse LinkedIn Campaign Uploader
Chrome extension (source: 3SVerse_Social_Campaign_Assistant /
linkedin-campaign-uploader v1.2.0; full extension source ships in
chrome-extension/ in this repo). Same command-for-command CDP driving:

  · content.js locators/state  → STATE_JS, injected via execute_script
  · background.js postItem     → web_post_item()
  · trusted CDP input          → Input.dispatchMouseEvent / insertText /
                                 dispatchKeyEvent (+ focus emulation)
  · media attach               → Page.setInterceptFileChooserDialog +
                                 DOM.setFileInputFiles (local file path)
  · schedule-first-then-caption (v1.1.5 fix) — configureSchedule runs
    BEFORE fillCaption; the caption is verified after schedule selection
    so LinkedIn can never drop it.
  · per-item queue state       → completed/failed/skipped, persisted on
    disk (completed results take precedence after a queue rebuild).

The workflow runs against the user's own logged-in Chrome session
(dedicated profile under the app data dir) in a normal Chrome window —
no LinkedIn developer app, no API token. Everything types and clicks
through CDP trusted input with focus emulation, so the window may sit
in the background while it works.

Developed by www.3SVerse.com (c) 2026
Independent vendor — not affiliated with LinkedIn or VidaPay.
"""

import json
import os
import time

DASHBOARD_URL_DEFAULT = (
    "https://www.linkedin.com/company/144975022/admin/dashboard/")

# ── content.js port: locator + state snapshot (chrome-free JS) ──────
# Same selectors/labels as the extension's content script.
STATE_JS = r"""
const __norm = v => String(v || "").normalize("NFKC").replace(/\s+/g, " ").trim().toLowerCase();
const __visible = e => Boolean(e && e.getClientRects().length && e.ownerDocument.defaultView.getComputedStyle(e).visibility !== "hidden");
const __enabled = e => __visible(e) && !e.matches(":disabled,[aria-disabled='true']");
const __label = e => __norm(e?.getAttribute("aria-label") || e?.innerText || e?.textContent);
function __documents() {
  const out = [];
  const visit = (doc, frames) => {
    out.push({ doc, frames });
    for (const f of doc.querySelectorAll("iframe")) {
      try { if (f.contentDocument) visit(f.contentDocument, [...frames, f]); } catch (_) {}
    }
  };
  visit(document, []);
  return out;
}
function __findInAll(pred) {
  for (const entry of __documents()) {
    const el = pred(entry.doc);
    if (el) return { ...entry, element: el };
  }
  return null;
}
function __controls(doc, sel = "button,[role='button'],a,[role='link']") {
  return [...doc.querySelectorAll(sel)].filter(__visible);
}
function __target(name) {
  if (name === "create") return __findInAll(d => __controls(d, "a,[role='link'],button,[role='button']").find(e => __enabled(e) && __label(e) === "create"));
  if (name === "startPost") return __findInAll(d => __controls(d, "a,[role='link'],button,[role='button']").find(e => __enabled(e) && /^start a post(?:\s|$)/.test(__label(e))));
  if (name === "editor") return __findInAll(d => [...d.querySelectorAll('[role="textbox"],[contenteditable="true"],textarea')].filter(__visible).find(e => /text editor for creating/.test(__label(e)))
    || [...d.querySelectorAll('[role="textbox"][contenteditable="true"],[contenteditable="true"].ql-editor')].filter(__visible).at(-1));
  if (name === "addMedia") return __findInAll(d => __controls(d).find(e => __enabled(e) && __label(e) === "add media"));
  if (name === "next") return __findInAll(d => __controls(d).find(e => __enabled(e) && __label(e) === "next"));
  if (name === "post") return __findInAll(d => __controls(d).filter(e => __enabled(e) && __label(e) === "post").at(-1));
  if (name === "schedulePost") return __findInAll(d => __controls(d).find(e => __enabled(e) && __label(e) === "schedule post"));
  if (name === "scheduleDate") return __findInAll(d => {
    const e = d.querySelector("#share-post__scheduled-date");
    return __visible(e) && !e.disabled ? e : null; });
  if (name === "scheduleTime") return __findInAll(d => {
    const e = d.querySelector("#share-post__scheduled-time");
    return __visible(e) && !e.disabled ? e : null; });
  if (name === "scheduleNext") return __findInAll(d => {
    const h = [...d.querySelectorAll('h1,h2,h3,[role="heading"]')].find(e => __visible(e) && __label(e) === "schedule post");
    return h ? __controls(d).find(e => __enabled(e) && __label(e) === "next") : null; });
  if (name === "scheduleFinal") return __findInAll(d => __controls(d).filter(e => __enabled(e) && __label(e) === "schedule").at(-1));
  return null;
}
function __pointFor(found) {
  const { element, frames } = found;
  for (const f of frames) f.scrollIntoView({ block: "center", inline: "center" });
  element.scrollIntoView({ block: "center", inline: "center" });
  element.focus({ preventScroll: true });
  let r = element.getBoundingClientRect();
  let x = r.left + r.width / 2, y = r.top + r.height / 2;
  for (let i = frames.length - 1; i >= 0; i -= 1) {
    r = frames[i].getBoundingClientRect();
    x += r.left; y += r.top;
  }
  return { x, y, label: __label(element), tag: element.tagName };
}
function __scheduleValues() {
  const found = __findInAll(d => d.querySelector("#share-post__scheduled-date"));
  return found ? {
    date: found.doc.querySelector("#share-post__scheduled-date").value || "",
    time: found.doc.querySelector("#share-post__scheduled-time").value || ""
  } : { date: "", time: "" };
}
function __editorValue(found) {
  const el = found?.element;
  if (!el) return "";
  return "value" in el ? el.value : (el.innerText || el.textContent || "");
}
function __screenSummary() {
  const names = __documents().flatMap(({ doc }) => __controls(doc).map(e => __label(e)).filter(Boolean)).slice(0, 24);
  return "url=" + location.href + ", controls=[" + names.join(" | ") + "]";
}
function __state() {
  const create = __target("create"), startPost = __target("startPost"),
        editor = __target("editor"), addMedia = __target("addMedia"),
        next = __target("next"), post = __target("post"),
        schedulePost = __target("schedulePost"), scheduleNext = __target("scheduleNext"),
        scheduleFinal = __target("scheduleFinal"), schedule = __scheduleValues();
  const live = __documents().flatMap(({ doc }) => [...doc.querySelectorAll('[role="alert"],[role="status"],[aria-live]')].filter(__visible).map(e => e.innerText || e.textContent || "")).join(" ");
  return {
    createReady: Boolean(create),
    startPostReady: Boolean(startPost),
    editorReady: Boolean(editor),
    captionText: __editorValue(editor),
    addMediaReady: Boolean(addMedia),
    nextReady: Boolean(next),
    postReady: Boolean(post),
    schedulePostReady: Boolean(schedulePost),
    scheduleDialogReady: Boolean(scheduleNext) && Boolean(schedule.date) && Boolean(schedule.time),
    scheduleFinalReady: Boolean(scheduleFinal),
    schedule,
    composerOpen: Boolean(editor) || Boolean(addMedia) || Boolean(post) || Boolean(scheduleFinal),
    successNotice: /(?:post (?:was|has been|is) (?:published|posted|scheduled)|successfully (?:published|posted|scheduled)|your post is live|post scheduled)/i.test(live),
    summary: __screenSummary()
  };
}
"""

LOCATE_JS = STATE_JS + " const __f = __target(%s); if (!__f) return { ok: false, error: 'LinkedIn control not found: %s; ' + __screenSummary() }; __f.element.focus({ preventScroll: true }); return { ok: true, point: __pointFor(__f) };"

FOCUS_JS = STATE_JS + " const __f = __target(%s); if (!__f) return { ok: false, error: 'LinkedIn control not found: %s; ' + __screenSummary() }; __f.element.focus({ preventScroll: true }); return { ok: __f.element.ownerDocument.activeElement === __f.element };"

STATE_SNIPPET = STATE_JS + " return __state();"


class WebError(Exception):
    pass


class LoginRequired(WebError):
    pass


def _norm(s):
    return " ".join(str(s or "").split()).strip().lower()


def expected_schedule(date_text, hhmm):
    """Extension expectedSchedule(): ISO date + HH:MM → LinkedIn fields."""
    year, month, day = date_text.split("-")
    hours, minutes = [int(x) for x in hhmm.split(":")]
    return {
        "date": "%s/%s/%s" % (month, day, year),
        "time": "%d:%02d %s" % (hours % 12 or 12, minutes,
                                "PM" if hours >= 12 else "AM"),
    }


class WebPoster:
    """One Selenium Chrome session driving the extension workflow."""

    def __init__(self, profile_dir, dashboard_url=DASHBOARD_URL_DEFAULT,
                 log=None):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        self.dashboard_url = dashboard_url or DASHBOARD_URL_DEFAULT
        self.log = log or (lambda m, c=None: None)
        opts = Options()
        opts.add_argument("--user-data-dir=%s" % profile_dir)
        opts.add_argument("--profile-directory=Default")
        opts.add_argument("--no-first-run")
        opts.add_argument("--no-default-browser-check")
        opts.add_argument("--disable-session-crashed-bubble")
        opts.add_argument("--hide-crash-restore-bubble")
        opts.add_argument(
            "--window-size=1280,900")
        # CDP events (Page.fileChooserOpened) surface via the perf log
        opts.set_capability("goog:loggingPrefs",
                            {"performance": "ALL", "browser": "ALL"})
        self.driver = webdriver.Chrome(options=opts)
        self.driver.set_page_load_timeout(90)

    # ── low-level CDP (same commands as the extension) ───────────────
    def _state(self):
        return self.driver.execute_script(STATE_SNIPPET)

    def _wait_state(self, predicate, timeout, description):
        started = time.time()
        state = None
        while time.time() - started < timeout:
            self._login_check()
            state = self._state()
            if predicate(state):
                return state
            time.sleep(0.4)
        raise WebError("%s; %s" % (description,
                                   (state or {}).get("summary", "state unavailable")))

    def _login_check(self):
        url = self.driver.current_url or ""
        if "/login" in url or "/checkpoint" in url or "authwall" in url:
            raise LoginRequired(
                "LinkedIn is asking you to sign in — open the Chrome "
                "window that this app launched, sign in, then start "
                "Browser Mode again.")

    def _locate(self, name):
        res = self.driver.execute_script(
            LOCATE_JS % (json.dumps(name), json.dumps(name)))
        if not res or not res.get("ok"):
            raise WebError((res or {}).get(
                "error", "LinkedIn control not found: %s" % name))
        return res["point"]

    def _focus(self, name):
        res = self.driver.execute_script(
            FOCUS_JS % (json.dumps(name), json.dumps(name)))
        if not res or not res.get("ok"):
            raise WebError((res or {}).get(
                "error", "LinkedIn did not focus the %s field." % name))

    def _focus_emulation(self):
        try:
            self.driver.execute_cdp_cmd(
                "Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            pass

    def _click(self, name):
        point = self._locate(name)
        self._focus_emulation()
        self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": point["x"], "y": point["y"]})
        for t in ("mousePressed", "mouseReleased"):
            self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                "type": t, "x": point["x"], "y": point["y"],
                "button": "left", "clickCount": 1})

    def _key(self, key, code, vk, modifiers=0, keyup=False):
        self.driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
            "type": "keyUp" if keyup else "rawKeyDown",
            "key": key, "code": code,
            "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
            "modifiers": modifiers})

    def _select_all(self):
        self._key("a", "KeyA", 65, modifiers=2)
        self._key("a", "KeyA", 65, modifiers=2, keyup=True)

    def _replace_text(self, name, text, commit):
        self._locate(name)
        self._focus_emulation()
        self._focus(name)
        self._select_all()
        self.driver.execute_cdp_cmd("Input.insertText", {"text": text})
        keys = ([("ArrowDown", "ArrowDown", 40), ("Enter", "Enter", 13)]
                if commit == "Enter" else [("Tab", "Tab", 9)])
        for key, code, vk in keys:
            self._key(key, code, vk)
            self._key(key, code, vk, keyup=True)

    def _upload(self, path):
        point = self._locate("addMedia")
        self._focus_emulation()
        self.driver.execute_cdp_cmd("Page.enable", {})
        self.driver.execute_cdp_cmd("DOM.enable", {})
        self.driver.execute_cdp_cmd("Runtime.enable", {})
        self.driver.execute_cdp_cmd("Page.setInterceptFileChooserDialog",
                                    {"enabled": True})
        try:
            self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": point["x"], "y": point["y"]})
            for t in ("mousePressed", "mouseReleased"):
                self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                    "type": t, "x": point["x"], "y": point["y"],
                    "button": "left", "clickCount": 1})
            # Selenium surfaces Page.fileChooserOpened through the event
            # log — poll the performance log for it.
            backend = self._wait_file_chooser(20)
            if not backend:
                raise WebError("LinkedIn's native media chooser did not "
                               "open within 20 seconds.")
            self.driver.execute_cdp_cmd("DOM.setFileInputFiles", {
                "files": [os.path.abspath(path)],
                "backendNodeId": backend})
        finally:
            try:
                self.driver.execute_cdp_cmd(
                    "Page.setInterceptFileChooserDialog", {"enabled": False})
            except Exception:
                pass

    def _wait_file_chooser(self, timeout_s):
        import json as _json
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                entries = self.driver.get_log("performance")
            except Exception:
                entries = []
            for entry in entries:
                try:
                    msg = _json.loads(entry["message"])["message"]
                except Exception:
                    continue
                if msg.get("method") == "Page.fileChooserOpened":
                    params = msg.get("params", {})
                    backend = params.get("backendNodeId")
                    if backend:
                        return backend
            time.sleep(0.25)
        return None

    # ── background.js port ───────────────────────────────────────────
    def hard_open(self):
        self.driver.get("about:blank")
        self._wait_loaded()
        self.driver.get(self.dashboard_url)
        self._wait_loaded()

    def _wait_loaded(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.driver.execute_script(
                        "return document.readyState") == "complete":
                    return
            except Exception:
                pass
            time.sleep(0.3)
        raise WebError("LinkedIn did not finish loading in time.")

    def configure_schedule(self, date_text, hhmm):
        wanted = expected_schedule(date_text, hhmm)
        self._click("schedulePost")
        self._wait_state(lambda s: s["scheduleDialogReady"], 30,
                         "LinkedIn's Schedule post dialog did not become "
                         "ready")
        self._replace_text("scheduleDate", wanted["date"], "Tab")
        self._wait_state(
            lambda s: s["schedule"]["date"] == wanted["date"], 15,
            "LinkedIn did not commit the requested schedule date")
        self._replace_text("scheduleTime", wanted["time"], "Enter")
        self._wait_state(
            lambda s: s["scheduleDialogReady"]
            and s["schedule"]["date"] == wanted["date"]
            and s["schedule"]["time"].lower() == wanted["time"].lower(),
            15, "LinkedIn did not commit the requested schedule time or "
                "enable Next")
        self._click("scheduleNext")
        self._wait_state(lambda s: s["scheduleFinalReady"], 30,
                         "LinkedIn's final Schedule button did not become "
                         "ready")

    def fill_caption(self, caption):
        self._locate("editor")
        self._focus_emulation()
        self._focus("editor")
        self._select_all()
        self.driver.execute_cdp_cmd("Input.insertText",
                                    {"text": caption})
        expected = _norm(caption)
        self._wait_state(
            lambda s: _norm(s["captionText"]) == expected, 15,
            "LinkedIn did not retain the complete caption and hashtags")

    def post_item(self, image_path, caption, date_text, hhmm):
        """Extension postItem(): create → media → next → schedule →
        caption → final Schedule. Returns True on success."""
        self.hard_open()
        self._wait_state(lambda s: s["createReady"], 60,
                         "LinkedIn's Create control did not appear")
        self._click("create")
        self._wait_state(lambda s: s["startPostReady"], 30,
                         "LinkedIn's Start a post control did not appear")
        self._click("startPost")
        self._wait_state(
            lambda s: s["addMediaReady"] and s["editorReady"], 30,
            "LinkedIn's post composer did not become ready")
        self._upload(image_path)
        is_video = (image_path or "").lower().endswith(
            (".mp4", ".mov", ".webm", ".m4v"))
        self._wait_state(
            lambda s: s["nextReady"], 600 if is_video else 180,
            "LinkedIn did not finish uploading %s"
            % os.path.basename(image_path))
        self._click("next")
        self._wait_state(
            lambda s: s["editorReady"] and s["postReady"], 60,
            "LinkedIn's final post screen did not become ready")
        self.configure_schedule(date_text, hhmm)
        self._wait_state(
            lambda s: s["editorReady"] and s["scheduleFinalReady"], 30,
            "LinkedIn's scheduled composer did not restore the caption "
            "editor")
        self.fill_caption(caption)
        self._wait_state(
            lambda s: _norm(s["captionText"]) == _norm(caption)
            and s["scheduleFinalReady"], 15,
            "LinkedIn did not retain the caption and hashtags after "
            "schedule selection")
        self._click("scheduleFinal")
        self._wait_state(
            lambda s: s["successNotice"] or not s["composerOpen"], 180,
            "LinkedIn did not confirm the scheduled post")
        time.sleep(4)
        return True

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass


def web_progress_path(store_dir_path):
    return os.path.join(store_dir_path, "web_progress.json")


def load_progress(store_dir_path):
    path = web_progress_path(store_dir_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_progress(store_dir_path, progress):
    path = web_progress_path(store_dir_path)
    os.makedirs(store_dir_path, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f)
    os.replace(tmp, path)
