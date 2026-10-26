// JobPilot service worker: talks to the local server and runs the apply queue.
// Content scripts never call the server directly (page CORS would block them);
// everything goes through the messages handled here.

const DEFAULTS = { server: "http://127.0.0.1:8765", token: "", autoFill: true, autoClickApply: false, capture: true, useAi: true };

async function config() {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  return { ...DEFAULTS, ...stored };
}

async function api(path, { method = "GET", body } = {}) {
  const cfg = await config();
  if (!cfg.token) throw new Error("JobPilot is not connected. Open the extension popup and add your token.");
  const res = await fetch(cfg.server.replace(/\/$/, "") + path, {
    method,
    headers: { "X-JobPilot-Token": cfg.token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const type = res.headers.get("content-type") || "";
  if (!res.ok) {
    const detail = type.includes("json") ? (await res.json()).detail : await res.text();
    throw new Error(typeof detail === "string" ? detail : `Server error ${res.status}`);
  }
  return type.includes("json") ? res.json() : res;
}

// ------------------------------------------------------------------ queue state (survives worker restarts)

async function getState() {
  const { queue } = await chrome.storage.session.get("queue");
  return queue || { running: false, tabId: null, current: null, done: 0, skipped: 0 };
}
async function setState(patch) {
  const next = { ...(await getState()), ...patch };
  await chrome.storage.session.set({ queue: next });
  chrome.runtime.sendMessage({ type: "queueChanged", state: next }).catch(() => {});
  return next;
}

async function openNext() {
  const state = await getState();
  if (!state.running) return state;
  const { items, preparing } = await api("/api/ext/queue");
  const next = items[0];
  if (!next) {
    const msg = preparing ? `${preparing} resume(s) still being prepared — start again in a moment.` : "Queue finished.";
    return setState({ running: false, current: null, message: msg });
  }
  await api(`/api/ext/applications/${next.application_id}/status`, { method: "POST", body: { status: "in_progress" } });
  let tabId = state.tabId;
  try {
    if (tabId != null) await chrome.tabs.update(tabId, { url: next.job.url, active: true });
    else throw new Error("no tab");
  } catch {
    const tab = await chrome.tabs.create({ url: next.job.url, active: true });
    tabId = tab.id;
  }
  return setState({ tabId, current: next, remaining: items.length - 1, message: "" });
}

async function finishCurrent(status, note = "") {
  const state = await getState();
  if (!state.current) return state;
  await api(`/api/ext/applications/${state.current.application_id}/status`, { method: "POST", body: { status, note } });
  await setState({ current: null, done: state.done + (status === "applied" ? 1 : 0), skipped: state.skipped + (status === "skipped" ? 1 : 0) });
  return openNext();
}

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const state = await getState();
  if (state.running && state.tabId === tabId) {
    if (state.current) {
      await api(`/api/ext/applications/${state.current.application_id}/status`, { method: "POST", body: { status: "queued" } }).catch(() => {});
    }
    await setState({ running: false, tabId: null, current: null, message: "Queue paused (tab closed)." });
  }
});

// ------------------------------------------------------------------ messages

function toBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

const handlers = {
  async api({ path, method, body }) { return api(path, { method, body }); },
  async config() { return config(); },
  async saveConfig({ values }) { await chrome.storage.local.set(values); return config(); },
  async resumeFile({ resumeId }) {
    const res = await api(`/api/ext/resumes/${resumeId}/pdf`);
    const disposition = res.headers.get("content-disposition") || "";
    const name = decodeURIComponent((disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i) || [])[1] || "resume.pdf");
    return { name, base64: toBase64(await res.arrayBuffer()) };
  },
  async getTask(_msg, sender) {
    const state = await getState();
    if (state.running && state.current && sender.tab && sender.tab.id === state.tabId) {
      const cfg = await config();
      return { ...state.current, autoFill: cfg.autoFill, autoClickApply: cfg.autoClickApply, useAi: cfg.useAi, remaining: state.remaining };
    }
    return null;
  },
  async taskDone({ status, note }) { return finishCurrent(status, note); },
  async startQueue() { await setState({ running: true, done: 0, skipped: 0, message: "" }); return openNext(); },
  async stopQueue() {
    const state = await getState();
    if (state.current) await api(`/api/ext/applications/${state.current.application_id}/status`, { method: "POST", body: { status: "queued" } }).catch(() => {});
    return setState({ running: false, current: null, message: "Queue stopped." });
  },
  async queueState() { return getState(); },
  async runInActiveTab({ action }) {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !/^https?:/.test(tab.url || "")) throw new Error("Open a job page first.");
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content/common.js"] });
    return chrome.tabs.sendMessage(tab.id, { type: action });
  },
};

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const fn = handlers[msg?.type];
  if (!fn) return false;
  fn(msg, sender).then((data) => sendResponse({ ok: true, data }), (err) => sendResponse({ ok: false, error: err.message || String(err) }));
  return true; // async response
});
