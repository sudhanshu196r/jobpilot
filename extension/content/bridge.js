// Runs on the JobPilot web app (127.0.0.1 / localhost) so the page can ask the
// extension to apply to an application right now. Only talks to the JobPilot
// server you connected in the popup.
(() => {
  if (window.__jobpilotBridge) return;
  window.__jobpilotBridge = true;

  chrome.runtime.sendMessage({ type: "config" }, (res) => {
    if (chrome.runtime.lastError || !res?.ok) return;
    const server = (res.data.server || "").replace(/\/$/, "");
    if (location.origin !== server) return; // some other local app, not JobPilot
    document.documentElement.dataset.jobpilotExtension = chrome.runtime.getManifest().version;
    document.documentElement.dataset.jobpilotConnected = res.data.token ? "yes" : "no";
    window.dispatchEvent(new CustomEvent("jobpilot-extension-ready"));

    window.addEventListener("message", (e) => {
      if (e.source !== window || e.origin !== server || e.data?.source !== "jobpilot-web") return;
      const { id, type, payload } = e.data;
      if (!["applyNow", "queueState"].includes(type)) return;
      chrome.runtime.sendMessage({ type, ...(payload || {}) }, (reply) => {
        window.postMessage({ source: "jobpilot-ext", id,
          reply: reply || { ok: false, error: chrome.runtime.lastError?.message || "No response" } }, server);
      });
    });
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg?.type === "jobpilotChanged") window.postMessage({ source: "jobpilot-ext", event: "changed", detail: msg.detail }, server);
    });
  });
})();
