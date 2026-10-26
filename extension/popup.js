const $ = (id) => document.getElementById(id);
const send = (type, payload = {}) => new Promise((resolve, reject) =>
  chrome.runtime.sendMessage({ type, ...payload }, (res) => {
    if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
    res?.ok ? resolve(res.data) : reject(new Error(res?.error || "No response"));
  }));

let cfg;

async function init() {
  cfg = await send("config");
  $("server").value = cfg.server;
  $("token").value = cfg.token;
  if (!cfg.token) return showConnect();
  try {
    const ping = await send("api", { path: "/api/ext/ping" });
    $("st").textContent = ping.name ? ping.name.split(" ")[0] : "connected";
    showMain();
  } catch (e) {
    showConnect(e.message);
  }
}

function showConnect(err = "") {
  $("connect").hidden = false; $("main").hidden = true;
  $("st").textContent = "offline"; $("cerr").textContent = err;
}

async function showMain() {
  $("connect").hidden = true; $("main").hidden = false;
  for (const k of ["autoFill", "autoClickApply", "useAi"]) $(k).checked = !!cfg[k];
  $("capture_opt").checked = !!cfg.capture;
  await renderQueue();
}

async function renderQueue(state) {
  state = state || await send("queueState");
  let info = "";
  try {
    const q = await send("api", { path: "/api/ext/queue" });
    const ready = q.items.filter((i) => i.status === "queued").length;
    info = `${ready} job(s) ready${q.preparing ? `, ${q.preparing} preparing` : ""}.`;
    $("start").disabled = !q.items.length && !state.running;
  } catch (e) { info = e.message; }
  if (state.running && state.current) info = `Applying: ${state.current.job.title} @ ${state.current.job.company}. ${info}`;
  if (state.done || state.skipped) info += ` This session: ${state.done} applied, ${state.skipped} skipped.`;
  if (state.message) info += ` ${state.message}`;
  $("qinfo").textContent = info;
  $("start").hidden = state.running; $("stop").hidden = !state.running;
}

$("save").onclick = async () => {
  const server = $("server").value.trim().replace(/\/$/, "") || "http://127.0.0.1:8765";
  cfg = await send("saveConfig", { values: { server, token: $("token").value.trim() } });
  init();
};
$("disconnect").onclick = (e) => { e.preventDefault(); showConnect(); };
$("start").onclick = async () => { try { await renderQueue(await send("startQueue")); } catch (e) { $("qinfo").textContent = e.message; } };
$("stop").onclick = async () => renderQueue(await send("stopQueue"));
$("open").onclick = () => chrome.tabs.create({ url: cfg.server });
$("fill").onclick = async () => {
  try {
    const r = await send("runInActiveTab", { action: "jp:autofill" });
    $("pinfo").textContent = r?.error ? r.error : `Filled ${r.filled} field(s)${r.review ? `, ${r.review} AI draft(s) to review` : ""}${r.unanswered ? `, ${r.unanswered} need you` : ""}.`;
  } catch (e) { $("pinfo").textContent = e.message; }
};
$("capture").onclick = async () => {
  try {
    const r = await send("runInActiveTab", { action: "jp:capturePage" });
    $("pinfo").textContent = r?.error ? r.error : `Saved — match score ${Math.round(r.score ?? 0)}. Open JobPilot to tailor a resume.`;
  } catch (e) { $("pinfo").textContent = e.message; }
};
for (const [id, key] of [["autoFill", "autoFill"], ["autoClickApply", "autoClickApply"], ["useAi", "useAi"], ["capture_opt", "capture"]]) {
  $(id).onchange = async (e) => { cfg = await send("saveConfig", { values: { [key]: e.target.checked } }); };
}
chrome.runtime.onMessage.addListener((msg) => { if (msg.type === "queueChanged") renderQueue(msg.state); });
init();
