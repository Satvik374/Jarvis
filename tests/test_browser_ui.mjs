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
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const UI = join(dirname(fileURLToPath(import.meta.url)), "..", "jarvis", "browser_ui");

let jsdomPath;
try {
  jsdomPath = createRequire(import.meta.url).resolve("jsdom");
} catch (error) {
  if (error.code !== "MODULE_NOT_FOUND") throw error;
  console.log("SKIP: jsdom not installed (npm i -D jsdom)");
  process.exit(0);
}
const { JSDOM } = await import(pathToFileURL(jsdomPath).href);

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
// Path-aware so the skill library can be exercised without a live server.
window.__skills = [
  {
    name: "research-brief",
    slug: "research-brief",
    description: "Research a question and produce a short brief with sources.",
    when_to_use: "research, look up, compare",
    tools: ["web_search", "read_url"],
    builtin: true,
    updated: "2026-01-01",
  },
  {
    name: "weekly-report",
    slug: "weekly-report",
    description: "Compile my weekly status report.",
    when_to_use: "weekly report",
    tools: [],
    builtin: false,
    updated: "2026-01-02",
  },
];
window.fetch = async (path) => ({
  ok: true,
  status: 200,
  json: async () => String(path).startsWith("/api/skills")
    ? { ok: true, skills: window.__skills, active: "" }
    : { ok: true, sessions: [], active_id: null },
});
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

// --- The same surface becomes a read-mostly remote-agent dashboard. ---
test("remote-agent session switches the interface mode", () => {
  fire({ event: "session", alive: true, interface_mode: "remote-agent" });
  assert.equal(document.documentElement.dataset.interface, "remote-agent");
  assert.equal($("#brandSubtitle").textContent, "REMOTE AGENT");
  assert.equal($("#tabCmdsLabel").textContent, "LINKS");
  assert.match($("#promptInput").placeholder, /controller/i);
});

test("remote task events render controller work and results", () => {
  fire({
    event: "remote",
    status: "task_received",
    controller: "Studio PC",
    task: "Open the release notes",
  });
  fire({
    event: "remote",
    status: "task_result",
    controller: "Studio PC",
    ok: true,
    result: "Release notes opened.",
  });
  const text = $("#messages").textContent;
  assert.match(text, /Studio PC/);
  assert.match(text, /Release notes opened/);
});

// --- Offline must not throw and must still be reflected. ---
test("session end sets the offline state cleanly", () => {
  fire({ event: "session", alive: false, message: "Terminal exited" });
  assert.equal(document.documentElement.dataset.state, "offline");
});

// --- The side panel must never be a dead end on a narrow window. ---
test("panel toggle opens the activity drawer and Escape closes it", () => {
  const toggle = $("#panelToggle");
  assert.ok(toggle, "a panel toggle must exist for widths where the panel does not fit");
  toggle.dispatchEvent(new window.Event("click"));
  assert.equal(document.body.classList.contains("panel-open"), true);
  assert.equal($("#panelBackdrop").hidden, false);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(document.body.classList.contains("panel-open"), false);
  assert.equal($("#panelBackdrop").hidden, true);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
});

// --- Skills: the library the agent can load, staged from the UI. ---
const settleFrame = () => new Promise((resolve) => setImmediate(resolve));
{
  // Back to the console shell: the remote-agent tests above left the page in
  // monitor mode, where the local skill library is deliberately not offered.
  fire({ event: "session", alive: true, interface_mode: "console" });
  results.push(
    $("#tabBtnSkills").hidden === false
      ? ["PASS", "skills tab returns with the console interface"]
      : ["FAIL", "skills tab returns with the console interface"],
  );
  fire({ event: "session", alive: true, interface_mode: "remote-agent" });
  results.push(
    $("#tabBtnSkills").hidden === true
      ? ["PASS", "remote-agent mode hides the local skill library"]
      : ["FAIL", "remote-agent mode hides the local skill library"],
  );
  fire({ event: "session", alive: true, interface_mode: "console" });

  $("#tabBtnSkills").dispatchEvent(new window.Event("click"));
  await settleFrame();
  const items = [...document.querySelectorAll("#skillList .skill-item")];
  if (items.length !== 2) {
    results.push(["FAIL", `skills tab renders the library -> ${items.length} items`]);
  } else {
    results.push(["PASS", "skills tab renders the library"]);
  }
  const badge = document.querySelector("#skillList .skill-badge");
  results.push(
    badge && badge.textContent === "BUILT-IN"
      ? ["PASS", "preset skills are marked built-in"]
      : ["FAIL", "preset skills are marked built-in"],
  );
  $("#skillList .skill-use").dispatchEvent(new window.Event("click"));
  const prompt = $("#promptInput").value;
  results.push(
    prompt === "Use the research-brief skill to "
      ? ["PASS", "USE stages a directive that loads that skill"]
      : ["FAIL", `USE stages a directive that loads that skill -> ${prompt}`],
  );
  const filter = $("#skillFilter");
  filter.value = "weekly";
  filter.dispatchEvent(new window.Event("input"));
  const shown = [...document.querySelectorAll("#skillList .skill-name")].map((n) => n.textContent);
  results.push(
    shown.length === 1 && shown[0] === "weekly-report"
      ? ["PASS", "skill filter narrows the library"]
      : ["FAIL", `skill filter narrows the library -> ${JSON.stringify(shown)}`],
  );
  filter.value = "";
  filter.dispatchEvent(new window.Event("input"));
  $("#promptInput").value = "";
}

// --- Discoverability: ? lists the keys, / reaches the composer. ---
test("? opens the shortcuts overlay and Escape closes it", () => {
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "?", bubbles: true }));
  assert.equal($("#shortcutsBackdrop").hidden, false);
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal($("#shortcutsBackdrop").hidden, true);
});

test("/ focuses the composer", () => {
  // The composer is disabled until a live runtime asks for input, and a
  // disabled field cannot take focus - so make it a real, live composer first.
  fire({ event: "session", alive: true, interface_mode: "console" });
  fire({ event: "input_request", prompt: "Awaiting directive", mode: "command" });
  assert.equal($("#promptInput").disabled, false);
  document.body.focus();
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "/", bubbles: true }));
  assert.equal(document.activeElement, $("#promptInput"));
});

test("keys typed into a field are not stolen by global shortcuts", () => {
  const input = $("#promptInput");
  input.value = "what time is it?";
  input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "?", bubbles: true }));
  assert.equal($("#shortcutsBackdrop").hidden, true);
  assert.equal(input.value, "what time is it?");
  input.value = "";
});

// Drive the real live toggle and mocked websocket, without mic/provider access.
try {
  const sent = [];
  let socket;
  let interruptOk = true;
  window.fetch = async (path) => ({
    ok: true,
    json: async () => path === "/api/interrupt"
      ? { ok: interruptOk, message: interruptOk ? "interrupt requested" : "Jarvis is ready for the next directive" }
      : { ok: true, sessions: [], ws_url: "ws://test.invalid/realtime" },
  });
  window.WebSocket = class {
    static OPEN = 1;
    constructor() { this.readyState = 1; socket = this; }
    send(data) { sent.push(JSON.parse(data)); }
    close() { this.readyState = 3; }
  };
  Object.defineProperty(window.navigator, "mediaDevices", { value: {
    getUserMedia: async () => ({ getTracks: () => [], getAudioTracks: () => [] }),
  } });
  const audioNode = () => ({ connect() {}, disconnect() {}, gain: { value: 0 }, frequencyBinCount: 32 });
  window.AudioContext = class {
    constructor() { this.state = "running"; this.currentTime = 0; this.destination = {}; }
    createMediaStreamSource() { return audioNode(); }
    createAnalyser() { return audioNode(); }
    createScriptProcessor() { return audioNode(); }
    createGain() { return audioNode(); }
    close() { return Promise.resolve(); }
  };
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  $("#liveVoiceToggle").click();
  await settle();
  assert.ok(socket, "live toggle should open the mocked websocket");
  socket.onopen();
  for (const [callId, ok] of [["cancel-active", true], ["cancel-idle", false]]) {
    interruptOk = ok;
    socket.onmessage({ data: JSON.stringify({
      type: "response.function_call_arguments.done", call_id: callId,
      name: "cancel_task", arguments: "{}",
    }) });
    await settle();
    const item = sent.find((event) => event.item?.call_id === callId)?.item;
    assert.ok(item, "cancellation tool must send an output");
    const output = JSON.parse(item.output);
    assert.equal(output.status, ok ? "cancelling" : "error");
    assert.equal(output.message, ok ? "interrupt requested" : "Jarvis is ready for the next directive");
  }
  results.push(["PASS", "live cancellation acknowledges request, not completion"]);
} catch (error) {
  results.push(["FAIL", `live cancellation acknowledges request, not completion -> ${error.message}`]);
} finally {
  $("#voiceExitBtn").click();
}

let failed = 0;
for (const [status, name] of results) {
  if (status === "FAIL") failed += 1;
  console.log(`${status}  ${name}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
