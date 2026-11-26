/* C65 — testvärd för MCP Apps-vyerna (används av test_c65.py).
 *
 * Läser JSON på stdin: {scenarios: [{name, html, result, responses, actions, screenshot}]}
 * Varje vy laddas i en sandboxad iframe (allow-scripts, som hos värden). Värden
 * svarar på ui/initialize, skickar tool-input + tool-result och svarar på
 * tools/call med färdiga svar. Skriver JSON på stdout: per scenario
 * {text, calls, attrs, error}.
 */
const { chromium } = require("playwright");

const HOST = `<!doctype html><html><body style="margin:0">
<iframe id="v" sandbox="allow-scripts" style="width:900px;height:1400px;border:0"></iframe>
<script>
window.__calls = []; window.__log = [];
window.addEventListener("message", function (ev) {
  var f = document.getElementById("v");
  if (ev.source !== f.contentWindow) return;
  var m = ev.data; window.__log.push(m.method || ("resp:" + m.id));
  function reply(obj) { f.contentWindow.postMessage(Object.assign({ jsonrpc: "2.0", id: m.id }, obj), "*"); }
  function note(method, params) { f.contentWindow.postMessage({ jsonrpc: "2.0", method: method, params: params }, "*"); }
  if (m.method === "ui/initialize") {
    reply({ result: { protocolVersion: "2026-01-26", hostCapabilities: {}, hostInfo: { name: "test-host", version: "1" },
                      hostContext: { theme: "light", toolInfo: { tool: { name: window.__tool } } } } });
  } else if (m.method === "ui/notifications/initialized") {
    note("ui/notifications/tool-input", { arguments: {} });
    note("ui/notifications/tool-result", window.__result);
  } else if (m.method === "tools/call") {
    window.__calls.push(m.params);
    var r = window.__responses[m.params.name];
    if (r === undefined) reply({ error: { code: -32000, message: "no canned answer" } });
    else reply({ result: { content: [{ type: "text", text: JSON.stringify(r) }], structuredContent: r } });
  } else if (m.method === "ui/open-link") {
    window.__calls.push({ name: "ui/open-link", arguments: m.params }); reply({ result: {} });
  } else if (m.id !== undefined && m.method) {
    reply({ result: {} });
  }
});
</script></body></html>`;

async function run(browser, sc) {
  const page = await browser.newPage({ viewport: { width: 920, height: 900 }, deviceScaleFactor: sc.scale || 1 });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.setContent(HOST);
  await page.evaluate((s) => {
    window.__tool = s.tool || null;
    window.__result = { content: [{ type: "text", text: JSON.stringify(s.result) }], structuredContent: s.result };
    window.__responses = s.responses || {};
    document.getElementById("v").srcdoc = s.html;
  }, sc);
  await page.waitForTimeout(300);
  const frame = page.frames().find((f) => f !== page.mainFrame());
  frame.on && frame.on("pageerror", (e) => errors.push(String(e)));
  await frame.waitForSelector(sc.waitFor || "h1", { timeout: 5000 });
  await page.waitForTimeout(sc.settle || 300);
  const out = { steps: [] };
  for (const a of sc.actions || []) {
    if (a.click) {
      await frame.click(a.click, { timeout: 3000 });
      await page.waitForTimeout(a.wait || 300);
    }
    if (a.snapshot) {
      out.steps.push({
        text: await frame.evaluate(() => document.body.innerText),
        calls: await page.evaluate(() => JSON.parse(JSON.stringify(window.__calls))),
      });
    }
    if (a.eval) {
      out.steps.push({ eval: await frame.evaluate(a.eval) });
    }
  }
  out.text = await frame.evaluate(() => document.body.innerText);
  out.html = await frame.evaluate(() => document.getElementById("app").innerHTML);
  out.calls = await page.evaluate(() => JSON.parse(JSON.stringify(window.__calls)));
  out.log = await page.evaluate(() => window.__log);
  out.attrs = {};
  for (const [key, sel] of Object.entries(sc.attrs || {})) {
    out.attrs[key] = await frame.evaluate((s) =>
      Array.from(document.querySelectorAll(s[0])).map((n) => n.getAttribute(s[1])), sel);
  }
  out.buttons = await frame.evaluate(() =>
    Array.from(document.querySelectorAll("button")).map((b) => b.textContent));
  if (sc.screenshot) {
    const h = await frame.evaluate(() => Math.ceil(document.getElementById("app").getBoundingClientRect().bottom) + 12);
    await page.setViewportSize({ width: 920, height: Math.min(Math.max(h + 20, 200), 3000) });
    await page.screenshot({ path: sc.screenshot, clip: { x: 0, y: 0, width: 900, height: Math.min(h + 10, 3000) } });
  }
  out.errors = errors;
  await page.close();
  return out;
}

(async () => {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const { scenarios } = JSON.parse(input);
  const browser = await chromium.launch();
  const results = {};
  try {
    for (const sc of scenarios) {
      try { results[sc.name] = await run(browser, sc); }
      catch (e) { results[sc.name] = { error: String(e && e.stack || e) }; }
    }
  } finally { await browser.close(); }
  process.stdout.write(JSON.stringify(results));
})();
