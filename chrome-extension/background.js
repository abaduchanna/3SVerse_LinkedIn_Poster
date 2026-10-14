const DASHBOARD_URL = "https://www.linkedin.com/company/144975022/admin/dashboard/";

const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const normalizeText = value => String(value || "").normalize("NFKC").replace(/\s+/g, " ").trim();
chrome.action.onClicked.addListener(() => chrome.tabs.create({ url: chrome.runtime.getURL("app.html") }));

async function workflowTab() {
  const stored = await chrome.storage.local.get("linkedinWorkflowTabId");
  if (stored.linkedinWorkflowTabId) {
    try {
      const tab = await chrome.tabs.get(stored.linkedinWorkflowTabId);
      if (/linkedin\.com/.test(tab.url || "")) return tab;
    } catch (_) {}
  }
  const tab = await chrome.tabs.create({ url: DASHBOARD_URL, active: false });
  await chrome.storage.local.set({ linkedinWorkflowTabId: tab.id });
  return tab;
}

async function waitForTab(tabId, timeout = 60000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return tab;
    await sleep(300);
  }
  throw new Error("LinkedIn did not finish loading in time.");
}

async function ensureContent(tabId) {
  const started = Date.now();
  while (Date.now() - started < 30000) {
    try { if ((await chrome.tabs.sendMessage(tabId, { type: "PING" }))?.ok) return; } catch (_) {}
    try { await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] }); } catch (_) {}
    await sleep(500);
  }
  throw new Error("LinkedIn loaded, but the uploader could not connect to the page.");
}

async function hardOpen(tabId, url) {
  await chrome.tabs.update(tabId, { url: "about:blank", active: false });
  await waitForTab(tabId, 15000);
  await chrome.tabs.update(tabId, { url, active: false });
  await waitForTab(tabId, 60000);
  await ensureContent(tabId);
}

async function readState(tabId) {
  const response = await chrome.tabs.sendMessage(tabId, { type: "GET_LINKEDIN_STATE" });
  if (!response?.ok) throw new Error(response?.error || "LinkedIn page state could not be read.");
  return response.state;
}

async function waitForState(tabId, predicate, timeout, description) {
  const started = Date.now();
  let state;
  while (Date.now() - started < timeout) {
    state = await readState(tabId);
    if (predicate(state)) return state;
    await sleep(400);
  }
  throw new Error(`${description}; ${state?.summary || "LinkedIn state unavailable"}`);
}

async function locate(tabId, name) {
  const response = await chrome.tabs.sendMessage(tabId, { type: "LOCATE_LINKEDIN_TARGET", target: name });
  if (!response?.ok) throw new Error(response?.error || `LinkedIn control not found: ${name}`);
  return response.point;
}

async function withDebugger(tabId, action) {
  const debuggee = { tabId };
  let attached = false;
  try {
    await chrome.debugger.attach(debuggee, "1.3");
    attached = true;
    try { await chrome.debugger.sendCommand(debuggee, "Emulation.setFocusEmulationEnabled", { enabled: true }); } catch (_) {}
    return await action(debuggee);
  } finally {
    if (attached) try { await chrome.debugger.detach(debuggee); } catch (_) {}
  }
}

async function dispatchClick(debuggee, point) {
  await chrome.debugger.sendCommand(debuggee, "Input.dispatchMouseEvent", { type: "mouseMoved", x: point.x, y: point.y });
  await chrome.debugger.sendCommand(debuggee, "Input.dispatchMouseEvent", { type: "mousePressed", x: point.x, y: point.y, button: "left", clickCount: 1 });
  await chrome.debugger.sendCommand(debuggee, "Input.dispatchMouseEvent", { type: "mouseReleased", x: point.x, y: point.y, button: "left", clickCount: 1 });
}

async function trustedClick(tabId, name) {
  const point = await locate(tabId, name);
  await withDebugger(tabId, debuggee => dispatchClick(debuggee, point));
}

async function focusTarget(tabId, name) {
  const response = await chrome.tabs.sendMessage(tabId, { type: "FOCUS_LINKEDIN_TARGET", target: name });
  if (!response?.ok) throw new Error(response?.error || `LinkedIn did not focus the ${name} field.`);
}

async function trustedReplaceText(tabId, name, text, commitKey) {
  await locate(tabId, name);
  await withDebugger(tabId, async debuggee => {
    await focusTarget(tabId, name);
    await chrome.debugger.sendCommand(debuggee, "Input.dispatchKeyEvent", { type: "rawKeyDown", key: "a", code: "KeyA", modifiers: 2, windowsVirtualKeyCode: 65 });
    await chrome.debugger.sendCommand(debuggee, "Input.dispatchKeyEvent", { type: "keyUp", key: "a", code: "KeyA", modifiers: 2, windowsVirtualKeyCode: 65 });
    await chrome.debugger.sendCommand(debuggee, "Input.insertText", { text });
    if (commitKey === "Enter") await sleep(300);
    const keys = commitKey === "Enter"
      ? [{ key: "ArrowDown", code: "ArrowDown", virtualKeyCode: 40 }, { key: "Enter", code: "Enter", virtualKeyCode: 13 }]
      : [{ key: "Tab", code: "Tab", virtualKeyCode: 9 }];
    for (const { key, code, virtualKeyCode } of keys) {
      await chrome.debugger.sendCommand(debuggee, "Input.dispatchKeyEvent", { type: "rawKeyDown", key, code, windowsVirtualKeyCode: virtualKeyCode, nativeVirtualKeyCode: virtualKeyCode });
      await chrome.debugger.sendCommand(debuggee, "Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode: virtualKeyCode, nativeVirtualKeyCode: virtualKeyCode });
    }
  });
}

async function foreground(tabId, action) {
  // Every trusted CDP action enables focus emulation, so the workflow remains
  // usable without stealing or depending on the operator's active tab.
  return action();
}

async function uploadFile(tabId, media) {
  const point = await locate(tabId, "addMedia");
  await withDebugger(tabId, async debuggee => {
    await chrome.debugger.sendCommand(debuggee, "Page.enable");
    await chrome.debugger.sendCommand(debuggee, "DOM.enable");
    await chrome.debugger.sendCommand(debuggee, "Runtime.enable");
    await chrome.debugger.sendCommand(debuggee, "Page.setInterceptFileChooserDialog", { enabled: true });
    const chooser = new Promise((resolve, reject) => {
      const timer = setTimeout(() => { chrome.debugger.onEvent.removeListener(listener); reject(new Error("LinkedIn's native media chooser did not open within 20 seconds.")); }, 20000);
      const listener = (source, method, params) => {
        if (source.tabId !== tabId || method !== "Page.fileChooserOpened") return;
        clearTimeout(timer);
        chrome.debugger.onEvent.removeListener(listener);
        resolve(params);
      };
      chrome.debugger.onEvent.addListener(listener);
    });
    await dispatchClick(debuggee, point);
    const event = await chooser;
    if (!event.backendNodeId) throw new Error("LinkedIn opened a file chooser without an upload input.");
    let attached = false;
    if (Array.isArray(media.bytes) && media.bytes.length) {
      const chunks = [];
      for (let index = 0; index < media.bytes.length; index += 32768) {
        chunks.push(String.fromCharCode(...media.bytes.slice(index, index + 32768)));
      }
      const resolved = await chrome.debugger.sendCommand(debuggee, "DOM.resolveNode", { backendNodeId: event.backendNodeId });
      const objectId = resolved?.object?.objectId;
      if (objectId) {
        const injected = await chrome.debugger.sendCommand(debuggee, "Runtime.callFunctionOn", {
          objectId,
          functionDeclaration: `function(media) {
            const binary = atob(media.base64);
            const bytes = new Uint8Array(binary.length);
            for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
            const transfer = new DataTransfer();
            transfer.items.add(new File([bytes], media.name, { type: media.type }));
            this.files = transfer.files;
            this.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
            this.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
            return this.files && this.files.length === 1;
          }`,
          arguments: [{ value: { name: media.name, type: media.type, base64: btoa(chunks.join("")) } }],
          returnByValue: true,
          awaitPromise: true
        });
        attached = injected?.result?.value === true && !injected?.exceptionDetails;
      }
    }
    if (!attached && media.path) {
      await chrome.debugger.sendCommand(debuggee, "DOM.setFileInputFiles", { files: [media.path], backendNodeId: event.backendNodeId });
      attached = true;
    }
    if (!attached) throw new Error("LinkedIn's intercepted media input did not accept the selected folder file.");
    await chrome.debugger.sendCommand(debuggee, "Page.setInterceptFileChooserDialog", { enabled: false });
  });
}

async function fillCaption(tabId, caption) {
  await locate(tabId, "editor");
  await withDebugger(tabId, async debuggee => {
    await focusTarget(tabId, "editor");
    await chrome.debugger.sendCommand(debuggee, "Input.dispatchKeyEvent", { type: "rawKeyDown", key: "a", code: "KeyA", modifiers: 2, windowsVirtualKeyCode: 65 });
    await chrome.debugger.sendCommand(debuggee, "Input.dispatchKeyEvent", { type: "keyUp", key: "a", code: "KeyA", modifiers: 2, windowsVirtualKeyCode: 65 });
    await chrome.debugger.sendCommand(debuggee, "Input.insertText", { text: caption });
  });
  const expected = normalizeText(caption);
  await waitForState(tabId, state => normalizeText(state.captionText) === expected, 15000, "LinkedIn did not retain the complete caption and hashtags");
}

function expectedSchedule(date, time) {
  const [year, month, day] = date.split("-");
  const [hours, minutes] = time.split(":").map(Number);
  return {
    date: `${month}/${day}/${year}`,
    time: `${hours % 12 || 12}:${String(minutes).padStart(2, "0")} ${hours >= 12 ? "PM" : "AM"}`
  };
}

async function configureSchedule(tabId, item) {
  await trustedClick(tabId, "schedulePost");
  await waitForState(tabId, state => state.scheduleDialogReady, 30000, "LinkedIn's Schedule post dialog did not become ready");
  const wanted = expectedSchedule(item.date, item.time);
  await trustedReplaceText(tabId, "scheduleDate", wanted.date, "Tab");
  await waitForState(tabId, state => state.schedule.date === wanted.date, 15000, "LinkedIn did not commit the requested schedule date");
  await trustedReplaceText(tabId, "scheduleTime", wanted.time, "Enter");
  await waitForState(tabId, state => state.scheduleDialogReady && state.schedule.date === wanted.date && state.schedule.time.toLowerCase() === wanted.time.toLowerCase(), 15000, "LinkedIn did not commit the requested schedule time or enable Next");
  await trustedClick(tabId, "scheduleNext");
  await waitForState(tabId, state => state.scheduleFinalReady, 30000, "LinkedIn's final Schedule button did not become ready");
}

async function postItem(item) {
  const tab = await workflowTab();
  await hardOpen(tab.id, DASHBOARD_URL);
  await waitForState(tab.id, state => state.createReady, 60000, "LinkedIn's Create control did not appear");
  await foreground(tab.id, async () => {
    await trustedClick(tab.id, "create");
    await waitForState(tab.id, state => state.startPostReady, 30000, "LinkedIn's Start a post control did not appear");
    await trustedClick(tab.id, "startPost");
    await waitForState(tab.id, state => state.addMediaReady && state.editorReady, 30000, "LinkedIn's post composer did not become ready");
    await uploadFile(tab.id, item.file);
    await waitForState(tab.id, state => state.nextReady, item.type === "video" ? 600000 : 180000, `LinkedIn did not finish uploading ${item.file.name}`);
    await trustedClick(tab.id, "next");
    await waitForState(tab.id, state => state.editorReady && state.postReady, 60000, "LinkedIn's final post screen did not become ready");
    await configureSchedule(tab.id, item);
    await waitForState(tab.id, state => state.editorReady && state.scheduleFinalReady, 30000, "LinkedIn's scheduled composer did not restore the caption editor");
    await fillCaption(tab.id, item.caption);
    await waitForState(tab.id, state => normalizeText(state.captionText) === normalizeText(item.caption) && state.scheduleFinalReady, 15000, "LinkedIn did not retain the caption and hashtags after schedule selection");
    await trustedClick(tab.id, "scheduleFinal");
    await waitForState(tab.id, state => state.successNotice || !state.composerOpen, 180000, "LinkedIn did not confirm the scheduled post");
    await sleep(4000);
  });
  return true;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "POST_LINKEDIN_ITEM") return;
  postItem(message.item).then(() => sendResponse({ ok: true })).catch(error => sendResponse({ ok: false, error: error.message }));
  return true;
});
