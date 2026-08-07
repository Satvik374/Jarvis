/**
 * Boots the real browser UI in jsdom and drives it with the same event
 * shapes jarvis/browser.py publishes, so the tabs/vitals/palette logic is
 * exercised rather than assumed.
 *
 * Run:
 *   npm i jsdom --prefix /tmp/jarvis-ui-test
 *   NODE_PATH=/tmp/jarvis-ui-test/node_modules node tests/test_browser_ui.mjs
 *
 * jsdom stays out of the repo (this is a Python project); the suite skips
 * with exit 0 when it is not installed.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const UI = join(dirname(fileURLToPath(import.meta.url)), "..", "jarvis", "browser_ui");

let JSDOM;
try {
  ({ JSDOM } = await import("jsdom"));
} catch {
  console.log("SKIP: jsdom not installed (npm i -D jsdom)");
  process.exit(0);
}

const html = readFileSync(join(UI, "index.html"), "utf8");
const appJs = readFileSync(join(UI, "app.js"), "utf8");

const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8765/#token=testtoken",
  pretendToBeVisual: true,
  runScripts: "outside-only",
});
const { window } = dom;
const { document } = window;

// Minimal stubs for the browser APIs app.js needs but jsdom lacks.
const rafQueue = [];
const flushRaf = () => { const q = rafQueue.splice(0); q.forEach((cb) => cb(0)); };
window.EventSource = class {
  constructor() { window.__es = this; }
  close() {}
};
window.ResizeObserver = class { observe() {} disconnect() {} };
window.fetch = async () => ({ json: async () => ({ ok: true, sessions: [], active_id: null }) });
window.HTMLCanvasElement.prototype.getContext = () => ({
  setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
  closePath() {}, fill() {}, stroke() {}, save() {}, restore() {}, translate() {},
  arc() {}, createLinearGradient: () => ({ addColorStop() {} }),
  createRadialGradient: () => ({ addColorStop() {} }), fillRect() {}, scale() {},
  rotate() {}, quadraticCurveTo() {}, bezierCurveTo() {}, ellipse() {}, clip() {},
  fillText() {}, measureText: () => ({ width: 0 }), setLineDash() {},
});
window.requestAnimationFrame = (cb) => { rafQueue.push(cb); return rafQueue.length; };
window.cancelAnimationFrame = () => {};
window.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
window.scrollTo = () => {};
window.Element.prototype.scrollIntoView = function () {};

window.eval(appJs);

const $ = (s) => document.querySelector(s);
const fire = (payload) => window.__es.onmessage({ data: JSON.stringify(payload) });
const results = [];
const test = (name, fn) => {
  try { fn(); results.push(["PASS", name]); }
  catch (e) { results.push(["FAIL", `${name} -> ${e.message}`]); }
};

// --- The UI must come up on the STREAM tab with the others hidden. ---
test("boots on stream tab", () => {
  assert.equal($("#tabBtnStream").getAttribute("aria-selected"), "true");
  assert.equal($("#tabVitals").hidden, true);
  assert.equal($("#tabCmds").hidden, true);
});

// --- All 17 real slash commands render in the CMDS tab. ---
test("renders all 17 slash commands", () => {
  assert.equal(document.querySelectorAll("#cmdList .cmd-item").length, 17);
  const names = [...document.querySelectorAll(".cmd-name")].map((n) => n.textContent);
  assert.ok(names.includes("/enhance") && names.includes("/quit"));
});

test("command filter narrows the list", () => {
  const input = $("#cmdFilter");
  input.value = "voice";
  input.dispatchEvent(new window.Event("input"));
  const shown = [...document.querySelectorAll(".cmd-name")].map((n) => n.textContent);
  assert.deepEqual(shown, ["/voice"]);
  input.value = "";
  input.dispatchEvent(new window.Event("input"));
  assert.equal(document.querySelectorAll(".cmd-item").length, 17);
});

// --- Tab switching toggles hidden + aria-selected together. ---
test("switching to vitals shows that panel only", () => {
  $("#tabBtnVitals").dispatchEvent(new window.Event("click"));
  flushRaf(); // selectTab defers renderVitals to rAF
  assert.equal($("#tabVitals").hidden, false);
  assert.equal($("#tabStream").hidden, true);
  assert.equal($("#tabBtnVitals").getAttribute("aria-selected"), "true");
  assert.equal($("#tabBtnStream").getAttribute("aria-selected"), "false");
});

// --- Real backend event shapes drive the counters. ---
test("activity events count into VITALS bars", () => {
  fire({ event: "activity", kind: "step", message: "Opening browser" });
  fire({ event: "activity", kind: "step", message: "Clicking login" });
  fire({ event: "activity", kind: "think", message: "Deciding next move" });
  flushRaf(); // debounced renderVitals fires here
  const rows = [...document.querySelectorAll("#kindBars .bar-row")];
  const step = rows.find((r) => r.dataset.kind === "step");
  assert.ok(step, "expected a step bar row");
  assert.equal(step.querySelector(".bar-value").textContent, "2");
  // The largest bar must be full width.
  assert.equal(step.querySelector(".bar-fill").style.getPropertyValue("--bar"), "100%");
});

test("HUD step counter tracks step activities", () => {
  assert.equal($("#hudSteps").textContent, "2");
});

// --- Warnings and errors must mirror into LOGS and raise the badge. ---
test("errors mirror into the alerts tab", () => {
  fire({ event: "activity", kind: "error", message: "Tool call failed" });
  fire({ event: "activity", kind: "warn", message: "Retrying once" });
  assert.equal(document.querySelectorAll("#alertList .alert-item").length, 2);
  assert.equal($("#hudAlerts").textContent, "2");
  assert.equal($("#tabCountLogs").textContent, "2");
  assert.equal($("#tabCountLogs").dataset.alert, "true");
});

test("clearing alerts resets the badge", () => {
  $("#clearAlerts").dispatchEvent(new window.Event("click"));
  assert.equal(document.querySelectorAll("#alertList .alert-item").length, 0);
  assert.equal($("#hudAlerts").textContent, "0");
  assert.equal($("#tabCountLogs").dataset.alert, "false");
});

// --- Message tallies come from the input/assistant events. ---
test("message counter splits user vs assistant", () => {
  fire({ event: "input", message: "open notepad" });
  fire({ event: "assistant", message: "Done." });
  fire({ event: "assistant", message: "Anything else?" });
  flushRaf(); // debounced renderVitals writes vitalMessages
  assert.equal($("#vitalMessages").textContent, "1/2");
});

// --- State transitions build the ribbon. ---
test("state changes append ribbon cells", () => {
  const before = document.querySelectorAll("#stateRibbon .ribbon-cell").length;
  fire({ event: "state", state: "thinking" });
  fire({ event: "state", state: "acting" });
  const after = document.querySelectorAll("#stateRibbon .ribbon-cell").length;
  assert.equal(after, before + 2);
});

test("repeated identical state does not duplicate a ribbon cell", () => {
  const before = document.querySelectorAll("#stateRibbon .ribbon-cell").length;
  fire({ event: "state", state: "acting" });
  assert.equal(document.querySelectorAll("#stateRibbon .ribbon-cell").length, before);
});

// --- Unseen-activity badge only counts while STREAM is not focused. ---
test("stream badge counts activity while another tab is open", () => {
  fire({ event: "activity", kind: "info", message: "background note" });
  assert.notEqual($("#tabCountStream").textContent, "0");
  $("#tabBtnStream").dispatchEvent(new window.Event("click"));
  assert.equal($("#tabCountStream").textContent, "0");
});

// --- Command palette. ---
test("Ctrl+K opens the palette", () => {
  document.dispatchEvent(new window.KeyboardEvent("keydown", {
    key: "k", ctrlKey: true, bubbles: true,
  }));
  assert.equal($("#paletteBackdrop").hidden, false);
  assert.ok(document.querySelectorAll(".palette-item").length > 0);
});

test("palette search matches real commands", () => {
  const input = $("#paletteInput");
  input.value = "vision";
  input.dispatchEvent(new window.Event("input"));
  const names = [...document.querySelectorAll(".palette-name")].map((n) => n.textContent);
  assert.ok(names.includes("/vision"), `expected /vision in ${JSON.stringify(names)}`);
});

test("arrow keys move palette selection", () => {
  const input = $("#paletteInput");
  input.value = "";
  input.dispatchEvent(new window.Event("input"));
  const first = () => document.querySelectorAll(".palette-item")[0];
  assert.equal(first().getAttribute("aria-selected"), "true");
  input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  assert.equal(first().getAttribute("aria-selected"), "false");
  assert.equal(
    document.querySelectorAll(".palette-item")[1].getAttribute("aria-selected"), "true",
  );
});

test("Escape closes the palette", () => {
  $("#paletteInput").dispatchEvent(
    new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
  );
  assert.equal($("#paletteBackdrop").hidden, true);
});

// --- Offline must not throw and must still be reflected. ---
test("session end sets the offline state cleanly", () => {
  fire({ event: "session", alive: false, message: "Terminal exited" });
  assert.equal(document.documentElement.dataset.state, "offline");
});

let failed = 0;
for (const [status, name] of results) {
  if (status === "FAIL") failed += 1;
  console.log(`${status}  ${name}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
