// Application forms on ATS sites (Greenhouse, Lever, Ashby, Workday, Indeed
// smartapply, ...). With a queue task: fill automatically. Without one: offer
// a one-click "Autofill" button.
(() => {
  const { runTask, setPanel, autofill, learnAnswers, collectFields } = window.JP;

  async function init() {
    // In iframes (embedded Greenhouse forms) only act when there is a form here.
    if (window.top !== window && !document.querySelector("form, input[type=file]")) return;
    const task = await runTask();
    if (task || window.top !== window) return;
    // Wait for the SPA form to render before offering help.
    let tries = 0;
    const timer = setInterval(() => {
      if (collectFields().length >= 3 || ++tries > 20) {
        clearInterval(timer);
        if (collectFields().length < 3) return;
        const log = (m) => { const el = document.getElementById("jobpilot-panel-host")?.shadowRoot?.querySelector(".log"); if (el) el.textContent = m; };
        setPanel(`<div class="t">Application form detected</div><div class="log">Fill it from your JobPilot profile and saved answers.</div>
          <div class="row"><button class="a pr" data-fill>Autofill</button><button class="a" data-learn>Save my answers</button></div>`, {
          "[data-fill]": () => autofill({ useAi: true, log }).catch((e) => log(e.message)),
          "[data-learn]": async () => log(`Saved ${await learnAnswers()} answer(s).`),
        });
      }
    }, 750);
  }
  init();
})();
