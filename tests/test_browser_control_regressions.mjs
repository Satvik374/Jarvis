/** Browser control races, using the real UI and mocked transports only.
 * Run: node --test tests/test_browser_control_regressions.mjs
 * Uses the same external jsdom dependency as test_browser_ui.mjs.
 */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import test from "node:test";

const { JSDOM } = createRequire(import.meta.url)("jsdom");
const ui = new URL("../jarvis/browser_ui/", import.meta.url);
const html = readFileSync(new URL("index.html", ui), "utf8");
const app = readFileSync(new URL("app.js", ui), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const response = body => ({ ok: true, status: 200, json: async () => body });
const ready = { ok: true, alive: true, accepting_input: true, state: "listening" };

async function boot(t, { fish = false } = {}) {
  const dom = new JSDOM(html, {
    url: "http://127.0.0.1:8765/#token=testtoken",
    pretendToBeVisual: true,
    runScripts: "outside-only",
  });
  t.after(() => dom.window.close());
  const { window } = dom;
  const requests = [];
  const streams = [];
  const sockets = [];
  window.EventSource = class {
    constructor() { this.closed = false; streams.push(this); }
    close() { this.closed = true; }
  };
  window.WebSocket = class {
    static OPEN = 1;
    static CLOSED = 3;
    constructor(url) { this.url = url; this.readyState = 1; this.sent = []; sockets.push(this); }
    send(raw) { this.sent.push(JSON.parse(raw)); }
    close() { this.readyState = 3; }
  };
  window.ResizeObserver = class { observe() {} disconnect() {} };
  window.requestAnimationFrame = () => 1;
  window.cancelAnimationFrame = () => {};
  window.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  window.scrollTo = () => {};
  window.Element.prototype.scrollIntoView = () => {};
  window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
    get: (_target, key) => key === "measureText" ? () => ({ width: 0 })
      : key.startsWith("create") ? () => ({ addColorStop() {} }) : () => {},
  });
  const audioNode = () => ({ connect() {}, disconnect() {}, gain: { value: 0 }, frequencyBinCount: 32 });
  window.AudioContext = class {
    constructor() { this.state = "running"; this.currentTime = 0; this.destination = {}; }
    createMediaStreamSource() { return audioNode(); }
    createAnalyser() { return audioNode(); }
    createScriptProcessor() { return audioNode(); }
    createGain() { return audioNode(); }
    close() { return Promise.resolve(); }
  };
  const tracks = [];
  const makeStream = () => {
    const track = { stopped: false, stop() { this.stopped = true; } };
    tracks.push(track);
    return { getTracks: () => [track], getAudioTracks: () => [track] };
  };
  let microphoneCalls = 0;
  Object.defineProperty(window.navigator, "mediaDevices", { value: {
    getUserMedia: async () => { microphoneCalls += 1; return makeStream(); },
  } });
  const defaults = path => {
    if (path === "/api/state") return ready;
    if (path === "/api/live/config") return { ok: true, provider: "realtime", ws_url: "ws://test.invalid/realtime" };
    return { ok: true, sessions: [], skills: [], active_id: null };
  };
  let handle = path => response(defaults(path));
  window.fetch = (path, options) => {
    const request = { path, body: options.body ? JSON.parse(options.body) : undefined };
    requests.push(request);
    return Promise.resolve(handle(path, request));
  };
  // Only substitute the module loader: the Fish startup/control logic stays real.
  window.eval(fish ? app.replace('import("/vendor/fish-agent-client.esm.js")', 'Promise.resolve(window.__fishSdk)') : app);
  await streams[0].onopen();
  await settle();
  const $ = selector => window.document.querySelector(selector);
  const fire = (payload, source = streams.at(-1)) => source.onmessage({ data: JSON.stringify(payload) });
  const type = text => { $("#promptInput").value = text; $("#promptInput").dispatchEvent(new window.Event("input")); };
  const submit = () => $("#promptInput").dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  return {
    window, $, fire, type, submit, streams, sockets, requests, tracks, makeStream,
    microphoneCalls: () => microphoneCalls,
    route: fn => { handle = (path, request) => fn(path, request) ?? response(defaults(path)); },
  };
}

test("a pending link diagnosis cannot close an explicitly reconnected stream", async t => {
  const ui = await boot(t);
  const diagnosis = deferred();
  ui.route(path => path === "/api/state" ? diagnosis.promise : undefined);
  const pending = ui.streams[0].onerror();
  ui.$("#reconnect").click();
  const current = ui.streams.at(-1);
  ui.route(() => undefined);
  await current.onopen();
  diagnosis.resolve(response({ ok: true, alive: false }));
  await pending;
  assert.equal(current.closed, false);
  assert.equal(ui.$("#connectionText").textContent, "LINKED");
  assert.equal(ui.$("#promptInput").disabled, false);
});

test("a pending link diagnosis cannot undo automatic stream recovery", async t => {
  const ui = await boot(t);
  const diagnosis = deferred();
  ui.route(path => path === "/api/state" ? diagnosis.promise : undefined);
  const current = ui.streams[0];
  const pending = current.onerror();
  ui.route(() => undefined);
  await current.onopen();
  diagnosis.resolve(response({ ok: true, alive: false }));
  await pending;
  assert.equal(current.closed, false);
  assert.equal(ui.$("#connectionText").textContent, "LINKED");
});

test("an old stream snapshot cannot override a newer connection", async t => {
  const ui = await boot(t);
  const snapshot = deferred();
  ui.route(path => path === "/api/state" ? snapshot.promise : undefined);
  const pending = ui.streams[0].onopen();
  ui.$("#reconnect").click();
  ui.route(() => undefined);
  await ui.streams.at(-1).onopen();
  snapshot.resolve(response({ ...ready, accepting_input: false, state: "offline", interface: { mode: "remote-agent" } }));
  await pending;
  assert.equal(ui.$("#promptInput").disabled, false);
  assert.equal(ui.window.document.documentElement.dataset.interface, "console");
});

test("explicit reconnect does not render replayed messages twice", async t => {
  const ui = await boot(t);
  const events = [
    { id: 11, event: "user", message: "Saved question" },
    { id: 12, event: "assistant", message: "Unique saved answer" },
  ];
  events.forEach(event => ui.fire(event));
  ui.$("#reconnect").click();
  await ui.streams.at(-1).onopen();
  events.forEach(event => ui.fire(event));
  assert.equal(ui.$("#messages").querySelectorAll(".message").length, 2);
});

test("a current permanent link failure still closes its stream", async t => {
  const ui = await boot(t);
  ui.route(path => path === "/api/state" ? response({ ok: true, alive: false }) : undefined);
  await ui.streams[0].onerror();
  assert.equal(ui.streams[0].closed, true);
  assert.equal(ui.$("#connectionText").textContent, "NO RUNTIME");
  assert.equal(ui.$("#promptInput").disabled, true);
});

test("changing runtime token resets the event replay watermark", async t => {
  const ui = await boot(t);
  ui.fire({ id: 12, event: "assistant", message: "Old runtime answer" });
  ui.window.location.hash = "token=new-runtime";
  ui.$("#reconnect").click();
  await ui.streams.at(-1).onopen();
  ui.fire({ id: 1, event: "assistant", message: "New runtime answer" });
  assert.match(ui.$("#messages").textContent, /New runtime answer/);
});

test("duplicate Enter presses submit only one directive", async t => {
  const ui = await boot(t);
  const pending = deferred();
  ui.route(path => path === "/api/input" ? pending.promise : undefined);
  ui.type("first directive");
  ui.submit();
  ui.submit();
  assert.equal(ui.requests.filter(req => req.path === "/api/input").length, 1);
  pending.resolve(response({ ok: true }));
  await settle();
  assert.equal(ui.$("#promptInput").value, "");
});

test("a late submit response preserves the next prompt's draft and upload", async t => {
  const ui = await boot(t);
  const submitted = deferred();
  const uploaded = deferred();
  ui.route(path => path === "/api/input" ? submitted.promise
    : path === "/api/attachment" ? uploaded.promise : undefined);
  ui.type("first directive");
  ui.submit();
  ui.fire({ event: "input_request", mode: "answer", prompt: "Which image?" });
  ui.type("the next answer");
  ui.window.FileReader = class {
    readAsDataURL() { this.result = "data:image/png;base64,YQ=="; this.onload(); }
  };
  Object.defineProperty(ui.$("#fileInput"), "files", {
    value: [new ui.window.File(["a"], "next.png", { type: "image/png" })],
  });
  ui.$("#fileInput").dispatchEvent(new ui.window.Event("change"));
  submitted.resolve(response({ ok: true }));
  await settle();
  uploaded.resolve(response({ ok: true, path: "C:/fake/next.png" }));
  await settle();
  assert.equal(ui.$("#promptInput").value, "the next answer");
  assert.equal(ui.$("#attachmentChip").hidden, false);
  assert.equal(ui.$("#attachmentName").textContent, "next.png");
});

test("a rejected submission preserves its draft and enables a retry", async t => {
  const ui = await boot(t);
  ui.route(path => path === "/api/input" ? response({ ok: false, error: "busy" }) : undefined);
  ui.type("retry this directive");
  ui.submit();
  await settle();
  assert.equal(ui.$("#promptInput").value, "retry this directive");
  assert.equal(ui.$("#promptInput").disabled, false);
});

test("stop during live configuration fetch prevents a later microphone/session start", async t => {
  const ui = await boot(t);
  const config = deferred();
  ui.route(path => path === "/api/live/config" ? config.promise : undefined);
  const voice = ui.window.liveVoice;
  const pending = voice.start();
  voice.stop();
  config.resolve(response({ ok: true, provider: "realtime", ws_url: "ws://test.invalid/realtime" }));
  await pending;
  assert.equal(voice.active, false);
  assert.equal(ui.microphoneCalls(), 0);
  assert.equal(ui.sockets.length, 0);
});

for (const provider of ["realtime", "gemini"]) {
  test(`stop during ${provider} microphone permission releases late tracks without reconnecting`, async t => {
    const ui = await boot(t);
    const microphone = deferred();
    ui.window.navigator.mediaDevices.getUserMedia = () => microphone.promise;
    ui.route(path => path === "/api/live/config"
      ? response({ ok: true, provider, ws_url: provider === "realtime" ? "ws://test.invalid/realtime" : "" })
      : path === "/api/voice/session" ? response({ ok: true, ws_url: "ws://test.invalid/gemini" }) : undefined);
    const voice = ui.window.liveVoice;
    const pending = voice.start();
    await settle();
    voice.stop();
    microphone.resolve(ui.makeStream());
    await pending;
    assert.equal(voice.active, false);
    assert.equal(ui.tracks[0].stopped, true);
    assert.equal(ui.sockets.length, 0);
    assert.equal(ui.requests.some(req => req.path === "/api/live/state" && req.body?.active), false);
  });
}

test("stop while Gemini session credentials load prevents microphone acquisition", async t => {
  const ui = await boot(t);
  const session = deferred();
  ui.route(path => path === "/api/live/config" ? response({ ok: true, provider: "gemini" })
    : path === "/api/voice/session" ? session.promise : undefined);
  const pending = ui.window.liveVoice.start();
  await settle();
  ui.window.liveVoice.stop();
  session.resolve(response({ ok: true, ws_url: "ws://test.invalid/gemini" }));
  await pending;
  assert.equal(ui.microphoneCalls(), 0);
  assert.equal(ui.window.liveVoice.active, false);
});

test("a Fish session completing after stop is ended, not activated", async t => {
  const ui = await boot(t, { fish: true });
  const session = deferred();
  let ended = false;
  ui.window.__fishSdk = { AgentSession: { start: () => session.promise } };
  ui.route(path => path === "/api/live/config" ? response({ ok: true, provider: "fish", fish_agent_id: "fake-agent" })
    : path === "/api/voice/session" ? response({ ok: true, session: "fake-session" }) : undefined);
  const pending = ui.window.liveVoice.start();
  await settle();
  ui.window.liveVoice.stop();
  session.resolve({ end() { ended = true; }, on() {} });
  await pending;
  assert.equal(ended, true);
  assert.equal(ui.window.liveVoice.active, false);
  assert.equal(ui.window.liveVoice.fishSession, null);
});

test("a cancelled startup cannot release the guard for a newer startup", async t => {
  const ui = await boot(t);
  const oldConfig = deferred();
  const newConfig = deferred();
  const voice = ui.window.liveVoice;
  ui.route(path => path === "/api/live/config" ? oldConfig.promise : undefined);
  const oldStart = voice.start();
  // The toggle must work as STOP even before active becomes true.
  await voice.toggle();
  ui.route(path => path === "/api/live/config" ? newConfig.promise : undefined);
  const newStart = voice.start();
  oldConfig.resolve(response({ ok: true, provider: "realtime", ws_url: "ws://test.invalid/old" }));
  await oldStart;
  assert.equal(voice.starting, true);
  assert.equal(ui.microphoneCalls(), 0);
  newConfig.resolve(response({ ok: true, provider: "realtime", ws_url: "ws://test.invalid/new" }));
  await newStart;
  assert.equal(voice.active, true);
  assert.equal(ui.sockets.length, 1);
  assert.equal(ui.sockets[0].url, "ws://test.invalid/new");
  voice.stop();
});

test("a cancelled microphone rejection cannot stop a newer live session", async t => {
  const ui = await boot(t);
  const microphone = deferred();
  const voice = ui.window.liveVoice;
  ui.window.navigator.mediaDevices.getUserMedia = () => microphone.promise;
  const oldStart = voice.start();
  await settle();
  voice.stop();
  ui.window.navigator.mediaDevices.getUserMedia = async () => ui.makeStream();
  await voice.start();
  microphone.reject(new Error("late permission refusal"));
  await oldStart;
  assert.equal(voice.active, true);
  assert.equal(ui.tracks[0].stopped, false);
  assert.equal(ui.sockets[0].readyState, ui.window.WebSocket.OPEN);
  voice.stop();
});

test("ordinary live startup still works and rejects concurrent starts", async t => {
  const ui = await boot(t);
  const voice = ui.window.liveVoice;
  await Promise.all([voice.start(), voice.start()]);
  assert.equal(voice.active, true);
  assert.equal(ui.microphoneCalls(), 1);
  assert.equal(ui.sockets.length, 1);
  voice.stop();
  assert.equal(ui.tracks[0].stopped, true);
});
