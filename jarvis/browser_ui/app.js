(() => {
  "use strict";

  const root = document.documentElement;
  const $ = (selector) => document.querySelector(selector);
  const elements = {
    activityFollow: $("#activityFollow"),
    activityList: $("#activityList"),
    attachButton: $("#attachButton"),
    attachmentChip: $("#attachmentChip"),
    attachmentName: $("#attachmentName"),
    clearMessages: $("#clearMessages"),
    clock: $("#clock"),
    closeTerminal: $("#closeTerminal"),
    composer: $("#composer"),
    connectionPill: $("#connectionPill"),
    connectionText: $("#connectionText"),
    reconnect: $("#reconnect"),
    activityPanel: $("#activityPanel"),
    panelToggle: $("#panelToggle"),
    panelBackdrop: $("#panelBackdrop"),
    copyTerminal: $("#copyTerminal"),
    endSession: $("#endSession"),
    fileInput: $("#fileInput"),
    messageFollow: $("#messageFollow"),
    messages: $("#messages"),
    prompt: $("#promptInput"),
    removeAttachment: $("#removeAttachment"),
    send: $("#sendButton"),
    sendLabel: $("#sendButtonLabel"),
    sessionsDrawer: $("#sessionsDrawer"),
    sessionsList: $("#sessionsList"),
    sessionsToggle: $("#sessionsToggle"),
    closeSessions: $("#closeSessions"),
    newSessionButton: $("#newSessionButton"),
    sessionTimer: $("#sessionTimer"),
    stateDetail: $("#stateDetail"),
    stateIndex: $("#stateIndex"),
    stateTitle: $("#stateTitle"),
    terminalDrawer: $("#terminalDrawer"),
    terminalFollow: $("#terminalFollow"),
    terminalOutput: $("#terminalOutput"),
    terminalToggle: $("#terminalToggle"),
    audioToggle: $("#audioToggle"),
    audioGlyph: $("#audioGlyph"),
    liveVoiceToggle: $("#liveVoiceToggle"),
    stageVoiceBtn: $("#stageVoiceBtn"),
    stageVoiceText: $("#stageVoiceText"),
    liveVoiceOverlay: $("#liveVoiceOverlay"),
    liveVoiceBadge: $("#liveVoiceBadge"),
    liveVoiceStateText: $("#liveVoiceStateText"),
    voiceWave: $("#voiceWave"),
    voiceCaption: $("#voiceCaption"),
    voiceMuteBtn: $("#voiceMuteBtn"),
    voiceMuteIcon: $("#voiceMuteIcon"),
    voiceMuteLabel: $("#voiceMuteLabel"),
    voiceBargeinBtn: $("#voiceBargeinBtn"),
    voiceExitBtn: $("#voiceExitBtn"),
    toastRegion: $("#toastRegion"),
    welcome: $("#welcomeCard"),
    brandSubtitle: $("#brandSubtitle"),
    topbarPrimary: $("#topbarPrimary"),
    topbarSecondary: $("#topbarSecondary"),
    tabCmdsLabel: $("#tabCmdsLabel"),
    tabBtnSkills: $("#tabBtnSkills"),
    cmdPanelEyebrow: $("#cmdPanelEyebrow"),
    cmdPanelCount: $("#cmdPanelCount"),
    telemetryRuntime: $("#telemetryRuntime"),
    telemetryControl: $("#telemetryControl"),
    telemetryChannel: $("#telemetryChannel"),
    nodeCoordinate: $("#nodeCoordinate"),
    coreCaption: $("#coreCaption"),
    dialogueEyebrow: $("#dialogueEyebrow"),
    dialogueTitle: $("#dialogueTitle"),
    welcomeEyebrow: $("#welcomeEyebrow"),
    welcomeTitle: $("#welcomeTitle"),
    welcomeCopy: $("#welcomeCopy"),
    welcomeStatOne: $("#welcomeStatOne"),
    welcomeStatOneLabel: $("#welcomeStatOneLabel"),
    welcomeStatTwo: $("#welcomeStatTwo"),
    welcomeStatTwoLabel: $("#welcomeStatTwoLabel"),
    welcomeStatThree: $("#welcomeStatThree"),
    welcomeStatThreeLabel: $("#welcomeStatThreeLabel"),
    promptLabel: $("#promptLabel"),
    footerPrimary: $("#footerPrimary"),
    footerSecondary: $("#footerSecondary"),
    // Tabs
    tabUnderline: $(".tab-underline"),
    tabCountStream: $("#tabCountStream"),
    tabCountLogs: $("#tabCountLogs"),
    // Vitals
    vitalUptime: $("#vitalUptime"),
    vitalEvents: $("#vitalEvents"),
    vitalRate: $("#vitalRate"),
    vitalMessages: $("#vitalMessages"),
    vitalPeak: $("#vitalPeak"),
    vitalsSpark: $("#vitalsSpark"),
    vitalKindTotal: $("#vitalKindTotal"),
    vitalStateTotal: $("#vitalStateTotal"),
    kindBars: $("#kindBars"),
    stateBars: $("#stateBars"),
    // Commands
    cmdFilter: $("#cmdFilter"),
    cmdList: $("#cmdList"),
    // Skills
    skillFilter: $("#skillFilter"),
    skillList: $("#skillList"),
    reloadSkills: $("#reloadSkills"),
    tabCountSkills: $("#tabCountSkills"),
    // Shortcuts overlay
    shortcutsBackdrop: $("#shortcutsBackdrop"),
    closeShortcuts: $("#closeShortcuts"),
    // Alerts
    alertList: $("#alertList"),
    clearAlerts: $("#clearAlerts"),
    // Stage HUD
    hudEvents: $("#hudEvents"),
    hudUptime: $("#hudUptime"),
    hudSteps: $("#hudSteps"),
    hudAlerts: $("#hudAlerts"),
    hudAlertChip: $(".hud-chip-alert"),
    stateRibbon: $("#stateRibbon"),
    // Palette
    paletteToggle: $("#paletteToggle"),
    paletteBackdrop: $("#paletteBackdrop"),
    paletteInput: $("#paletteInput"),
    paletteResults: $("#paletteResults"),
  };

  // The real slash commands, mirroring _SLASH_COMMANDS in jarvis/console.py.
  const slashCommands = [
    ["/enhance", "AI-rewrite a rough prompt, confirm, then run it"],
    ["/paste", "attach the clipboard image/screenshot (Ctrl+V works too)"],
    ["/remember", "[fact] - store a fact in permanent memory forever"],
    ["/memory", "list permanent memories and learned plans"],
    ["/help", "show all commands"],
    ["/voice", "voice-ONLY mode: talk instead of typing"],
    ["/wake", 'hands-free mode: say "Hey Jarvis" to command'],
    ["/cron", "list/add/remove scheduled jobs"],
    ["/connect", "Gmail/Discord/WhatsApp connector status and test"],
    ["/remote", "list, remove, trust, or send tasks to paired devices"],
    ["/mcp", "list/add/remove MCP servers (extra tool connectors)"],
    ["/startup", "on|off - launch Jarvis when Windows starts"],
    ["/confirm", "on|off - confirm each action"],
    ["/vision", "on|off - send screenshots to the model"],
    ["/steps", "set max steps per task, e.g. /steps 20"],
    ["/config", "print the active configuration"],
    ["/quit", "exit"],
  ];


  const stateMeta = {
    booting: ["00", "INITIALIZING", "Calibrating neural interface"],
    listening: ["01", "LISTENING", "Awaiting your directive"],
    working: ["02", "PROCESSING", "Routing through terminal core"],
    planning: ["03", "PLANNING", "Plotting an execution path"],
    perceiving: ["04", "PERCEIVING", "Reading the active environment"],
    thinking: ["05", "THINKING", "Synthesizing the next action"],
    acting: ["06", "ACTING", "Executing through local controls"],
    verifying: ["07", "VERIFYING", "Confirming the final state"],
    transcribing: ["08", "TRANSCRIBING", "Resolving captured speech"],
    responding: ["09", "RESPONDING", "Composing a response"],
    success: ["10", "COMPLETE", "Directive completed"],
    warning: ["11", "ATTENTION", "Reviewing an unexpected condition"],
    error: ["12", "FAULT", "The terminal reported an error"],
    offline: ["13", "OFFLINE", "Terminal session ended"],
  };

  const remoteStateMeta = {
    booting: ["00", "SECURING LINK", "Preparing the encrypted relay channel"],
    listening: ["01", "REMOTE AGENT ONLINE", "Awaiting encrypted directives"],
    working: ["02", "REMOTE TASK ACTIVE", "Executing an authorized directive"],
    planning: ["03", "PLANNING", "Plotting the remote execution path"],
    perceiving: ["04", "PERCEIVING", "Reading this device's environment"],
    thinking: ["05", "THINKING", "Resolving the next safe action"],
    acting: ["06", "ACTING", "Operating this device"],
    verifying: ["07", "VERIFYING", "Confirming the remote result"],
    transcribing: ["08", "TRANSCRIBING", "Resolving a local answer"],
    responding: ["09", "REPORTING", "Preparing the encrypted result"],
    success: ["10", "TASK DELIVERED", "Result returned to the controller"],
    warning: ["11", "ATTENTION", "The remote channel needs review"],
    error: ["12", "LINK FAULT", "The remote agent reported an error"],
    offline: ["13", "AGENT OFFLINE", "Remote control is no longer available"],
  };

  const generatingStates = new Set([
    "working",
    "planning",
    "perceiving",
    "thinking",
    "acting",
    "verifying",
    "transcribing",
    "responding",
  ]);

  const sessionStarted = Date.now();
  let acceptingInput = false;
  let inputMode = "command";
  let inputPrompt = "";
  let connected = false;
  //: Short reason shown while the composer is disabled ("NO RUNTIME", ...).
  let linkDiag = "";
  let attachment = null;
  let attachmentUploading = false;
  let uploadVersion = 0;
  let terminalText = "";
  let currentState = "booting";
  let eventSource = null;
  //: One link probe at a time: EventSource fires onerror on every retry.
  let linkProbeInFlight = false;
  //: Last toast text, so a repeated failure does not stack identical alerts.
  let lastLinkToast = "";
  let powerArmed = false;
  let powerTimer = null;
  let eventGeneration = 0;
  let activeUtteranceId = 0;
  let speechIdWatermark = 0;
  let speechExpiryTimer = null;
  let interruptPending = false;
  let interfaceMode = "console";
  let remotePairings = [];
  let remoteUnattended = false;

  // ---- Live metrics, all derived from events the UI already receives ----
  const metrics = {
    events: 0,
    userMessages: 0,
    assistantMessages: 0,
    steps: 0,
    alerts: 0,
    kinds: new Map(),          // activity kind -> count
    stateMs: new Map(),        // state name -> accumulated ms
    ribbon: [],               // recent states, newest last
    buckets: new Array(48).fill(0), // events per 2s slot, rolling
  };
  let lastStateAt = Date.now();
  let activeTab = "stream";
  let unseenStream = 0;
  let vitalsRafId = null; // debounce handle for renderVitals

  const stateColors = {
    listening: "82, 216, 255",
    perceiving: "83, 184, 255",
    thinking: "56, 196, 255",
    acting: "38, 224, 255",
    planning: "92, 170, 255",
    working: "67, 178, 255",
    verifying: "116, 206, 255",
    transcribing: "86, 190, 255",
    responding: "130, 214, 255",
    success: "189, 246, 120",
    warning: "255, 194, 71",
    error: "255, 78, 69",
    offline: "58, 96, 128",
    booting: "100, 125, 139",
  };

  const hashParams = new URLSearchParams(location.hash.replace(/^#/, ""));
  let token = hashParams.get("token") || sessionStorage.getItem("jarvis-browser-token") || "";
  if (token) {
    sessionStorage.setItem("jarvis-browser-token", token);
    if (location.hash) {
      history.replaceState(null, "", location.pathname + location.search);
    }
  }

  function nowTime(date = new Date()) {
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  function shortTime(date = new Date()) {
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }

  function escapePath(path) {
    return `"${String(path).replaceAll('"', '\\"')}"`;
  }

  function escapeHtml(text) {
    return String(text || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }


  function setConnected(value, label, detail) {
    connected = value;
    root.dataset.connected = value ? "true" : "false";
    const status = label || (value ? "LINKED" : "OFFLINE");
    elements.connectionText.textContent = status;
    const explanation = detail || (value
      ? interfaceMode === "remote-agent"
        ? "Connected to the local remote-agent runtime"
        : "Connected to the local terminal runtime"
      : interfaceMode === "remote-agent"
        ? "Remote-agent link unavailable"
        : "Terminal link unavailable");
    elements.connectionPill.setAttribute(
      "aria-label",
      `Connection status: ${status.toLowerCase()} — ${explanation}`,
    );
    elements.connectionPill.title = explanation;
    // The pill is the only always-visible status surface, so the reason a link
    // is down has to be readable on hover rather than buried in the console.
    elements.connectionPill.dataset.detail = detail ? "true" : "false";
    if (elements.reconnect) {
      elements.reconnect.hidden = !(detail && !value);
    }
  }

  /**
   * Work out *why* the link is down, because the answer decides what the user
   * can do about it. A rejected token and a dead terminal runtime are both
   * permanent, and EventSource reconnects on its own forever - which is how
   * this page used to sit at "RELINKING" with a console full of 401s and no
   * explanation on screen.
   */
  async function classifyLinkFailure() {
    try {
      const snapshot = await api("/api/state");
      const worker = snapshot.worker || {};
      if (snapshot.alive === false) {
        const code = worker.exit_code;
        const because = worker.last_error
          ? `${worker.last_error}${code === null || code === undefined ? "" : ` (exit ${code})`}`
          : "the terminal runtime is not running";
        return {
          kind: "runtime",
          label: "NO RUNTIME",
          short: `Terminal runtime ended — ${because}`,
          message: `The terminal runtime is gone: ${because}. Restart Jarvis to get it back.`,
          permanent: true,
        };
      }
      return {
        kind: "transient",
        label: "RELINKING",
        short: "Reconnecting to the terminal link",
        message: "the terminal link dropped; reconnecting",
        permanent: false,
      };
    } catch (error) {
      const text = String((error && error.message) || error);
      if (/unauthor|token|\b401\b/i.test(text)) {
        return {
          kind: "token",
          label: "TOKEN REJECTED",
          short: "Session token refused — reopen the address from the terminal",
          message: "This page's session token was refused: it is stale, or another "
            + "Jarvis is serving this port. Open the URL printed in the terminal again.",
          permanent: true,
        };
      }
      return {
        kind: "server",
        label: "SERVER DOWN",
        short: `Jarvis server unreachable — ${text}`,
        message: `The Jarvis server is not answering (${text}). Start Jarvis again with --browser.`,
        permanent: true,
      };
    }
  }

  function closeEvents() {
    if (!eventSource) return;
    eventSource.onerror = null;
    eventSource.onmessage = null;
    eventSource.onopen = null;
    eventSource.close();
    eventSource = null;
  }

  function applyLinkDiagnosis(diagnosis) {
    linkDiag = diagnosis.permanent ? diagnosis.label : "";
    // Pill label is terse and the stage line is short on purpose: #stateDetail
    // is one ellipsised line, and a diagnosis nobody can finish reading is not
    // a diagnosis. The full sentence goes to the pill's tooltip and the toast.
    setConnected(false, diagnosis.label, diagnosis.message);
    if (diagnosis.permanent) setState("error", diagnosis.short);
    if (diagnosis.permanent && diagnosis.message !== lastLinkToast) {
      lastLinkToast = diagnosis.message;
      toast(diagnosis.message, "error");
    }
    updateComposer();
  }

  /** Reconnect deliberately: a permanent failure never retries on its own. */
  function reconnect() {
    const params = new URLSearchParams(location.hash.replace(/^#/, ""));
    const fromHash = params.get("token");
    if (fromHash) {
      token = fromHash;
      try {
        sessionStorage.setItem("jarvis-browser-token", token);
      } catch {
        /* private mode: the hash is enough */
      }
    }
    linkProbeInFlight = false;
    lastLinkToast = "";
    closeEvents();
    connectEvents();
  }

  function setState(name, detail) {
    if (!(name in stateMeta)) name = "working";
    const previous = currentState;
    currentState = name;
    const meta = interfaceMode === "remote-agent" ? remoteStateMeta : stateMeta;
    const [index, title, fallback] = meta[name];
    root.dataset.state = name;
    elements.stateIndex.textContent = index;
    elements.stateTitle.textContent = title;
    elements.stateDetail.textContent = String(detail || fallback).replace(/\s+/g, " ").trim();
    orb.setState(name);
    // Shift the Grainient background palette to match the current state.
    window.grainientSetState?.(name);

    // Bank the time the previous state was held, then log the transition.
    const now = Date.now();
    if (previous) {
      metrics.stateMs.set(previous, (metrics.stateMs.get(previous) || 0) + (now - lastStateAt));
    }
    lastStateAt = now;
    if (previous !== name) {
      metrics.ribbon.push(name);
      if (metrics.ribbon.length > 40) metrics.ribbon.shift();
      renderRibbon();
    }

    if (name === "offline") {
      acceptingInput = false;
      interruptPending = false;
      clearSpeechOverlay();
      updateComposer();
    }
    updateSendEnabled();
  }

  function updateComposer(promptText) {
    if (promptText !== undefined) inputPrompt = String(promptText || "");
    const remoteReply = interfaceMode !== "remote-agent" || inputMode !== "command";
    const enabled = connected && acceptingInput && remoteReply && currentState !== "offline";
    elements.prompt.disabled = !enabled;
    elements.fileInput.disabled = !enabled;
    elements.attachButton.disabled = !enabled;
    elements.endSession.disabled = !connected || currentState === "offline";

    if (enabled) {
      if (inputMode === "confirmation") {
        elements.prompt.placeholder = inputPrompt || "Approve this action? Type Y or N";
      } else if (inputMode === "answer") {
        elements.prompt.placeholder = inputPrompt || "Answer Jarvis, or send blank to cancel";
      } else {
        elements.prompt.placeholder = inputPrompt || "Issue a directive…";
      }
    } else if (linkDiag) {
      // A disabled composer with no explanation is the worst state this page
      // has: say what is wrong and offer the way out.
      elements.prompt.placeholder = `${linkDiag} — press RECONNECT`;
    } else if (currentState === "offline") {
      elements.prompt.placeholder = interfaceMode === "remote-agent"
        ? "Remote agent session has ended"
        : "Terminal session has ended";
    } else if (interfaceMode === "remote-agent") {
      elements.prompt.placeholder = remotePairings.some((pair) => pair.trusted)
        ? "Waiting for a trusted controller…"
        : "Pair and trust a controller before starting";
    } else {
      elements.prompt.placeholder = "Jarvis is working…";
    }
    updateSendEnabled();
    document.querySelectorAll("[data-suggestion]").forEach((button) => {
      button.disabled = !enabled || inputMode !== "command";
    });
    document.querySelectorAll(".cmd-item").forEach((button) => {
      button.disabled = !enabled || inputMode !== "command";
    });
  }

  function isGeneratingResponse() {
    return (
      connected &&
      !acceptingInput &&
      currentState !== "offline" &&
      generatingStates.has(currentState)
    );
  }

  function updateSendEnabled() {
    const canStop = isGeneratingResponse();
    elements.send.classList.toggle("is-stop", canStop);
    elements.sendLabel.textContent = canStop ? "STOP" : "SEND";
    elements.send.setAttribute(
      "aria-label",
      canStop ? "Stop Jarvis response" : "Send directive to Jarvis",
    );
    elements.send.title = canStop ? "Stop response" : "Send directive";

    if (canStop) {
      elements.send.disabled = interruptPending;
      return;
    }

    const hasText = Boolean(elements.prompt.value.trim());
    const canSendBlank = ["confirmation", "answer"].includes(inputMode);
    elements.send.disabled = !(
      connected &&
      acceptingInput &&
      !attachmentUploading &&
      (hasText || attachment || canSendBlank) &&
      currentState !== "offline"
    );
  }

  function autoSizeComposer() {
    elements.prompt.style.height = "auto";
    elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 150)}px`;
  }

  function toast(message, kind = "") {
    const node = document.createElement("div");
    node.className = `toast ${kind}`.trim();
    node.textContent = message;
    if (kind === "error" || kind === "warning") {
      node.setAttribute("role", "alert");
      node.setAttribute("aria-live", "assertive");
    } else {
      node.setAttribute("role", "status");
    }
    elements.toastRegion.append(node);
    window.setTimeout(() => {
      node.classList.add("leaving");
      window.setTimeout(() => node.remove(), 260);
    }, 3600);
  }

  async function api(path, payload) {
    const options = {
      method: payload === undefined ? "GET" : "POST",
      headers: {
        "X-Jarvis-Token": token,
      },
    };
    if (payload !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    const response = await fetch(path, options);
    let body;
    try {
      body = await response.json();
    } catch {
      body = { ok: false, error: `HTTP ${response.status}` };
    }
    if (!response.ok || body.ok === false) {
      // The server may attach a hint explaining what to do about it (out of
      // credit, beta access, unpublished agent). Carry it into the message so
      // it reaches the caption and the toast, not just the console.
      const reason = body.error || body.message || `HTTP ${response.status}`;
      throw new Error(body.hint ? `${reason} ${body.hint}` : reason);
    }
    return body;
  }

  function applyInterface(next = {}) {
    const mode = next.mode === "remote-agent" ? "remote-agent" : "console";
    interfaceMode = mode;
    root.dataset.interface = mode;
    remotePairings = Array.isArray(next.pairings) ? next.pairings : remotePairings;
    remoteUnattended = Boolean(next.unattended);

    if (mode !== "remote-agent") {
      // A page that has left remote-agent mode gets its local library back.
      elements.tabBtnSkills?.removeAttribute("hidden");
      elements.skillFilter?.removeAttribute("hidden");
      return;
    }

    document.title = "JARVIS // REMOTE AGENT";
    elements.brandSubtitle.textContent = "REMOTE AGENT";
    elements.topbarPrimary.textContent = "ENCRYPTED RELAY";
    elements.topbarSecondary.textContent = "AUTHORIZED CONTROL";
    elements.tabCmdsLabel.textContent = "LINKS";
    elements.cmdPanelEyebrow.textContent = "TRUSTED CONTROLLERS";
    elements.cmdPanelCount.textContent = String(remotePairings.length);
    elements.cmdFilter.placeholder = "Filter controllers…";
    // Remote-agent mode has no local skill library to offer.
    elements.tabBtnSkills?.setAttribute("hidden", "");
    elements.skillFilter?.setAttribute("hidden", "");
    if (activeTab === "skills") selectTab("stream");
    elements.telemetryRuntime.innerHTML = '<i class="status-led"></i> REMOTE';
    elements.telemetryControl.textContent = remoteUnattended ? "UNATTENDED" : "CONFIRM";
    elements.telemetryChannel.textContent = "E2E RELAY";
    if (elements.nodeCoordinate) elements.nodeCoordinate.textContent = "LOCAL AGENT // OUTBOUND ONLY";
    elements.coreCaption.textContent = "REMOTE CORE";
    elements.dialogueEyebrow.textContent = "ENCRYPTED TASK CHANNEL";
    elements.dialogueTitle.textContent = "REMOTE ACTIVITY";
    elements.promptLabel.textContent = "Answer a remote task prompt";
    elements.footerPrimary.textContent = "JARVIS REMOTE // AGENT NODE";
    elements.footerSecondary.textContent = "ENCRYPTED · OPT-IN · LOCAL";
    elements.endSession.setAttribute("aria-label", "Stop remote agent");
    elements.terminalToggle.setAttribute("aria-label", "Open remote agent transcript");

    if (elements.welcome) {
      elements.welcomeEyebrow.textContent = "ENCRYPTED NODE READY";
      elements.welcomeTitle.innerHTML = "This device is ready.<br>Control stays yours.";
      elements.welcomeCopy.textContent = remotePairings.some((pair) => pair.trusted)
        ? "Trusted controllers can send encrypted tasks through the relay. Every action runs here; task contents never belong to the relay."
        : "No trusted controller is available yet. Accept and verify a pairing in the terminal before this device can receive tasks.";
      const trustedCount = remotePairings.filter((pair) => pair.trusted).length;
      elements.welcomeStatOne.textContent = String(trustedCount);
      elements.welcomeStatOneLabel.textContent = "TRUSTED";
      elements.welcomeStatTwo.textContent = "E2E";
      elements.welcomeStatTwoLabel.textContent = "ENCRYPTED";
      elements.welcomeStatThree.textContent = remoteUnattended ? "AUTO" : "ASK";
      elements.welcomeStatThreeLabel.textContent = "APPROVAL";
      elements.welcome.querySelector(".suggestions")?.setAttribute("hidden", "");
      const hint = elements.welcome.querySelector(".welcome-hint");
      if (hint) hint.textContent = "Keep this page open to monitor incoming work.";
    }

    renderCommands(elements.cmdFilter.value);
    setState(currentState);
    updateComposer();
  }

  function handleRemoteEvent(payload) {
    const controller = String(payload.controller || "Trusted controller");
    switch (payload.status) {
      case "ready":
        addActivity("remote", `Encrypted agent online for ${(payload.controllers || []).join(", ") || "trusted controllers"}`, payload.timestamp);
        break;
      case "task_received":
        metrics.userMessages += 1;
        addMessage("user", `${controller}\n${String(payload.task || "Remote directive received")}`, payload.timestamp);
        addActivity("remote", `Directive received from ${controller}`, payload.timestamp);
        break;
      case "task_started":
        addActivity("act", `Executing ${controller}'s directive`, payload.timestamp);
        break;
      case "task_result":
        metrics.assistantMessages += 1;
        addMessage(
          "assistant",
          `${payload.ok ? "Delivered to" : "Task ended for"} ${controller}\n${String(payload.result || "No result was returned.")}`,
          payload.timestamp,
        );
        break;
      case "relay_error":
        addActivity("warning", `Relay unavailable for ${controller}: ${String(payload.message || "connection failed")}`, payload.timestamp);
        break;
      case "task_error":
        addActivity("error", String(payload.message || "Remote task failed"), payload.timestamp);
        break;
      case "replay_ignored":
        addActivity("warning", `Blocked a replayed directive from ${controller}`, payload.timestamp);
        break;
      case "idle":
        addActivity("remote", "Encrypted channel idle and ready", payload.timestamp);
        break;
      case "stopped":
        addActivity("system", "Remote agent stopped", payload.timestamp);
        break;
      default:
        break;
    }
  }

  function hideWelcome() {
    if (elements.welcome) {
      elements.welcome.remove();
      elements.welcome = null;
    }
  }

  function appendInlineMarkdown(parent, source) {
    const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))/g;
    let cursor = 0;
    for (const match of source.matchAll(pattern)) {
      if (match.index > cursor) {
        parent.append(document.createTextNode(source.slice(cursor, match.index)));
      }
      const tokenValue = match[0];
      let node;
      if (tokenValue.startsWith("`")) {
        node = document.createElement("code");
        node.textContent = tokenValue.slice(1, -1);
      } else if (tokenValue.startsWith("**")) {
        node = document.createElement("strong");
        node.textContent = tokenValue.slice(2, -2);
      } else if (tokenValue.startsWith("*")) {
        node = document.createElement("em");
        node.textContent = tokenValue.slice(1, -1);
      } else {
        const linkMatch = tokenValue.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/);
        node = document.createElement("a");
        node.textContent = linkMatch[1];
        node.href = linkMatch[2];
        node.target = "_blank";
        node.rel = "noopener noreferrer";
      }
      parent.append(node);
      cursor = match.index + tokenValue.length;
    }
    if (cursor < source.length) {
      parent.append(document.createTextNode(source.slice(cursor)));
    }
  }

  function renderAssistantMarkdown(container, source) {
    const lines = String(source).split(/\r?\n/);
    let codeLines = null;
    let listNode = null;
    let listKind = "";

    const flushList = () => {
      if (!listNode) return;
      container.append(listNode);
      listNode = null;
      listKind = "";
    };

    const appendBlock = (node) => {
      flushList();
      container.append(node);
    };

    const flushCode = () => {
      if (codeLines === null) return;
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = codeLines.join("\n");
      pre.append(code);
      appendBlock(pre);
      codeLines = null;
    };

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (line.trimStart().startsWith("```")) {
        if (codeLines === null) {
          flushList();
          codeLines = [];
        }
        else flushCode();
        continue;
      }
      if (codeLines !== null) {
        codeLines.push(rawLine);
        continue;
      }
      if (!line.trim()) {
        flushList();
        const spacer = document.createElement("div");
        spacer.className = "markdown-spacer";
        spacer.setAttribute("aria-hidden", "true");
        container.append(spacer);
        continue;
      }

      const heading = line.match(/^\s*(#{1,6})\s+(.+)$/);
      const bullet = line.match(/^\s*[-*+]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      let node;
      if (heading) {
        const level = Math.min(6, heading[1].length + 2);
        node = document.createElement(`h${level}`);
        node.className = "markdown-heading";
        appendInlineMarkdown(node, heading[2]);
      } else if (bullet || ordered) {
        const desiredKind = bullet ? "ul" : "ol";
        if (!listNode || listKind !== desiredKind) {
          flushList();
          listNode = document.createElement(desiredKind);
          listNode.className = "markdown-list";
          listKind = desiredKind;
        }
        node = document.createElement("li");
        appendInlineMarkdown(node, bullet ? bullet[1] : ordered[1]);
        listNode.append(node);
        continue;
      } else if (/^\s*>/.test(line)) {
        node = document.createElement("blockquote");
        appendInlineMarkdown(node, line.replace(/^\s*>\s?/, ""));
      } else if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
        node = document.createElement("hr");
      } else {
        node = document.createElement("p");
        appendInlineMarkdown(node, line.trim());
      }
      appendBlock(node);
    }
    flushCode();
    flushList();
  }

  function isNearBottom(element, threshold = 52) {
    return element.scrollHeight - element.scrollTop - element.clientHeight <= threshold;
  }

  function followLatest(scroller, button) {
    scroller.scrollTop = scroller.scrollHeight;
    button.hidden = true;
  }

  function addMessage(role, message, timestamp) {
    const text = String(message || "").trim();
    if (!text) return;

    // Universal deduplication: prevent identical back-to-back messages
    const lastMsg = elements.messages ? elements.messages.querySelector(".message:last-of-type") : null;
    if (lastMsg && lastMsg.classList.contains(`message-${role}`)) {
      const lastBody = lastMsg.querySelector(".message-body");
      if (lastBody && lastBody.textContent.trim() === text) {
        return;
      }
    }

    hideWelcome();
    const shouldFollow = isNearBottom(elements.messages);

    const article = document.createElement("article");
    article.className = `message message-${role}`;

    const meta = document.createElement("div");
    meta.className = "message-meta";
    const roleNode = document.createElement("span");
    roleNode.className = "message-role";
    roleNode.textContent = role === "assistant" ? "JARVIS" : "YOU";
    const timeNode = document.createElement("time");
    timeNode.textContent = shortTime(timestamp ? new Date(timestamp * 1000) : new Date());
    meta.append(roleNode, timeNode);

    const body = document.createElement("div");
    body.className = "message-body";
    if (role === "assistant") renderAssistantMarkdown(body, text);
    else body.textContent = text;
    article.append(meta, body);
    elements.messages.append(article);

    if (window.liveVoice && window.liveVoice.active && role === "assistant" && elements.voiceCaption) {
      elements.voiceCaption.textContent = text.slice(0, 180);
      elements.voiceCaption.className = "voice-caption is-assistant";
    }

    const rendered = elements.messages.querySelectorAll(".message");
    if (rendered.length > 120) {
      const removeCount = Math.max(1, rendered.length - 100);
      for (let index = 0; index < removeCount; index += 1) {
        rendered[index].remove();
      }
      if (!elements.messages.querySelector(".history-trimmed")) {
        const note = document.createElement("p");
        note.className = "history-trimmed";
        note.textContent = "Older rendered messages trimmed · terminal transcript retained";
        elements.messages.prepend(note);
      }
    }

    if (shouldFollow) followLatest(elements.messages, elements.messageFollow);
    else elements.messageFollow.hidden = false;
  }

  // Activity kinds that represent Jarvis actually doing something. Each one
  // kicks the stage core, so consecutive actions in one state stay visible
  // instead of relying on a state transition to move the blob.
  const ACTION_KINDS = new Set(["act", "ok", "error", "warn", "warning"]);

  function addActivity(kind, message, timestamp) {
    const text = String(message || "").replace(/\s+/g, " ").trim();
    if (!text) return;

    if (ACTION_KINDS.has(kind)) {
      window.energyCore?.triggerPulse(kind === "ok" ? 1.5 : 1.1);
    }

    // Every activity routes through here, so tally metrics at this one point.
    metrics.kinds.set(kind, (metrics.kinds.get(kind) || 0) + 1);
    if (kind === "step") {
      metrics.steps += 1;
      // updateHud() is already called by handleEvent before dispatching here,
      // so a second call is redundant — removed to avoid double DOM write.
    }
    if (kind === "error" || kind === "warn" || kind === "warning") {
      addAlert(kind, text, timestamp);
    }
    if (activeTab !== "stream") {
      unseenStream += 1;
      setTabCount(elements.tabCountStream, unseenStream);
    }

    const shouldFollow = isNearBottom(elements.activityList);
    const empty = elements.activityList.querySelector(".activity-empty");
    if (empty) empty.remove();

    const row = document.createElement("div");
    row.className = "activity-item";
    row.dataset.kind = kind;

    const time = document.createElement("time");
    time.className = "activity-time";
    time.textContent = shortTime(timestamp ? new Date(timestamp * 1000) : new Date());
    const node = document.createElement("span");
    node.className = "activity-node";
    node.setAttribute("aria-hidden", "true");
    const copy = document.createElement("div");
    copy.className = "activity-copy";
    const label = document.createElement("span");
    label.className = "activity-kind";
    label.textContent = kind;
    const detail = document.createElement("span");
    detail.className = "activity-message";
    detail.textContent = text;
    copy.append(label, detail);
    row.append(time, node, copy);
    elements.activityList.append(row);

    while (elements.activityList.children.length > 42) {
      elements.activityList.firstElementChild.remove();
    }
    if (shouldFollow) followLatest(elements.activityList, elements.activityFollow);
    else elements.activityFollow.hidden = false;
  }

  function appendTerminal(text) {
    const drawerOpen = elements.terminalDrawer.classList.contains("open");
    const shouldFollow = !drawerOpen || isNearBottom(elements.terminalOutput);
    terminalText += String(text || "");
    if (terminalText.length > 50000) {
      terminalText = `… earlier output trimmed …\n${terminalText.slice(-45000)}`;
    }
    elements.terminalOutput.textContent = terminalText || "No terminal output yet.";
    if (drawerOpen && shouldFollow) {
      followLatest(elements.terminalOutput, elements.terminalFollow);
    } else if (drawerOpen) {
      elements.terminalFollow.hidden = false;
    }
  }

  // ---- Web Audio & HTML5 Audio System ----
  let audioContext = null;
  let activeAudioSource = null;
  let activeAudioElement = null;
  let activeUtteranceIdPlaying = 0;
  let audioMuted = false;

  function getAudioContext() {
    if (!audioContext) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        audioContext = new AudioContextClass();
      }
    }
    return audioContext;
  }

  function unlockAudioEngine() {
    const ctx = getAudioContext();
    if (ctx && ctx.state === "suspended") {
      ctx.resume().catch(() => {});
    }
  }

  window.addEventListener("pointerdown", unlockAudioEngine, { passive: true });
  window.addEventListener("keydown", unlockAudioEngine, { passive: true });
  window.addEventListener("click", unlockAudioEngine, { passive: true });

  function stopSpeechAudio() {
    activeUtteranceIdPlaying = 0;
    if (activeAudioSource) {
      try {
        activeAudioSource.stop();
        activeAudioSource.disconnect();
      } catch (_) {}
      activeAudioSource = null;
    }
    if (activeAudioElement) {
      try {
        activeAudioElement.pause();
        activeAudioElement.currentTime = 0;
      } catch (_) {}
      activeAudioElement = null;
    }
    updateAudioButtonState(false);
  }

  function b64ToArrayBuffer(b64) {
    const comma = b64.indexOf(",");
    const raw = comma >= 0 ? b64.slice(comma + 1) : b64;
    const binary = atob(raw);
    const len = binary.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
  }

  async function playSpeechAudio(audioSrc, utteranceId) {
    if (audioMuted) return;
    if (window.liveVoice && window.liveVoice.active) {
      // When Live Voice Mode is active, all speaking belongs to gpt-realtime.
      // The Communication Agent is silenced and must not speak.
      return;
    }
    if (activeUtteranceIdPlaying === utteranceId) return;
    stopSpeechAudio();
    activeUtteranceIdPlaying = utteranceId;

    // Strategy 1: Web Audio API via decodeAudioData (zero latency, high quality, active device routing)
    const ctx = getAudioContext();
    if (ctx) {
      try {
        if (ctx.state === "suspended") {
          await ctx.resume();
        }
        let arrayBuffer = null;
        if (audioSrc && audioSrc.startsWith("data:")) {
          arrayBuffer = b64ToArrayBuffer(audioSrc);
        } else {
          const fetchUrl = `/api/speech/audio.wav?id=${utteranceId}&token=${encodeURIComponent(token)}`;
          const resp = await fetch(fetchUrl);
          if (resp.ok) {
            arrayBuffer = await resp.arrayBuffer();
          }
        }
        if (arrayBuffer && activeUtteranceIdPlaying === utteranceId) {
          const decoded = await ctx.decodeAudioData(arrayBuffer.slice(0));
          if (activeUtteranceIdPlaying !== utteranceId) return;
          const source = ctx.createBufferSource();
          source.buffer = decoded;
          const gain = ctx.createGain();
          gain.gain.setValueAtTime(1.0, ctx.currentTime);
          source.connect(gain);
          gain.connect(ctx.destination);
          source.onended = () => {
            if (activeUtteranceIdPlaying === utteranceId) {
              activeAudioSource = null;
              activeUtteranceIdPlaying = 0;
              updateAudioButtonState(false);
            }
          };
          activeAudioSource = source;
          source.start(0);
          updateAudioButtonState(true);
          return;
        }
      } catch (err) {
        console.warn("Web Audio API decode notice:", err);
      }
    }

    // Strategy 2: HTML5 Audio fallback
    if (activeUtteranceIdPlaying !== utteranceId) return;
    try {
      const fallbackSrc = audioSrc || `/api/speech/audio.wav?id=${utteranceId}&token=${encodeURIComponent(token)}`;
      const audio = new Audio(fallbackSrc);
      activeAudioElement = audio;
      audio.onended = () => {
        if (activeUtteranceIdPlaying === utteranceId) {
          activeAudioElement = null;
          activeUtteranceIdPlaying = 0;
          updateAudioButtonState(false);
        }
      };
      const playPromise = audio.play();
      if (playPromise !== undefined) {
        playPromise.then(() => {
          updateAudioButtonState(true);
        }).catch((err) => {
          console.warn("HTML5 Audio playback notice:", err);
          if (err.name === "NotAllowedError") {
            toast("Click anywhere to enable voice output 🔊", "info");
            updateAudioButtonState(false);
          }
        });
      }
    } catch (err) {
      console.warn("HTML5 Audio fallback error:", err);
    }
  }

  function updateAudioButtonState(isPlaying = false) {
    const btn = elements.audioToggle;
    const glyph = elements.audioGlyph;
    if (!btn || !glyph) return;
    if (audioMuted) {
      glyph.textContent = "🔇";
      btn.title = "Audio Muted (Click to unmute and test)";
      btn.style.opacity = "0.5";
    } else {
      glyph.textContent = isPlaying ? "🔊" : "🔉";
      btn.title = isPlaying ? "Audio Playing (Click to test chime)" : "Audio Active (Click to test sound)";
      btn.style.opacity = "1";
    }
  }

  function testAudioChime() {
    unlockAudioEngine();
    const ctx = getAudioContext();
    if (!ctx) {
      toast("AudioContext unavailable in this browser.", "warning");
      return;
    }
    ctx.resume().then(() => {
      audioMuted = false;
      updateAudioButtonState(false);
      const now = ctx.currentTime;
      const osc1 = ctx.createOscillator();
      const osc2 = ctx.createOscillator();
      const gain = ctx.createGain();
      osc1.type = "sine";
      osc1.frequency.setValueAtTime(587.33, now); // D5
      osc2.type = "sine";
      osc2.frequency.setValueAtTime(880.0, now + 0.09); // A5
      gain.gain.setValueAtTime(0.22, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
      osc1.connect(gain);
      osc2.connect(gain);
      gain.connect(ctx.destination);
      osc1.start(now);
      osc1.stop(now + 0.09);
      osc2.start(now + 0.09);
      osc2.stop(now + 0.35);
      toast("Voice audio active 🔊 - Tone played", "info");
    }).catch((e) => {
      toast("Audio unlock required: " + e.message, "warning");
    });
  }

  function clearSpeechOverlay() {
    if (speechExpiryTimer !== null) {
      clearTimeout(speechExpiryTimer);
      speechExpiryTimer = null;
    }
    stopSpeechAudio();
    activeUtteranceId = 0;
    root.dataset.speaking = "false";
    orb.setSpeaking({ active: false });
    if (window.liveVoice && window.liveVoice.active) {
      window.liveVoice.setState("listening");
      if (elements.voiceCaption) {
        elements.voiceCaption.textContent = "Listening for your voice… say \"Hey Jarvis\"";
        elements.voiceCaption.className = "voice-caption";
      }
    }
  }

  function applySpeech(payload) {
    if (window.liveVoice && window.liveVoice.active) {
      // In Live Voice Mode, the Communication Agent is silenced and must not speak.
      // All voice output is handled solely by gpt-realtime.
      return;
    }
    const active = Boolean(payload.active);
    const rawId = Number(payload.utterance_id);
    const utteranceId = Number.isFinite(rawId) && rawId > 0
      ? Math.floor(rawId)
      : active
        ? Math.max(activeUtteranceId, speechIdWatermark) + 1
        : activeUtteranceId || speechIdWatermark;

    if (active) {
      if (utteranceId < speechIdWatermark) return;
      if (
        utteranceId === speechIdWatermark
        && activeUtteranceId !== utteranceId
      ) {
        return;
      }
      const durationMs = Math.max(0, Number(payload.duration_ms) || 0);
      const startedAt = Number(payload.started_at || payload.timestamp) || Date.now() / 1000;
      const elapsedMs = Math.max(0, Date.now() - startedAt * 1000);
      speechIdWatermark = Math.max(speechIdWatermark, utteranceId);
      if (durationMs > 0 && elapsedMs >= durationMs + 120) {
        clearSpeechOverlay();
        return;
      }
      activeUtteranceId = utteranceId;
      root.dataset.speaking = "true";
      if (window.liveVoice && window.liveVoice.active) {
        window.liveVoice.setState("speaking");
      }
      orb.setSpeaking({
        active: true,
        durationMs,
        elapsedMs,
        levels: payload.levels,
        bands: payload.bands,
        bandCount: payload.band_count,
        bandFps: payload.band_fps,
      });

      if (payload.audio) {
        playSpeechAudio(payload.audio, utteranceId);
      }

      if (speechExpiryTimer !== null) clearTimeout(speechExpiryTimer);
      const remainingMs = durationMs > 0
        ? Math.max(0, durationMs - elapsedMs) + 180
        : 30000;
      speechExpiryTimer = setTimeout(() => {
        speechExpiryTimer = null;
        if (activeUtteranceId === utteranceId) clearSpeechOverlay();
      }, Math.max(120, remainingMs));
      return;
    }

    if (utteranceId < speechIdWatermark) return;
    speechIdWatermark = Math.max(speechIdWatermark, utteranceId);
    stopSpeechAudio();
    clearSpeechOverlay();
  }

  // ============================================================
  // Tabs
  // ============================================================

  const tabButtons = Array.from(document.querySelectorAll(".panel-tab"));

  function selectTab(name) {
    const button = tabButtons.find((b) => b.dataset.tab === name);
    if (!button) return;
    activeTab = name;
    tabButtons.forEach((b, i) => {
      const on = b.dataset.tab === name;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
      const panel = document.getElementById(b.getAttribute("aria-controls"));
      if (panel) {
        panel.hidden = !on;
        panel.classList.toggle("is-active", on);
      }
      if (on && elements.tabUnderline) {
        elements.tabUnderline.style.setProperty(
          "transform", `translateX(${i * 100}%)`,
        );
      }
    });
    if (name === "stream") {
      unseenStream = 0;
      setTabCount(elements.tabCountStream, 0);
      followLatest(elements.activityList, elements.activityFollow);
    }
    if (name === "vitals") {
      // Defer one frame so the panel finishes layout before canvas measures itself.
      requestAnimationFrame(() => renderVitals());
    }
    if (name === "skills") fetchSkills();
  }

  function setTabCount(node, value, alert = false) {
    if (!node) return;
    node.textContent = value > 99 ? "99+" : String(value);
    node.dataset.zero = value === 0 ? "true" : "false";
    if (alert) node.dataset.alert = value > 0 ? "true" : "false";
  }

  tabButtons.forEach((button) => {
    button.addEventListener("click", () => selectTab(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      const dir = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!dir) return;
      event.preventDefault();
      const index = tabButtons.indexOf(button);
      const next = tabButtons[(index + dir + tabButtons.length) % tabButtons.length];
      next.focus();
      selectTab(next.dataset.tab);
    });
  });

  // ============================================================
  // Vitals
  // ============================================================

  function formatDuration(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
  }

  function renderBars(container, entries, formatter) {
    if (!container) return;
    if (!entries.length) {
      container.innerHTML = "";
      const empty = document.createElement("p");
      empty.className = "vital-empty";
      empty.textContent = "Nothing recorded yet.";
      container.append(empty);
      return;
    }
    const max = Math.max(...entries.map(([, value]) => value)) || 1;
    // Reuse existing rows so the CSS width transition animates instead of
    // restarting from zero on every repaint.
    const existing = new Map(
      Array.from(container.querySelectorAll(".bar-row")).map((r) => [r.dataset.kind, r]),
    );
    container.querySelectorAll(".vital-empty").forEach((n) => n.remove());

    entries.forEach(([key, value]) => {
      let row = existing.get(key);
      if (!row) {
        row = document.createElement("div");
        row.className = "bar-row";
        row.dataset.kind = key;
        const name = document.createElement("span");
        name.className = "bar-name";
        name.textContent = key.toUpperCase();
        const track = document.createElement("div");
        track.className = "bar-track";
        const fill = document.createElement("i");
        fill.className = "bar-fill";
        track.append(fill);
        const amount = document.createElement("span");
        amount.className = "bar-value";
        row.append(name, track, amount);
      } else {
        existing.delete(key);
      }
      row.querySelector(".bar-fill").style.setProperty(
        "--bar", `${Math.round((value / max) * 100)}%`,
      );
      row.querySelector(".bar-value").textContent = formatter(value);
      container.append(row);
    });
    existing.forEach((row) => row.remove());
  }

  function renderVitals() {
    if (activeTab !== "vitals") return;
    const uptime = Date.now() - sessionStarted;
    elements.vitalUptime.textContent = formatDuration(uptime);
    elements.vitalEvents.textContent = metrics.events.toLocaleString();
    // Use a 5-second minimum to avoid 60× inflated rate in the first second.
    const minutes = Math.max(uptime / 60000, 5 / 60);
    elements.vitalRate.textContent = Math.round(metrics.events / minutes);
    elements.vitalMessages.textContent =
      `${metrics.userMessages}/${metrics.assistantMessages}`;

    const kinds = [...metrics.kinds.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
    elements.vitalKindTotal.textContent =
      [...metrics.kinds.values()].reduce((a, b) => a + b, 0);
    renderBars(elements.kindBars, kinds, (v) => v);

    // Include the in-flight state so the bars keep moving between transitions.
    const live = new Map(metrics.stateMs);
    live.set(currentState, (live.get(currentState) || 0) + (Date.now() - lastStateAt));
    const states = [...live.entries()]
      .filter(([, ms]) => ms > 400)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8);
    const totalMs = [...live.values()].reduce((a, b) => a + b, 0);
    elements.vitalStateTotal.textContent = formatDuration(totalMs);
    renderBars(elements.stateBars, states, (ms) => `${Math.round(ms / 1000)}s`);

    drawSparkline();
  }

  function drawSparkline() {
    const canvas = elements.vitalsSpark;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 2) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);

    const data = metrics.buckets;
    const peak = Math.max(...data, 1);
    elements.vitalPeak.textContent = `peak ${peak}`;
    const accent = getComputedStyle(root).getPropertyValue("--accent-rgb").trim()
      || "67, 198, 255";
    const step = rect.width / (data.length - 1);
    const y = (v) => rect.height - 2 - (v / peak) * (rect.height - 6);

    ctx.beginPath();
    ctx.moveTo(0, rect.height);
    data.forEach((v, i) => ctx.lineTo(i * step, y(v)));
    ctx.lineTo(rect.width, rect.height);
    ctx.closePath();
    const gradient = ctx.createLinearGradient(0, 0, 0, rect.height);
    gradient.addColorStop(0, `rgba(${accent}, 0.36)`);
    gradient.addColorStop(1, `rgba(${accent}, 0)`);
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    data.forEach((v, i) => (i ? ctx.lineTo(i * step, y(v)) : ctx.moveTo(0, y(v))));
    ctx.strokeStyle = `rgba(${accent}, 0.95)`;
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }

  function renderRibbon() {
    const track = elements.stateRibbon;
    if (!track) return;
    track.innerHTML = "";
    metrics.ribbon.slice(-32).forEach((state) => {
      const cell = document.createElement("i");
      cell.className = "ribbon-cell";
      cell.style.setProperty(
        "--cell", `rgba(${stateColors[state] || "67, 198, 255"}, 0.75)`,
      );
      track.append(cell);
    });
  }

  function updateHud() {
    if (elements.hudEvents) elements.hudEvents.textContent = metrics.events.toLocaleString();
    if (elements.hudUptime) elements.hudUptime.textContent = formatDuration(Date.now() - sessionStarted);
    if (elements.hudSteps) elements.hudSteps.textContent = metrics.steps;
    if (elements.hudAlerts) elements.hudAlerts.textContent = metrics.alerts;
    if (elements.hudAlertChip) elements.hudAlertChip.dataset.active = metrics.alerts > 0 ? "true" : "false";
  }

  // ============================================================
  // Alerts (warnings + errors mirrored into the LOGS tab)
  // ============================================================

  function addAlert(kind, message, timestamp) {
    const list = elements.alertList;
    if (!list) return;
    list.querySelectorAll(".vital-empty").forEach((n) => n.remove());

    const item = document.createElement("div");
    item.className = "alert-item";
    item.dataset.kind = kind;
    const head = document.createElement("div");
    head.className = "alert-head";
    const kindNode = document.createElement("span");
    kindNode.className = "alert-kind";
    kindNode.textContent = kind;
    const time = document.createElement("time");
    time.className = "alert-time";
    time.textContent = shortTime(timestamp ? new Date(timestamp * 1000) : new Date());
    head.append(kindNode, time);
    const text = document.createElement("p");
    text.className = "alert-text";
    text.textContent = message;
    item.append(head, text);
    list.append(item);

    while (list.children.length > 60) list.firstElementChild.remove();
    if (activeTab === "logs") list.scrollTop = list.scrollHeight;

    metrics.alerts += 1;
    setTabCount(elements.tabCountLogs, metrics.alerts, true);
    updateHud();
  }

  if (elements.clearAlerts) {
    elements.clearAlerts.addEventListener("click", () => {
      elements.alertList.innerHTML =
        '<p class="vital-empty">No warnings or faults. All clear.</p>';
      metrics.alerts = 0;
      setTabCount(elements.tabCountLogs, 0, true);
      updateHud();
    });
  }

  // ============================================================
  // CMDS tab
  // ============================================================

  function renderCommands(filter = "") {
    const list = elements.cmdList;
    if (!list) return;
    if (interfaceMode === "remote-agent") {
      renderRemoteLinks(filter);
      return;
    }
    const needle = filter.trim().toLowerCase();
    const matches = slashCommands.filter(
      ([name, desc]) =>
        !needle || name.includes(needle) || desc.toLowerCase().includes(needle),
    );
    list.innerHTML = "";
    if (!matches.length) {
      const empty = document.createElement("p");
      empty.className = "vital-empty";
      empty.textContent = `No command matches “${filter}”.`;
      list.append(empty);
      return;
    }
    matches.forEach(([name, desc]) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "cmd-item";
      item.setAttribute("role", "listitem");
      const label = document.createElement("span");
      label.className = "cmd-name";
      label.textContent = name;
      const hint = document.createElement("span");
      hint.className = "cmd-desc";
      hint.textContent = desc;
      item.append(label, hint);
      item.addEventListener("click", () => {
        // Commands taking an argument are staged in the composer, not fired.
        if (/\[|e\.g\.|on\|off/.test(desc)) {
          elements.prompt.value = `${name} `;
          elements.prompt.focus();
          autoSizeComposer();
          updateSendEnabled();
        } else {
          runDirective(name);
        }
      });
      list.append(item);
    });
    document.querySelectorAll(".cmd-item").forEach((b) => {
      b.disabled = !(acceptingInput && connected && inputMode === "command");
    });
  }

  function renderRemoteLinks(filter = "") {
    const list = elements.cmdList;
    const needle = filter.trim().toLowerCase();
    const matches = remotePairings.filter((pair) => {
      const haystack = `${pair.label || ""} ${pair.peer_name || ""} ${pair.relay || ""}`.toLowerCase();
      return !needle || haystack.includes(needle);
    });
    list.innerHTML = "";
    elements.cmdPanelCount.textContent = String(remotePairings.length);
    if (!matches.length) {
      const empty = document.createElement("div");
      empty.className = "remote-links-empty";
      const title = document.createElement("strong");
      title.textContent = remotePairings.length ? "No matching controller" : "No controller paired";
      const copy = document.createElement("span");
      copy.textContent = remotePairings.length
        ? "Try another name or relay."
        : "Use --remote-accept and verify the fingerprint on both devices.";
      empty.append(title, copy);
      list.append(empty);
      return;
    }
    matches.forEach((pair) => {
      const card = document.createElement("article");
      card.className = `remote-link-card ${pair.trusted ? "is-trusted" : "needs-trust"}`;
      card.setAttribute("role", "listitem");

      const header = document.createElement("div");
      const signal = document.createElement("i");
      signal.className = "remote-link-signal";
      const name = document.createElement("strong");
      name.textContent = pair.label || pair.peer_name || "Controller";
      const badge = document.createElement("span");
      badge.textContent = pair.trusted ? "TRUSTED" : "VERIFY";
      header.append(signal, name, badge);

      const peer = document.createElement("p");
      peer.textContent = `Controller · ${pair.peer_name || pair.label || "Unknown"}`;
      const relay = document.createElement("small");
      relay.textContent = `Relay · ${pair.relay || "configured endpoint"}`;
      card.append(header, peer, relay);
      list.append(card);
    });
  }

  if (elements.cmdFilter) {
    elements.cmdFilter.addEventListener("input", () =>
      renderCommands(elements.cmdFilter.value));
  }

  // ============================================================
  // SKILLS tab
  // The library itself lives on disk and is owned by jarvis/skills;
  // this panel reads it and stages a directive, never runs one.
  // ============================================================

  let skillCache = [];
  let skillsFetchedAt = 0;
  const SKILLS_TTL_MS = 15000;

  function skillDirective(skill) {
    return `Use the ${skill.name} skill to `;
  }

  function stageSkill(skill) {
    elements.prompt.value = skillDirective(skill);
    elements.prompt.focus();
    const end = elements.prompt.value.length;
    try {
      elements.prompt.setSelectionRange(end, end);
    } catch { /* not focusable yet; harmless */ }
    autoSizeComposer();
    updateSendEnabled();
    toast(`${skill.name} staged — finish the sentence and send`);
  }

  function renderSkillsError(message) {
    const list = elements.skillList;
    if (!list) return;
    list.innerHTML = "";
    const empty = document.createElement("p");
    empty.className = "vital-empty";
    empty.textContent = `Could not read the skill library: ${message}`;
    list.append(empty);
    setTabCount(elements.tabCountSkills, 0);
  }

  function renderSkills(filter = "") {
    const list = elements.skillList;
    if (!list) return;
    const needle = filter.trim().toLowerCase();
    const matches = skillCache.filter((skill) => {
      if (!needle) return true;
      return [skill.name, skill.description, skill.when_to_use, (skill.tools || []).join(" ")]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
    list.innerHTML = "";
    setTabCount(elements.tabCountSkills, skillCache.length);

    if (!skillCache.length) {
      const empty = document.createElement("p");
      empty.className = "vital-empty";
      empty.textContent =
        "No skills yet. Ask Jarvis to write one — \u201csave what you just did as a skill\u201d.";
      list.append(empty);
      return;
    }
    if (!matches.length) {
      const empty = document.createElement("p");
      empty.className = "vital-empty";
      empty.textContent = `No skill matches \u201c${filter}\u201d.`;
      list.append(empty);
      return;
    }

    matches.forEach((skill) => {
      const item = document.createElement("article");
      item.className = "skill-item";
      item.setAttribute("role", "listitem");

      const head = document.createElement("div");
      head.className = "skill-head";
      const name = document.createElement("strong");
      name.className = "skill-name";
      name.textContent = skill.name;
      head.append(name);
      if (skill.builtin) {
        const badge = document.createElement("span");
        badge.className = "skill-badge";
        badge.textContent = "BUILT-IN";
        head.append(badge);
      }

      const desc = document.createElement("p");
      desc.className = "skill-desc";
      desc.textContent = skill.description || "No description.";

      item.append(head, desc);

      if (skill.when_to_use) {
        const when = document.createElement("p");
        when.className = "skill-when";
        when.textContent = `Use when: ${skill.when_to_use}`;
        item.append(when);
      }

      const foot = document.createElement("div");
      foot.className = "skill-foot";
      const tools = document.createElement("span");
      tools.className = "skill-tools";
      tools.textContent = (skill.tools || []).length
        ? (skill.tools || []).join(" · ")
        : "no tools declared";
      const use = document.createElement("button");
      use.type = "button";
      use.className = "skill-use";
      use.textContent = "USE";
      use.title = `Stage a directive that loads ${skill.name}`;
      use.addEventListener("click", () => stageSkill(skill));
      foot.append(tools, use);
      item.append(foot);
      list.append(item);
    });
  }

  async function fetchSkills({ force = false } = {}) {
    if (!token) return;
    if (!force && skillCache.length && Date.now() - skillsFetchedAt < SKILLS_TTL_MS) {
      renderSkills(elements.skillFilter?.value || "");
      return;
    }
    try {
      const body = await api("/api/skills");
      skillCache = Array.isArray(body.skills) ? body.skills : [];
      skillsFetchedAt = Date.now();
      renderSkills(elements.skillFilter?.value || "");
    } catch (error) {
      // A skills fetch must never take the console down with it: the library is
      // a convenience panel, and the agent can still list skills itself.
      renderSkillsError(error.message || "unavailable");
    }
  }

  if (elements.skillFilter) {
    elements.skillFilter.addEventListener("input", () =>
      renderSkills(elements.skillFilter.value));
  }
  if (elements.reloadSkills) {
    elements.reloadSkills.addEventListener("click", () => fetchSkills({ force: true }));
  }

  // ============================================================
  // Side panel: a column when it fits, a drawer when it does not
  // ============================================================

  function sidePanelOpen() {
    return document.body.classList.contains("panel-open");
  }

  function toggleSidePanel(force) {
    const next = force === undefined ? !sidePanelOpen() : Boolean(force);
    document.body.classList.toggle("panel-open", next);
    if (elements.panelToggle) {
      elements.panelToggle.setAttribute("aria-expanded", next ? "true" : "false");
    }
    if (elements.panelBackdrop) elements.panelBackdrop.hidden = !next;
    if (next) {
      // The drawer covers the stage, so the tab the user came for gets focus.
      const button = tabButtons.find((b) => b.dataset.tab === activeTab);
      if (button) button.focus({ preventScroll: true });
    } else if (elements.panelToggle) {
      elements.panelToggle.focus({ preventScroll: true });
    }
  }

  if (elements.panelToggle) {
    elements.panelToggle.addEventListener("click", () => toggleSidePanel());
  }
  if (elements.panelBackdrop) {
    elements.panelBackdrop.addEventListener("click", () => toggleSidePanel(false));
  }

  // ============================================================
  // Shortcuts overlay
  // ============================================================

  let focusBeforeShortcuts = null;

  function shortcutsOpen() {
    return elements.shortcutsBackdrop ? !elements.shortcutsBackdrop.hidden : false;
  }

  function toggleShortcuts(force) {
    if (!elements.shortcutsBackdrop) return;
    const next = force === undefined ? !shortcutsOpen() : Boolean(force);
    elements.shortcutsBackdrop.hidden = !next;
    if (next) {
      focusBeforeShortcuts = document.activeElement;
      elements.closeShortcuts?.focus({ preventScroll: true });
    } else if (focusBeforeShortcuts && focusBeforeShortcuts.focus) {
      focusBeforeShortcuts.focus({ preventScroll: true });
      focusBeforeShortcuts = null;
    }
  }

  if (elements.closeShortcuts) {
    elements.closeShortcuts.addEventListener("click", () => toggleShortcuts(false));
  }
  if (elements.shortcutsBackdrop) {
    elements.shortcutsBackdrop.addEventListener("click", (event) => {
      if (event.target === elements.shortcutsBackdrop) toggleShortcuts(false);
    });
  }

  // ============================================================
  // Command palette
  // ============================================================

  let paletteItems = [];
  let paletteIndex = 0;

  function paletteOpen() {
    return !elements.paletteBackdrop.hidden;
  }

  function buildPaletteItems(query) {
    const needle = query.trim().toLowerCase();
    const score = (text) => {
      const value = text.toLowerCase();
      if (!needle) return 0;
      const at = value.indexOf(needle);
      return at < 0 ? -1 : at;
    };

    const items = [];
    slashCommands.forEach(([name, desc]) => {
      const best = Math.max(score(name), score(desc));
      if (!needle || score(name) >= 0 || score(desc) >= 0) {
        items.push({
          group: "COMMANDS",
          icon: "/",
          name,
          hint: desc,
          rank: score(name) >= 0 ? score(name) : 100 + best,
          run: () => {
            if (/\[|e\.g\.|on\|off/.test(desc)) {
              elements.prompt.value = `${name} `;
              elements.prompt.focus();
              autoSizeComposer();
              updateSendEnabled();
            } else {
              runDirective(name);
            }
          },
        });
      }
    });

    const actions = [
      ["Keyboard shortcuts", "every key this interface listens for", "⌘",
        () => toggleShortcuts(true)],
      ["Show skills", "the reusable procedures Jarvis can load", "✦",
        () => { toggleSidePanel(true); selectTab("skills"); }],
      ["Show activity panel", "stream, vitals, commands and alerts", "◫",
        () => { toggleSidePanel(true); selectTab("stream"); }],
      ["Toggle terminal transcript", "view the raw backend output", "▤",
        () => toggleTerminal()],
      ["Open sessions", "browse saved conversations", "☰",
        () => toggleSessions(true)],
      ["New session", "start a fresh conversation", "＋",
        () => createNewSession()],
      ["Clear conversation", "empty the on-screen session log", "⌫",
        () => elements.clearMessages.click()],
      ["Show vitals", "session metrics and activity charts", "◈",
        () => selectTab("vitals")],
      ["Show alerts", "warnings and faults this session", "⚠",
        () => selectTab("logs")],
    ];
    actions.forEach(([name, hint, icon, run]) => {
      if (!needle || score(name) >= 0 || score(hint) >= 0) {
        items.push({ group: "ACTIONS", icon, name, hint, rank: score(name), run });
      }
    });

    skillCache.slice(0, 8).forEach((skill) => {
      const best = Math.max(score(skill.name), score(skill.description || ""));
      if (!needle || score(skill.name) >= 0 || score(skill.description || "") >= 0) {
        items.push({
          group: "SKILLS",
          icon: "✦",
          name: skill.name,
          hint: `stage a directive that loads it${skill.builtin ? " · built-in" : ""}`,
          rank: score(skill.name) >= 0 ? score(skill.name) : 100 + best,
          run: () => stageSkill(skill),
        });
      }
    });

    cachedSessions.slice(0, 8).forEach((session) => {
      const title = session.title || "New Session";
      if (!needle || score(title) >= 0) {
        items.push({
          group: "SESSIONS",
          icon: "◷",
          name: title,
          hint: `${session.message_count || 0} messages`,
          rank: score(title),
          run: () => loadSession(session.id),
        });
      }
    });

    return items.sort((a, b) => {
      // Primary: group order keeps COMMANDS before ACTIONS before SKILLS and SESSIONS.
      const groupOrder = { COMMANDS: 0, ACTIONS: 1, SKILLS: 2, SESSIONS: 3 };
      const gd = (groupOrder[a.group] ?? 3) - (groupOrder[b.group] ?? 3);
      if (gd !== 0) return gd;
      // Secondary: substring position (closer to start = better match).
      const ra = a.rank < 0 ? 999 : a.rank;
      const rb = b.rank < 0 ? 999 : b.rank;
      return ra - rb;
    });
  }

  function renderPalette() {
    const container = elements.paletteResults;
    container.innerHTML = "";
    if (!paletteItems.length) {
      const empty = document.createElement("p");
      empty.className = "palette-empty";
      empty.textContent = "No matches.";
      container.append(empty);
      return;
    }
    let group = "";
    paletteItems.forEach((item, index) => {
      if (item.group !== group) {
        group = item.group;
        const heading = document.createElement("p");
        heading.className = "palette-group";
        heading.textContent = group;
        container.append(heading);
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "palette-item";
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", index === paletteIndex ? "true" : "false");
      const icon = document.createElement("span");
      icon.className = "palette-icon";
      icon.textContent = item.icon;
      const text = document.createElement("span");
      text.className = "palette-text";
      const name = document.createElement("span");
      name.className = "palette-name";
      name.textContent = item.name;
      const hint = document.createElement("span");
      hint.className = "palette-hint";
      hint.textContent = item.hint;
      text.append(name, hint);
      button.append(icon, text);
      button.addEventListener("click", () => {
        togglePalette(false);
        item.run();
      });
      button.addEventListener("mousemove", () => {
        if (paletteIndex === index) return;
        paletteIndex = index;
        container.querySelectorAll(".palette-item").forEach((node, i) =>
          node.setAttribute("aria-selected", i === index ? "true" : "false"));
      });
      container.append(button);
    });
  }

  function movePalette(delta) {
    if (!paletteItems.length) return;
    paletteIndex = (paletteIndex + delta + paletteItems.length) % paletteItems.length;
    const nodes = elements.paletteResults.querySelectorAll(".palette-item");
    nodes.forEach((node, i) =>
      node.setAttribute("aria-selected", i === paletteIndex ? "true" : "false"));
    nodes[paletteIndex]?.scrollIntoView({ block: "nearest" });
  }

  function togglePalette(force) {
    const open = force === undefined ? !paletteOpen() : Boolean(force);
    elements.paletteBackdrop.hidden = !open;
    if (open) {
      elements.paletteInput.value = "";
      paletteIndex = 0;
      paletteItems = buildPaletteItems("");
      renderPalette();
      elements.paletteInput.focus();
      fetchSessions();
    } else if (!elements.prompt.disabled) {
      elements.prompt.focus({ preventScroll: true });
    }
  }

  elements.paletteToggle.addEventListener("click", () => togglePalette());
  if (elements.reconnect) {
    elements.reconnect.addEventListener("click", () => reconnect());
  }
  elements.paletteBackdrop.addEventListener("mousedown", (event) => {
    if (event.target === elements.paletteBackdrop) togglePalette(false);
  });
  elements.paletteInput.addEventListener("input", () => {
    paletteIndex = 0;
    paletteItems = buildPaletteItems(elements.paletteInput.value);
    renderPalette();
  });
  elements.paletteInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      movePalette(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      movePalette(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const item = paletteItems[paletteIndex];
      if (item) {
        togglePalette(false);
        item.run();
      }
    } else if (event.key === "Escape") {
      event.preventDefault();
      togglePalette(false);
    }
  });

  /** Send a directive the user picked from a list rather than typed. */
  function runDirective(text) {
    if (!acceptingInput || !connected) {
      toast("Jarvis is busy — try again in a moment.");
      return;
    }
    elements.prompt.value = text;
    autoSizeComposer();
    submitDirective();
  }

  function handleEvent(payload) {
    metrics.events += 1;
    metrics.buckets[metrics.buckets.length - 1] += 1;
    updateHud();
    switch (payload.event) {      case "session":
        if (payload.interface_mode) {
          applyInterface({ mode: payload.interface_mode });
        }
        linkDiag = "";
        lastLinkToast = "";
        setConnected(Boolean(payload.alive), payload.alive ? "LINKED" : "OFFLINE");
        // Skills can change between sessions (the agent writes them), so a new
        // link re-reads the library rather than trusting an old copy.
        if (payload.alive) fetchSkills({ force: true });
        if (!payload.alive) {
          clearSpeechOverlay();
          // An exit code is the difference between "it ended" and "it crashed
          // with 1" - worth showing instead of a bare "OFFLINE".
          const code = payload.exit_code;
          const why = payload.message
            || `Terminal runtime ended${code === null || code === undefined ? "" : ` (exit code ${code})`}`;
          setState("offline", why);
        }
        updateComposer();
        break;
      case "state":
        setState(payload.state, payload.label);
        break;
      case "input_request":
        acceptingInput = true;
        interruptPending = false;
        inputMode = payload.mode || "command";
        inputPrompt = String(payload.prompt || "");
        setState("listening", payload.prompt || (
          inputMode === "confirmation"
            ? "Awaiting confirmation"
            : inputMode === "answer"
              ? "Awaiting your answer"
              : "Awaiting directive"
        ));
        updateComposer(inputPrompt);
        if (inputMode !== "command") {
          elements.prompt.focus({ preventScroll: true });
        }
        break;
      case "input":
      case "user":
        acceptingInput = false;
        metrics.userMessages += 1;
        addMessage("user", payload.message, payload.timestamp);
        updateComposer();
        break;
      case "assistant":
        metrics.assistantMessages += 1;
        addMessage("assistant", payload.message, payload.timestamp);
        break;
      case "activity":
        addActivity(payload.kind || "activity", payload.message, payload.timestamp);
        break;
      case "system":
        addActivity("system", payload.message, payload.timestamp);
        break;
      case "terminal":
        appendTerminal(payload.text);
        break;
      case "speech":
        applySpeech(payload);
        break;
      case "remote":
        handleRemoteEvent(payload);
        break;
      case "session_list":
        renderSessionsList(payload.sessions || [], payload.active_id);
        break;
      case "session_title_updated":
        updateSessionTitleInUI(payload.id, payload.title);
        toast(`Session title: ${payload.title}`);
        break;
      case "proactive_alert":
        addActivity("proactive", `[${payload.title || payload.rule_name || 'Alert'}] ${payload.message}`, payload.timestamp);
        toast(`⏰ ${payload.title || 'Proactive Alert'}: ${payload.message}`, "info");
        break;
      default:
        break;
    }
    // Debounce vitals repaint: schedule at most one rAF per event burst so
    // terminal floods (100+ events/sec) don't cause per-event canvas redraws.
    if (activeTab === "vitals" && vitalsRafId === null) {
      vitalsRafId = requestAnimationFrame(() => {
        vitalsRafId = null;
        renderVitals();
      });
    }
  }


  function connectEvents() {
    if (!token) {
      setConnected(false, "NO TOKEN");
      setState("error", "Launch this page with jarvis --browser");
      toast("This interface needs a Jarvis session token.", "error");
      return;
    }
    eventSource = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
    eventSource.onopen = async () => {
      setConnected(true, "LINKED");
      const generationAtRequest = eventGeneration;
      try {
        const snapshot = await api("/api/state");
        applyInterface(snapshot.interface || { mode: interfaceMode });
        fetchSkills({ force: true });
        if (generationAtRequest === eventGeneration) {
          acceptingInput = Boolean(snapshot.accepting_input);
          inputMode = snapshot.input_mode || "command";
          inputPrompt = String(snapshot.input_prompt || "");
          if (snapshot.state) setState(snapshot.state);
          applySpeech(snapshot.speech || { active: false });
          updateComposer(inputPrompt);
        }
      } catch {
        updateComposer();
      }
    };
    eventSource.onmessage = (event) => {
      try {
        eventGeneration += 1;
        handleEvent(JSON.parse(event.data));
      } catch (error) {
        console.warn("Ignored malformed Jarvis event", error);
      }
    };
    eventSource.onerror = async () => {
      setConnected(false, "RELINKING");
      updateComposer();
      if (linkProbeInFlight) return;
      linkProbeInFlight = true;
      try {
        const diagnosis = await classifyLinkFailure();
        // A permanent cause never recovers on its own, and EventSource would
        // otherwise retry it forever behind an unchanged "RELINKING".
        if (diagnosis.permanent) closeEvents();
        applyLinkDiagnosis(diagnosis);
      } finally {
        linkProbeInFlight = false;
      }
    };
  }

  async function submitDirective() {
    if (!acceptingInput || !connected) return;
    if (attachmentUploading) {
      toast("Wait for the image to finish attaching.");
      return;
    }
    const instruction = elements.prompt.value.trim();
    let text = instruction;
    let displayText = instruction;
    if (attachment) {
      const effectiveInstruction = instruction || "Analyze this image.";
      text = `${escapePath(attachment.path)} ${effectiveInstruction}`;
      displayText = `${effectiveInstruction}\n[Attached image: ${attachment.name}]`;
    }
    if (!text.trim() && !["confirmation", "answer"].includes(inputMode)) return;

    const generationAtSubmit = eventGeneration;
    acceptingInput = false;
    updateComposer();

    const voice = window.liveVoice;
    if (
      voice && voice.active && !attachment &&
      !["confirmation", "answer"].includes(inputMode)
    ) {
      // While live voice mode is active the conversation belongs to the voice
      // agent (it falls back to the main agent when no transport is open).
      // Confirmations and attachments still go to the main agent above.
      elements.prompt.value = "";
      autoSizeComposer();
      await voice.submitVoiceTurn(text);
      updateComposer();
      return;
    }

    try {
      await api("/api/input", { text, display_text: displayText });
      elements.prompt.value = "";
      autoSizeComposer();
      clearAttachment();
      if (generationAtSubmit === eventGeneration) acceptingInput = false;
      updateComposer();
    } catch (error) {
      toast(error.message || "Could not submit directive", "error");
      if (generationAtSubmit === eventGeneration && currentState !== "offline") {
        acceptingInput = true;
      }
      updateComposer();
    }
  }

  async function stopResponse() {
    if (!isGeneratingResponse() || interruptPending) return;

    interruptPending = true;
    updateSendEnabled();
    try {
      await api("/api/interrupt", {});
    } catch (error) {
      interruptPending = false;
      updateSendEnabled();
      toast(error.message || "Could not stop Jarvis", "error");
    }
  }

  function clearAttachment() {
    uploadVersion += 1;
    attachment = null;
    attachmentUploading = false;
    elements.fileInput.value = "";
    elements.attachmentChip.hidden = true;
    elements.attachmentName.textContent = "";
    updateSendEnabled();
  }

  async function uploadAttachment(file) {
    if (!file || !file.type.startsWith("image/")) {
      toast("Jarvis browser mode accepts image attachments only.", "error");
      return;
    }
    if (file.size > 12 * 1024 * 1024) {
      toast("Choose an image smaller than 12 MB.", "error");
      return;
    }
    const version = ++uploadVersion;
    attachment = null;
    attachmentUploading = true;
    elements.attachmentName.textContent = `Uploading ${file.name}…`;
    elements.attachmentChip.hidden = false;
    updateSendEnabled();

    const reader = new FileReader();
    reader.onerror = () => {
      if (version !== uploadVersion) return;
      clearAttachment();
      toast("Could not read that image.", "error");
    };
    reader.onload = async () => {
      if (version !== uploadVersion) return;
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      if (comma < 0) {
        clearAttachment();
        toast("Could not read that image.", "error");
        return;
      }
      try {
        const saved = await api("/api/attachment", {
          name: file.name,
          type: file.type,
          data: result.slice(comma + 1),
        });
        if (version !== uploadVersion) return;
        attachment = { path: saved.path, name: file.name };
        attachmentUploading = false;
        elements.attachmentName.textContent = file.name;
        updateSendEnabled();
      } catch (error) {
        if (version !== uploadVersion) return;
        clearAttachment();
        toast(error.message || "Attachment failed", "error");
      }
    };
    reader.readAsDataURL(file);
  }

  function toggleTerminal(force) {
    const open = force === undefined
      ? !elements.terminalDrawer.classList.contains("open")
      : Boolean(force);
    const shouldReturnFocus =
      !open && elements.terminalDrawer.contains(document.activeElement);
    elements.terminalDrawer.classList.toggle("open", open);
    elements.terminalDrawer.inert = !open;
    elements.terminalDrawer.setAttribute("aria-hidden", open ? "false" : "true");
    elements.terminalToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      followLatest(elements.terminalOutput, elements.terminalFollow);
      elements.closeTerminal.focus({ preventScroll: true });
    } else if (shouldReturnFocus) {
      elements.terminalToggle.focus({ preventScroll: true });
    }
  }

  elements.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    if (isGeneratingResponse()) {
      stopResponse();
    } else {
      submitDirective();
    }
  });

  elements.prompt.addEventListener("input", () => {
    autoSizeComposer();
    updateSendEnabled();
  });

  elements.prompt.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submitDirective();
    }
  });

  elements.fileInput.addEventListener("change", () => {
    uploadAttachment(elements.fileInput.files[0]);
  });

  elements.attachButton.addEventListener("click", () => {
    if (!elements.attachButton.disabled) elements.fileInput.click();
  });

  elements.removeAttachment.addEventListener("click", clearAttachment);

  document.querySelectorAll("[data-suggestion]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!acceptingInput) return;
      elements.prompt.value = button.dataset.suggestion || "";
      autoSizeComposer();
      updateSendEnabled();
      elements.prompt.focus();
    });
  });

  elements.clearMessages.addEventListener("click", () => {
    elements.messages
      .querySelectorAll(".message, .history-trimmed")
      .forEach((message) => message.remove());
    elements.messageFollow.hidden = true;
    // Reset the unseen-activity badge so it doesn't show stale count
    // for messages that no longer exist in the stream.
    unseenStream = 0;
    setTabCount(elements.tabCountStream, 0);
    toast("Visible conversation cleared. Jarvis memory is unchanged.");
  });

  elements.messageFollow.addEventListener(
    "click",
    () => followLatest(elements.messages, elements.messageFollow),
  );
  elements.activityFollow.addEventListener(
    "click",
    () => followLatest(elements.activityList, elements.activityFollow),
  );
  elements.terminalFollow.addEventListener(
    "click",
    () => followLatest(elements.terminalOutput, elements.terminalFollow),
  );

  for (const [scroller, button] of [
    [elements.messages, elements.messageFollow],
    [elements.activityList, elements.activityFollow],
    [elements.terminalOutput, elements.terminalFollow],
  ]) {
    scroller.addEventListener("scroll", () => {
      if (isNearBottom(scroller)) button.hidden = true;
    }, { passive: true });
  }

  elements.terminalToggle.addEventListener("click", () => toggleTerminal());
  elements.audioToggle?.addEventListener("click", () => testAudioChime());
  elements.closeTerminal.addEventListener("click", () => toggleTerminal(false));

  elements.copyTerminal.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(terminalText);
      toast("Terminal transcript copied.");
    } catch {
      toast("Clipboard access was unavailable.", "error");
    }
  });

  let cachedSessions = [];
  let currentActiveSessionId = null;

  function toggleSessions(force) {
    const open = force === undefined
      ? !elements.sessionsDrawer.classList.contains("open")
      : Boolean(force);
    const shouldReturnFocus =
      !open && elements.sessionsDrawer.contains(document.activeElement);
    elements.sessionsDrawer.classList.toggle("open", open);
    elements.sessionsDrawer.inert = !open;
    elements.sessionsDrawer.setAttribute("aria-hidden", open ? "false" : "true");
    elements.sessionsToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      fetchSessions();
      elements.closeSessions.focus({ preventScroll: true });
    } else if (shouldReturnFocus) {
      elements.sessionsToggle.focus({ preventScroll: true });
    }
  }

  async function fetchSessions() {
    try {
      const res = await api("/api/sessions");
      if (res.ok) {
        cachedSessions = res.sessions || [];
        currentActiveSessionId = res.active_id;
        renderSessionsList(cachedSessions, currentActiveSessionId);
      }
    } catch (err) {
      console.warn("Failed to fetch sessions", err);
    }
  }

  function renderSessionsList(sessions, activeId) {
    cachedSessions = sessions || [];
    if (activeId) currentActiveSessionId = activeId;
    if (!elements.sessionsList) return;
    if (!cachedSessions || cachedSessions.length === 0) {
      elements.sessionsList.innerHTML = '<div class="sessions-empty">No saved sessions yet</div>';
      return;
    }
    elements.sessionsList.innerHTML = "";
    cachedSessions.forEach((s) => {
      const card = document.createElement("div");
      card.className = "session-card" + (s.id === currentActiveSessionId ? " active" : "");
      card.dataset.id = s.id;

      const dateStr = s.updated_at ? new Date(s.updated_at * 1000).toLocaleString([], {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
      }) : "";

      card.innerHTML = `
        <div class="session-card-header">
          <span class="session-title">${escapeHtml(s.title || "New Session")}</span>
          ${s.id === currentActiveSessionId ? '<span class="session-badge">ACTIVE</span>' : ''}
        </div>
        <div class="session-meta">
          <span>${s.message_count || 0} msgs · ${dateStr}</span>
          <button type="button" class="session-delete-btn" title="Delete Session">×</button>
        </div>
      `;

      card.addEventListener("click", (e) => {
        if (e.target.classList.contains("session-delete-btn")) {
          e.stopPropagation();
          deleteSession(s.id);
          return;
        }
        if (s.id !== currentActiveSessionId) {
          loadSession(s.id);
        }
      });

      elements.sessionsList.appendChild(card);
    });
  }

  function updateSessionTitleInUI(id, title) {
    const card = elements.sessionsList.querySelector(`.session-card[data-id="${CSS.escape(id)}"]`);
    if (card) {
      const titleElem = card.querySelector(".session-title");
      if (titleElem) titleElem.textContent = title;
    }
  }

  /** Reset all session-scoped metrics. Called on session load/new. */
  function resetMetrics() {
    metrics.events = 0;
    metrics.userMessages = 0;
    metrics.assistantMessages = 0;
    metrics.steps = 0;
    metrics.alerts = 0;
    metrics.kinds.clear();
    metrics.stateMs.clear();
    metrics.ribbon.length = 0;
    metrics.buckets.fill(0);
    unseenStream = 0;
    lastStateAt = Date.now();
    setTabCount(elements.tabCountStream, 0);
    setTabCount(elements.tabCountLogs, 0, true);
    renderRibbon();
    updateHud();
  }

  async function loadSession(id) {
    try {
      const res = await api(`/api/sessions/load?id=${encodeURIComponent(id)}`);
      if (res.ok && res.session) {
        currentActiveSessionId = res.active_id;
        resetMetrics();
        elements.messages.querySelectorAll(".message, .welcome-card").forEach(m => m.remove());
        const msgs = res.session.messages || [];
        if (msgs.length === 0) {
          elements.messages.appendChild(elements.welcome);
        } else {
          msgs.forEach((m) => {
            const role = m.role === "user" ? "user" : "assistant";
            addMessage(role, m.display_text || m.content, m.timestamp);
          });
        }
        renderSessionsList(cachedSessions, currentActiveSessionId);
        toast(`Loaded session: ${res.session.title}`);
        toggleSessions(false);
      }
    } catch (err) {
      toast("Failed to load session", "error");
    }
  }

  async function createNewSession() {
    try {
      const res = await api("/api/sessions/new", {});
      if (res.ok) {
        currentActiveSessionId = res.active_id;
        resetMetrics();
        elements.messages.querySelectorAll(".message").forEach(m => m.remove());
        elements.messages.appendChild(elements.welcome);
        await fetchSessions();
        toast("Created new session");
        toggleSessions(false);
      }
    } catch (err) {
      toast("Failed to create new session", "error");
    }
  }

  async function deleteSession(id) {
    try {
      const res = await api("/api/sessions/delete", { id });
      if (res.ok) {
        toast("Session deleted");
        await fetchSessions();
      }
    } catch (err) {
      toast("Failed to delete session", "error");
    }
  }

  elements.sessionsToggle.addEventListener("click", () => toggleSessions());
  elements.closeSessions.addEventListener("click", () => toggleSessions(false));
  elements.newSessionButton.addEventListener("click", () => createNewSession());

  elements.endSession.addEventListener("click", async () => {
    if (!powerArmed) {
      powerArmed = true;
      toast("Press the power control again to end this session.", "warning");
      clearTimeout(powerTimer);
      powerTimer = setTimeout(() => { powerArmed = false; }, 3500);
      return;
    }
    powerArmed = false;
    elements.endSession.disabled = true;
    try {
      await api("/api/shutdown", {});
      toast("Ending local Jarvis session…");
    } catch (error) {
      toast(error.message || "Could not end the session yet", "error");
      updateComposer();
    }
  });

  //
  // Publish the header's real height as `--header-h`.
  //
  // The stylesheet sets it per breakpoint, but the header is not a fixed height:
  // below 720px the action row wraps to a second line when the reconnect
  // control appears, and no px value survives every font metric and zoom level.
  // Every drawer anchors off this variable, so measuring it here is what keeps
  // them below the header instead of across it. Without this the stylesheet's
  // breakpoint values still apply and the layout is merely approximate, never
  // broken: measurement only ever refines it.
  function syncHeaderHeight() {
    const header = document.querySelector(".topbar");
    if (!header) return;
    const publish = () => {
      const height = Math.round(header.getBoundingClientRect().height);
      if (height > 0) {
        document.documentElement.style.setProperty("--header-h", `${height}px`);
      }
    };
    publish();
    if (typeof ResizeObserver === "function") {
      new ResizeObserver(publish).observe(header);
    }
    window.addEventListener("resize", publish);
    window.addEventListener("orientationchange", publish);
  }

  syncHeaderHeight();

  // Keys typed into a field belong to that field, never to a global shortcut.
  function isTypingTarget(node) {
    if (!node || !node.tagName) return false;
    if (node.isContentEditable) return true;
    return ["INPUT", "TEXTAREA", "SELECT"].includes(node.tagName);
  }

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      togglePalette();
    } else if (event.key === "Escape" && paletteOpen()) {
      togglePalette(false);
    } else if (event.key === "Escape" && shortcutsOpen()) {
      toggleShortcuts(false);
    } else if (event.key === "Escape" && elements.terminalDrawer.classList.contains("open")) {
      toggleTerminal(false);
    } else if (event.key === "Escape" && elements.sessionsDrawer.classList.contains("open")) {
      toggleSessions(false);
    } else if (event.key === "Escape" && sidePanelOpen()) {
      toggleSidePanel(false);
    } else if (event.key === "?" && !event.ctrlKey && !event.metaKey && !isTypingTarget(event.target)) {
      event.preventDefault();
      toggleShortcuts();
    } else if (event.key === "/" && !event.ctrlKey && !event.metaKey && !isTypingTarget(event.target)) {
      event.preventDefault();
      elements.prompt.focus({ preventScroll: true });
    }
  });

  // Paste a screenshot straight into the composer. A text paste keeps its
  // default behaviour; only an image is intercepted.
  document.addEventListener("paste", (event) => {
    if (!acceptingInput || attachmentUploading) return;
    const items = Array.from(event.clipboardData?.items || []);
    const image = items.find((item) => String(item.type || "").startsWith("image/"));
    const file = image?.getAsFile();
    if (!file) return;
    event.preventDefault();
    uploadAttachment(file);
  });


  const dialoguePanel = $(".dialogue-panel");
  dialoguePanel.addEventListener("dragover", (event) => {
    if (!acceptingInput) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });
  dialoguePanel.addEventListener("drop", (event) => {
    if (!acceptingInput) return;
    event.preventDefault();
    uploadAttachment(event.dataTransfer.files[0]);
  });

  window.addEventListener("beforeunload", () => {
    if (eventSource) eventSource.close();
  });

  window.setInterval(() => {
    elements.clock.textContent = nowTime();
    const seconds = Math.floor((Date.now() - sessionStarted) / 1000);
    const minutes = Math.floor(seconds / 60);
    elements.sessionTimer.textContent =
      `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    updateHud();
    renderVitals();
  }, 1000);
  elements.clock.textContent = nowTime();

  // Roll the sparkline window every 2s so it reads as events-over-time.
  window.setInterval(() => {
    metrics.buckets.push(0);
    metrics.buckets.shift();
  }, 2000);

  class EnergyCore {
    constructor(canvas) {
      this.canvas = canvas;
      this.holo3d = null;
      if (window.HolographicCore3D && window.THREE) {
        try {
          this.holo3d = new window.HolographicCore3D(canvas.parentElement, canvas);
        } catch (err) {
          console.warn("Could not start 3D Hologram, falling back to 2D canvas:", err);
          this.ctx = canvas.getContext("2d", { alpha: true });
        }
      } else {
        this.ctx = canvas.getContext("2d", { alpha: true });
      }

      this.dpr = 1;
      this.width = 0;
      this.height = 0;
      this.cx = 0;
      this.cy = 0;
      this.radius = 100;
      this.state = "booting";
      this.motionQuery = matchMedia("(prefers-reduced-motion: reduce)");
      this.reducedMotion = this.motionQuery.matches;
      this.hidden = document.hidden;
      this.rafId = null;
      this.lastFrame = 0;
      this.startTime = performance.now();
      this.pointer = { x: 0, y: 0, tx: 0, ty: 0 };
      this.speaking = false;
      this.speechDuration = 0;
      this.speechStartedAt = 0;
      this.speechEnvelope = [];
      this.speechMix = 0;
      this.speechLevel = 0;
      // Level reported by the browser's own voice session (Fish Agents or the
      // realtime socket), independent of the server's speech envelopes.
      this.voiceLevel = 0;
      this.bands = null;
      this.bandCount = 0;
      this.bandFps = 30;
      this.bandFrames = 0;
      this.bars = new Float32Array(0);
      this.barTargets = new Float32Array(0);
      this.bass = 0;
      this.bassAverage = 0;
      this.lastShock = -1;
      this.shocks = [];
      this.current = {
        speed: 0.2,
        deform: 0.25,
        energy: 0.28,
        color: [67, 178, 255],
        pulse: 0.3,
      };
      this.target = { ...this.current, color: [...this.current.color] };
      this.nodes = [];
      this.links = [];
      this.sparks = [];
      this.profiles = {
        // Colors mirror the Grainient background palette (c1 highlight channel)
        // so the blob and background always share the same hue family.
        booting:       { speed: 0.18, deform: 0.18, energy: 0.42, pulse: 0.38, color: [28,  255, 255] },
        listening:     { speed: 0.25, deform: 0.3,  energy: 0.94, pulse: 1.15, color: [0,   255, 221] },
        perceiving:    { speed: 0.42, deform: 0.23, energy: 0.72, pulse: 0.48, color: [187,  68, 255] },
        thinking:      { speed: 1.05, deform: 0.82, energy: 1.0,  pulse: 1.05, color: [124,  58, 255] },
        planning:      { speed: 0.72, deform: 0.42, energy: 0.74, pulse: 0.82, color: [102,  34, 255] },
        verifying:     { speed: 0.52, deform: 0.16, energy: 0.82, pulse: 0.56, color: [85,   68, 255] },
        transcribing:  { speed: 0.63, deform: 0.68, energy: 0.78, pulse: 1.3,  color: [68,  170, 255] },
        working:       { speed: 0.54, deform: 0.5,  energy: 0.79, pulse: 0.72, color: [0,   136, 255] },
        acting:        { speed: 1.45, deform: 0.58, energy: 1.0,  pulse: 1.4,  color: [0,   153, 255] },
        responding:    { speed: 0.38, deform: 0.37, energy: 0.78, pulse: 0.96, color: [85,  204, 255] },
        success:       { speed: 0.22, deform: 0.16, energy: 1.0,  pulse: 1.55, color: [0,   255, 136] },
        warning:       { speed: 0.58, deform: 0.7,  energy: 0.84, pulse: 0.92, color: [255, 170,   0] },
        error:         { speed: 0.3,  deform: 0.88, energy: 0.68, pulse: 0.6,  color: [255,  51,   0] },
        offline:       { speed: 0.04, deform: 0.08, energy: 0.08, pulse: 0.1,  color: [255,  34,   0] },
      };
      // Liquid energy core (blob.js). It renders inside this core's frame loop
      // and is the default stage visual; the 3D hologram stays one click away.
      this.blob = null;
      this.displayMode = "hologram";
      const blobHost = document.getElementById("fluidBlob");
      if (blobHost && window.FluidBlob) {
        this.blob = new window.FluidBlob(blobHost, { reducedMotion: this.reducedMotion });
        if (this.blob.available) this.displayMode = "blob";
        else this.blob = null;
      }

      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(canvas.parentElement);
      this.seedGeometry();
      this.bind();
      this.schedule();
    }

    /** Switch the stage between the liquid blob and the 3D hologram modes. */
    setDisplayMode(mode) {
      const next = mode || "blob";
      this.displayMode = next;
      const blobMode = next === "blob" && Boolean(this.blob);
      if (blobMode) {
        this.canvas.parentElement?.setAttribute("data-core-mode", "blob");
        this.holo3d?.setSuspended(true);
        this.blob.setActive(true);
      } else {
        this.canvas.parentElement?.setAttribute("data-core-mode", "hologram");
        this.blob?.setActive(false);
        this.holo3d?.setSuspended(false);
        this.holo3d?.setDisplayMode(next);
      }
      // The 2D canvas is display:none in blob mode, so its measured size has to
      // be refreshed explicitly when the hologram takes the stage back.
      this.resize();
    }

    resetView() {
      this.holo3d?.resetView();
      this.triggerPulse(1.2);
    }

    triggerPulse(power = 1.5) {
      this.holo3d?.triggerPulse(power);
      this.blob?.pulse(power);
    }

    /**
     * Voice level from the browser's live-voice analyser. Fed to both the blob
     * and the hologram, which otherwise only reacts to server speech envelopes.
     */
    setVoiceLevel(level, speaking) {
      const value = Math.max(0, Math.min(1, Number(level) || 0));
      this.voiceLevel = value;
      if (this.holo3d) {
        if (value > 0.02 && speaking) {
          this.holo3d.speechMix = Math.min(1.0, value * 2.5);
          this.holo3d.speechLevel = value;
        } else if (value > 0.08) {
          this.holo3d.speechMix = Math.min(1.0, value * 2.0);
          this.holo3d.speechLevel = value;
        }
      }
      if (this.reducedMotion) this.schedule();
    }

    bind() {
      window.addEventListener("pointermove", (event) => {
        if (this.reducedMotion) return;
        this.pointer.tx = (event.clientX / window.innerWidth - 0.5) * 10;
        this.pointer.ty = (event.clientY / window.innerHeight - 0.5) * 7;
      }, { passive: true });
      document.addEventListener("visibilitychange", () => {
        this.hidden = document.hidden;
        if (this.hidden && this.rafId !== null) {
          cancelAnimationFrame(this.rafId);
          this.rafId = null;
        } else if (!this.hidden) {
          this.schedule();
        }
      });
      this.motionQuery.addEventListener("change", (event) => {
        this.reducedMotion = event.matches;
        if (this.rafId !== null) {
          cancelAnimationFrame(this.rafId);
          this.rafId = null;
        }
        if (this.reducedMotion) {
          this.current = { ...this.target, color: [...this.target.color] };
          this.pointer = { x: 0, y: 0, tx: 0, ty: 0 };
        }
        this.lastFrame = 0;
        this.schedule();
      });
    }

    resize() {
      // Measure the frame rather than the canvas: the canvas is hidden while
      // the blob owns the stage, which would report a 0x0 box.
      const frame = this.canvas.parentElement;
      const measured = (frame || this.canvas).getBoundingClientRect();
      const own = this.canvas.getBoundingClientRect();
      const rect = {
        width: measured.width || own.width,
        height: measured.height || own.height,
      };
      this.dpr = Math.min(window.devicePixelRatio || 1, 1.55);
      this.width = Math.max(1, rect.width);
      this.height = Math.max(1, rect.height);
      this.canvas.width = Math.round(this.width * this.dpr);
      this.canvas.height = Math.round(this.height * this.dpr);
      if (this.ctx) {
        this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      }
      if (this.holo3d && typeof this.holo3d.resize === "function") {
        this.holo3d.resize(this.width, this.height);
      }
      if (this.blob) this.blob.resize();
      this.cx = this.width / 2;
      this.cy = this.height / 2 - Math.min(12, this.height * 0.025);
      this.radius = Math.max(92, Math.min(this.width, this.height) * 0.46);
      this.seedGeometry();
      if (this.reducedMotion) this.schedule();
    }

    seedGeometry() {
      const count = Math.max(130, Math.min(320, Math.floor((this.width || 600) * 0.43)));
      this.nodes = [];
      const golden = Math.PI * (3 - Math.sqrt(5));
      for (let i = 0; i < count; i += 1) {
        const y = 1 - (i / Math.max(1, count - 1)) * 2;
        const ring = Math.sqrt(Math.max(0, 1 - y * y));
        const theta = golden * i;
        this.nodes.push({
          x: Math.cos(theta) * ring,
          y,
          z: Math.sin(theta) * ring,
          phase: this.hash(i * 9.13) * Math.PI * 2,
          size: 0.35 + this.hash(i * 3.7) * 1.35,
          drift: 0.6 + this.hash(i * 12.7) * 1.6,
        });
      }
      this.links = [];
      for (let i = 0; i < Math.min(150, count); i += 1) {
        this.links.push([i, (i * 37 + 23) % count]);
      }
      this.sparks = Array.from({ length: 26 }, (_, index) => ({
        angle: this.hash(index * 3.19) * Math.PI * 2,
        orbit: 0.72 + this.hash(index * 8.71) * 0.72,
        speed: (0.18 + this.hash(index * 1.91) * 0.55) * (index % 2 ? 1 : -1),
        size: 0.5 + this.hash(index * 4.31) * 1.2,
        phase: this.hash(index * 10.3) * Math.PI * 2,
      }));
    }

    hash(value) {
      const x = Math.sin(value * 12.9898 + 78.233) * 43758.5453;
      return x - Math.floor(x);
    }

    setState(name) {
      // The blob understands states the 2D profiles do not (speaking, muted,
      // connecting), so it receives the raw name and falls back on its own.
      const requested = name;
      const changed = requested !== this.state;
      if (!this.profiles[name]) name = "working";
      this.state = name;
      if (this.holo3d) {
        this.holo3d.setState(name);
      }
      if (this.blob) {
        this.blob.setState(requested);
        if (changed) this.pulseForState(requested);
      }
      const profile = this.profiles[name];
      this.target = {
        speed: profile.speed,
        deform: profile.deform,
        energy: profile.energy,
        pulse: profile.pulse,
        color: [...profile.color],
      };
      if (this.reducedMotion) {
        this.current = { ...this.target, color: [...this.target.color] };
        this.schedule();
      }
    }

    /** Kick the blob when Jarvis changes gear, hardest when it acts or fails. */
    pulseForState(name) {
      if (!this.blob) return;
      if (name === "acting" || name === "success") this.blob.pulse(1.5);
      else if (name === "error" || name === "warning") this.blob.pulse(1.2);
      else this.blob.pulse(0.5);
    }

    setSpeaking(payload) {
      this.speaking = Boolean(payload?.active);
      if (this.holo3d) {
        this.holo3d.setSpeaking(payload);
      }
      if (this.speaking) {
        this.speechDuration = Math.max(
          0.2,
          (Number(payload.durationMs) || 0) / 1000,
        );
        const elapsed = Math.max(0, (Number(payload.elapsedMs) || 0) / 1000);
        this.speechStartedAt =
          (performance.now() - this.startTime) / 1000 - elapsed;
        this.speechEnvelope = Array.isArray(payload.levels)
          ? payload.levels
            .slice(0, 96)
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value))
            .map((value) => Math.max(0, Math.min(1, value / 255)))
          : [];
        this.bandCount = Math.max(0, Math.min(64, Number(payload.bandCount) || 0));
        this.bandFps = Math.max(1, Math.min(120, Number(payload.bandFps) || 30));
        this.bands = this.decodeBands(payload.bands);
      } else {
        this.bands = null;
        this.bandFrames = 0;
      }
      if (this.reducedMotion) {
        this.speechMix = this.speaking ? 1 : 0;
        this.speechLevel = this.speaking ? 0.62 : 0;
        this.schedule();
      }
    }

    speechSample(t, offset = 0) {
      if (!this.speechEnvelope.length) {
        return 0.58 + Math.sin(t * 8.4 + offset * 0.47) * 0.2;
      }
      const progress = this.reducedMotion
        ? 0.28
        : Math.max(
          0,
          Math.min(1, (t - this.speechStartedAt) / this.speechDuration),
        );
      const cursor = progress * (this.speechEnvelope.length - 1);
      const position = Math.max(
        0,
        Math.min(this.speechEnvelope.length - 1, cursor + offset),
      );
      const lower = Math.floor(position);
      const upper = Math.min(this.speechEnvelope.length - 1, lower + 1);
      const blend = position - lower;
      return this.mix(
        this.speechEnvelope[lower],
        this.speechEnvelope[upper],
        blend,
      );
    }

    mix(current, target, amount) {
      return current + (target - current) * amount;
    }

    decodeBands(encoded) {
      this.bandFrames = 0;
      if (typeof encoded !== "string" || !encoded || !this.bandCount) return null;
      let binary;
      try {
        binary = atob(encoded);
      } catch {
        return null;
      }
      const frames = Math.floor(binary.length / this.bandCount);
      if (frames < 1) return null;
      const bytes = new Uint8Array(frames * this.bandCount);
      for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      this.bandFrames = frames;
      return bytes;
    }

    /** Fill barTargets with this instant's per-band levels (0..1). */
    sampleBands(t) {
      const count = this.bars.length;
      if (!count) return;
      if (this.bands && this.bandFrames > 0) {
        const cursor = Math.max(0, Math.min(
          this.bandFrames - 1,
          (t - this.speechStartedAt) * this.bandFps,
        ));
        const lower = Math.floor(cursor);
        const upper = Math.min(this.bandFrames - 1, lower + 1);
        const blend = cursor - lower;
        const lowRow = lower * this.bandCount;
        const highRow = upper * this.bandCount;
        for (let index = 0; index < count; index += 1) {
          this.barTargets[index] = this.mix(
            this.bands[lowRow + index],
            this.bands[highRow + index],
            blend,
          ) / 255;
        }
        return;
      }
      // No spectrogram (numpy missing, or an older backend): shape the flat
      // amplitude envelope into a plausible voice curve so the ring still
      // reads as speech instead of a rigid pulsing circle.
      const level = this.speechSample(t);
      for (let index = 0; index < count; index += 1) {
        const tilt = 1 - (index / count) * 0.5;
        const shimmer = 0.62 + Math.abs(Math.sin(index * 0.83 - t * 7.3)) * 0.38;
        this.barTargets[index] = Math.max(0, Math.min(1, level * tilt * shimmer));
      }
    }

    frame(time) {
      this.rafId = null;
      if (this.hidden) return;
      if (this.reducedMotion || time - this.lastFrame >= 16) {
        const dt = Math.min(40, time - (this.lastFrame || time)) / 16.67;
        this.lastFrame = time;
        this.update(dt);
        this.draw(time);
      }
      if (!this.reducedMotion) this.schedule();
    }

    schedule() {
      if (!this.hidden && this.rafId === null) {
        this.rafId = requestAnimationFrame((time) => this.frame(time));
      }
    }

    update(dt) {
      const amount = Math.min(1, 0.045 * dt);
      for (const key of ["speed", "deform", "energy", "pulse"]) {
        this.current[key] = this.mix(this.current[key], this.target[key], amount);
      }
      for (let index = 0; index < 3; index += 1) {
        this.current.color[index] = this.mix(
          this.current.color[index],
          this.target.color[index],
          amount,
        );
      }
      const t = (performance.now() - this.startTime) / 1000;
      const rawSpeechLevel = this.speaking ? this.speechSample(t) : 0;
      const mixAmount = (this.speaking ? 0.17 : 0.075) * dt;
      const levelAmount = (rawSpeechLevel > this.speechLevel ? 0.2 : 0.09) * dt;
      this.speechMix = this.mix(
        this.speechMix,
        this.speaking ? 1 : 0,
        Math.min(1, mixAmount),
      );
      this.speechLevel = this.mix(
        this.speechLevel,
        rawSpeechLevel,
        Math.min(1, levelAmount),
      );
      this.updateBars(t, dt);
      this.pointer.x = this.mix(this.pointer.x, this.pointer.tx, 0.03 * dt);
      this.pointer.y = this.mix(this.pointer.y, this.pointer.ty, 0.03 * dt);
      // Let the browser voice level fall away on its own, so one loud frame
      // does not leave the core lit up.
      this.voiceLevel = this.mix(this.voiceLevel, 0, Math.min(1, 0.045 * dt));

      if (this.blob && this.blob.active) {
        const voice = this.voiceLevel;
        this.blob.setSignals({
          time: t,
          energy: this.current.energy,
          deform: this.current.deform,
          speed: this.current.speed,
          pulse: this.current.pulse,
          // Whichever source is louder: the server's speech envelope or the
          // browser's live-voice analyser.
          speech: Math.max(this.speechLevel, voice),
          speechMix: Math.max(this.speechMix, voice > 0.03 ? Math.min(1, voice * 2.4) : 0),
          bass: this.bass,
          bars: this.bars,
          color: this.current.color,
        });
      }
    }

    updateBars(t, dt) {
      const count = this.bandCount || 24;
      if (this.bars.length !== count) {
        this.bars = new Float32Array(count);
        this.barTargets = new Float32Array(count);
      }
      if (this.speaking || this.speechMix > 0.01) this.sampleBands(t);
      else this.barTargets.fill(0);

      // Fast attack, slow release: transients survive, so a beat reads as a
      // snap outward followed by a decay instead of an averaged-out wobble.
      const attack = Math.min(1, 0.44 * dt);
      const release = Math.min(1, 0.1 * dt);
      const bassBins = Math.min(4, count);
      let bass = 0;
      for (let index = 0; index < count; index += 1) {
        const target = this.barTargets[index];
        this.bars[index] = this.reducedMotion
          ? target
          : this.mix(
            this.bars[index],
            target,
            target > this.bars[index] ? attack : release,
          );
        if (index < bassBins) bass += this.bars[index];
      }
      this.bass = bassBins ? bass / bassBins : 0;

      this.bassAverage = this.mix(this.bassAverage, this.bass, Math.min(1, 0.05 * dt));
      const isKick = this.bass > 0.24
        && this.bass > this.bassAverage * 1.4
        && this.speechMix > 0.25;
      if (isKick && t - this.lastShock > 0.14) {
        this.lastShock = t;
        this.shocks.push({ born: t, power: Math.min(1, this.bass) });
      }
      if (this.shocks.length) {
        this.shocks = this.shocks.filter((shock) => t - shock.born < 1.5);
      }
    }

    color(alpha, multiplier = 1) {
      const [r, g, b] = this.current.color.map((value) =>
        Math.max(0, Math.min(255, Math.round(value * multiplier))));
      return `rgba(${r},${g},${b},${alpha})`;
    }

    /** Current state colour blended toward white; `whiteness` 0..1 = hotter. */
    tint(alpha, whiteness = 0) {
      const [r, g, b] = this.current.color.map((value) =>
        Math.max(0, Math.min(255, Math.round(this.mix(value, 255, whiteness)))));
      return `rgba(${r},${g},${b},${alpha})`;
    }

    draw(time) {
      const t = (time - this.startTime) / 1000;
      if (this.displayMode === "blob" && this.blob && this.blob.active) {
        this.blob.render(t);
        return;
      }
      if (this.holo3d && this.holo3d.isWebGLAvailable) return;
      const ctx = this.ctx;
      if (!ctx) return;
      const { energy, speed, deform, pulse } = this.current;
      ctx.clearRect(0, 0, this.width, this.height);
      ctx.save();
      ctx.translate(this.pointer.x, this.pointer.y);

      this.drawOuterHud(ctx, t, energy, speed);
      this.drawPulseRings(ctx, t, energy, pulse);
      this.drawShockwaves(ctx, t);
      // The particle shell breathes with the voice so the whole blob talks,
      // not just the ring around it.
      const projected = this.projectNodes(
        t,
        deform * (1 + this.speechMix * this.speechLevel * 0.85),
        speed,
      );
      this.drawLinks(ctx, projected, energy);
      this.drawShell(ctx, projected, energy, t);
      this.drawFilaments(ctx, t, energy, deform, speed);
      this.drawOrganicLoops(ctx, t, energy, deform, speed);
      this.drawVoiceSpectrum(ctx, t, energy);
      this.drawCore(ctx, t, energy, pulse, this.speechLevel);
      this.drawSparks(ctx, t, energy, speed);
      this.drawStateEffects(ctx, t, energy);

      ctx.restore();
    }

    drawOuterHud(ctx, t, energy, speed) {
      ctx.save();
      ctx.translate(this.cx, this.cy);
      ctx.globalCompositeOperation = "lighter";
      const rings = [
        { radius: 1.23, width: 0.55, alpha: 0.13, dash: [2, 9], spin: 0.12 },
        { radius: 1.08, width: 1, alpha: 0.21, dash: [17, 7, 3, 12], spin: -0.17 },
        { radius: 0.91, width: 0.55, alpha: 0.17, dash: [1, 6], spin: 0.24 },
      ];
      rings.forEach((ring, index) => {
        ctx.save();
        ctx.rotate(t * ring.spin * speed + index * 0.7);
        ctx.beginPath();
        ctx.setLineDash(ring.dash);
        ctx.lineDashOffset = t * (index % 2 ? 13 : -10) * speed;
        ctx.strokeStyle = this.color(ring.alpha * (0.45 + energy * 0.75));
        ctx.lineWidth = ring.width;
        ctx.ellipse(
          0,
          0,
          this.radius * ring.radius,
          this.radius * ring.radius * 0.96,
          0,
          0,
          Math.PI * 2,
        );
        ctx.stroke();
        ctx.restore();
      });

      const tickRadius = this.radius * 1.29;
      ctx.strokeStyle = this.color(0.14 + energy * 0.12);
      for (let index = 0; index < 72; index += 1) {
        if (index % 4 === 1 || index % 7 === 2) continue;
        const angle = (index / 72) * Math.PI * 2 + t * 0.02;
        const length = index % 9 === 0 ? 8 : index % 3 === 0 ? 4 : 2;
        ctx.lineWidth = index % 9 === 0 ? 1 : 0.5;
        ctx.beginPath();
        ctx.moveTo(Math.cos(angle) * tickRadius, Math.sin(angle) * tickRadius);
        ctx.lineTo(
          Math.cos(angle) * (tickRadius + length),
          Math.sin(angle) * (tickRadius + length),
        );
        ctx.stroke();
      }
      ctx.restore();
    }

    drawShockwaves(ctx, t) {
      if (!this.shocks.length) return;
      ctx.save();
      ctx.translate(this.cx, this.cy);
      ctx.globalCompositeOperation = "lighter";
      for (const shock of this.shocks) {
        const progress = (t - shock.born) / 1.5;
        if (progress < 0 || progress > 1) continue;
        const eased = 1 - (1 - progress) ** 3;
        const fade = (1 - progress) ** 2;
        ctx.beginPath();
        ctx.strokeStyle = this.color(fade * 0.42 * shock.power, 1.2);
        ctx.lineWidth = 0.5 + fade * 2.4;
        ctx.arc(0, 0, this.radius * (0.5 + eased * 0.95), 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }

    drawPulseRings(ctx, t, energy, pulse) {
      if (
        this.speechMix < 0.02
        && !["listening", "success", "transcribing", "acting"].includes(this.state)
      ) return;
      ctx.save();
      ctx.translate(this.cx, this.cy);
      ctx.globalCompositeOperation = "lighter";
      for (let index = 0; index < 3; index += 1) {
        const progress = (t * (0.24 + pulse * 0.09) + index / 3) % 1;
        const radius = this.radius * (0.52 + progress * 1.02);
        const alpha = (1 - progress) * (
          0.12 + energy * 0.13 + this.speechMix * 0.08
        );
        ctx.beginPath();
        ctx.strokeStyle = this.color(alpha);
        ctx.lineWidth = 0.5 + (1 - progress);
        ctx.arc(0, 0, radius, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }

    projectNodes(t, deform, speed) {
      const rotation = t * 0.18 * speed;
      const tilt = Math.sin(t * 0.17) * 0.18;
      const cosR = Math.cos(rotation);
      const sinR = Math.sin(rotation);
      const cosT = Math.cos(tilt);
      const sinT = Math.sin(tilt);
      return this.nodes.map((node, index) => {
        let x = node.x * cosR - node.z * sinR;
        let z = node.x * sinR + node.z * cosR;
        let y = node.y * cosT - z * sinT;
        z = node.y * sinT + z * cosT;
        const noise =
          Math.sin(node.phase + t * node.drift * (0.42 + speed * 0.25)) * 0.55 +
          Math.sin(node.phase * 1.9 - t * 0.73) * 0.28 +
          Math.sin(index * 0.31 + t * 1.17) * 0.17;
        const radial = this.radius * (0.72 + noise * deform * 0.14);
        const perspective = 1 + z * 0.18;
        return {
          x: this.cx + x * radial * perspective,
          y: this.cy + y * radial * perspective * 0.96,
          z,
          alpha: 0.12 + ((z + 1) / 2) * 0.62,
          size: node.size * (0.64 + ((z + 1) / 2) * 0.8),
          noise,
        };
      });
    }

    drawLinks(ctx, projected, energy) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      ctx.lineWidth = 0.45;
      for (let index = 0; index < this.links.length; index += 1) {
        const [aIndex, bIndex] = this.links[index];
        const a = projected[aIndex];
        const b = projected[bIndex];
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        if (distance > this.radius * 0.92) continue;
        const alpha = (1 - distance / (this.radius * 0.92)) * 0.23 * energy *
          Math.max(0.1, (a.alpha + b.alpha) / 2);
        ctx.strokeStyle = this.color(alpha);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      ctx.restore();
    }

    drawShell(ctx, projected, energy, t) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      projected
        .slice()
        .sort((a, b) => a.z - b.z)
        .forEach((point, index) => {
          const flicker = 0.72 + Math.sin(t * 2.4 + index * 1.73) * 0.28;
          const alpha = point.alpha * energy * flicker;
          if (this.state === "acting") {
            ctx.strokeStyle = this.color(alpha * 0.2);
            ctx.lineWidth = 0.5;
            ctx.beginPath();
            ctx.moveTo(point.x, point.y);
            ctx.lineTo(
              point.x - (point.x - this.cx) * 0.055,
              point.y - (point.y - this.cy) * 0.055,
            );
            ctx.stroke();
          }
          ctx.fillStyle = this.color(Math.min(0.95, alpha * 0.92), 1.1);
          ctx.beginPath();
          ctx.arc(point.x, point.y, Math.max(0.3, point.size), 0, Math.PI * 2);
          ctx.fill();
        });
      ctx.restore();
    }

    drawFilaments(ctx, t, energy, deform, speed) {
      ctx.save();
      ctx.translate(this.cx, this.cy);
      ctx.globalCompositeOperation = "lighter";
      const count = this.width < 500 ? 22 : 38;
      for (let index = 0; index < count; index += 1) {
        const seed = this.hash(index * 7.41);
        const direction = index % 2 ? 1 : -1;
        const angle = seed * Math.PI * 2 + t * 0.035 * speed * direction;
        const startRadius = this.radius * (0.27 + this.hash(index * 2.73) * 0.3);
        const endRadius = this.radius * (0.72 + this.hash(index * 4.91) * 0.32);
        const bend = (this.hash(index * 8.37) - 0.5) * (0.45 + deform * 0.36);
        const flicker = 0.45 + Math.sin(t * (1.1 + seed) + index) * 0.45;
        const alpha = Math.max(0.025, flicker * energy * (0.075 + seed * 0.12));
        ctx.beginPath();
        ctx.moveTo(
          Math.cos(angle) * startRadius,
          Math.sin(angle) * startRadius * 0.9,
        );
        ctx.quadraticCurveTo(
          Math.cos(angle + bend) * this.radius * 0.72,
          Math.sin(angle + bend) * this.radius * 0.63,
          Math.cos(angle + bend * 0.4) * endRadius,
          Math.sin(angle + bend * 0.4) * endRadius * 0.9,
        );
        ctx.strokeStyle = this.color(alpha, 1.14);
        ctx.lineWidth = seed > 0.78 ? 1.1 : 0.45;
        if (index % 4 === 0) {
          ctx.setLineDash([2 + seed * 9, 4 + seed * 7]);
          ctx.lineDashOffset = -t * (8 + seed * 14) * speed;
        } else {
          ctx.setLineDash([]);
        }
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.restore();
    }

    drawOrganicLoops(ctx, t, energy, deform, speed) {
      ctx.save();
      ctx.translate(this.cx, this.cy);
      ctx.globalCompositeOperation = "lighter";
      const loopCount = this.width < 500 ? 5 : 8;
      for (let loop = 0; loop < loopCount; loop += 1) {
        const base = this.radius * (0.43 + loop * 0.044);
        const rotation = t * (0.09 + loop * 0.008) * speed * (loop % 2 ? -1 : 1);
        ctx.save();
        ctx.rotate(rotation + loop * 0.77);
        ctx.scale(1, 0.78 + (loop % 3) * 0.08);
        ctx.beginPath();
        const segments = 96;
        for (let index = 0; index <= segments; index += 1) {
          const angle = (index / segments) * Math.PI * 2;
          const turbulence =
            Math.sin(angle * (3 + loop % 4) + t * (0.7 + loop * 0.08)) * deform * 8 +
            Math.sin(angle * 11 - t * 1.3 + loop) * deform * 2.6;
          const radius = base + turbulence;
          const x = Math.cos(angle) * radius;
          const y = Math.sin(angle) * radius;
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.strokeStyle = this.color((0.065 + loop * 0.016) * energy);
        ctx.lineWidth = loop % 3 === 0 ? 1.25 : 0.58;
        if (loop % 3 === 1) ctx.setLineDash([8, 4, 1, 7]);
        ctx.stroke();
        ctx.restore();
      }
      ctx.restore();
    }

    drawVoiceSpectrum(ctx, t, energy) {
      const voice = this.speechMix;
      const bands = this.bars.length;
      if (voice < 0.008 || !bands) return;

      const compact = Math.min(this.width, this.height) < 520;
      const slots = bands * 2;
      const baseRadius = this.radius * (compact ? 0.56 : 0.6);
      const span = this.radius * (compact ? 0.3 : 0.42);
      const surface = (band, angle) =>
        baseRadius * (0.84 + this.bars[band] * 0.26 * voice)
        + Math.sin(angle * 3 + t * 1.7) * this.radius * 0.014;

      ctx.save();
      ctx.translate(this.cx, this.cy);
      ctx.globalCompositeOperation = "lighter";
      ctx.lineCap = "round";

      ctx.beginPath();
      for (let slot = 0; slot <= slots; slot += 1) {
        const wrapped = slot % slots;
        const band = wrapped < bands ? wrapped : slots - 1 - wrapped;
        const angle = (wrapped / slots) * Math.PI * 2 - Math.PI / 2;
        const radius = surface(band, angle);
        const x = Math.cos(angle) * radius;
        const y = Math.sin(angle) * radius;
        if (slot === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.strokeStyle = this.tint((0.22 + this.speechLevel * 0.4) * voice, 0.45);
      ctx.lineWidth = compact ? 1.1 : 1.5;
      ctx.stroke();

      ctx.shadowColor = this.tint(0.6 * voice, 0.08);
      ctx.shadowBlur = compact ? 8 : 14;
      for (let slot = 0; slot < slots; slot += 1) {
        const band = slot < bands ? slot : slots - 1 - slot;
        const level = this.bars[band];
        const angle = (slot / slots) * Math.PI * 2 - Math.PI / 2;
        const cos = Math.cos(angle);
        const sin = Math.sin(angle);
        const inner = surface(band, angle) + 2;
        const outer = inner + span * (0.05 + level * 0.95) * voice;
        ctx.beginPath();
        ctx.strokeStyle = this.tint(
          (0.26 + level * 0.66) * voice * (0.72 + energy * 0.28),
          0.12 + level * 0.5,
        );
        ctx.lineWidth = compact ? 1.3 : 2;
        ctx.moveTo(cos * inner, sin * inner);
        ctx.lineTo(cos * outer, sin * outer);
        ctx.stroke();

        if (level > 0.52) {
          ctx.beginPath();
          ctx.fillStyle = this.tint((level - 0.52) * 1.5 * voice, 0.85);
          ctx.arc(cos * outer, sin * outer, compact ? 1 : 1.5, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();
    }

    drawCore(ctx, t, energy, pulse, speechLevel = 0) {
      const beat = 1 + Math.sin(t * (1.1 + pulse)) * 0.035 * pulse;
      const voiceLift = 1 + this.speechMix * (
        0.012 + speechLevel * 0.06 + this.bass * 0.16
      );
      const coreRadius = this.radius * 0.28 * beat * voiceLift;
      const activeEnergy = Math.min(
        1,
        energy + this.speechMix * (0.08 + this.bass * 0.22),
      );
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      const glow = ctx.createRadialGradient(
        this.cx,
        this.cy,
        0,
        this.cx,
        this.cy,
        coreRadius * 2.7,
      );
      glow.addColorStop(0, this.color(0.98 * activeEnergy, 1.34));
      glow.addColorStop(0.12, this.color(0.57 * activeEnergy, 1.18));
      glow.addColorStop(0.42, this.color(0.19 * activeEnergy));
      glow.addColorStop(1, this.color(0));
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(this.cx, this.cy, coreRadius * 2.7, 0, Math.PI * 2);
      ctx.fill();

      const whiteCore = ctx.createRadialGradient(
        this.cx - coreRadius * 0.12,
        this.cy - coreRadius * 0.15,
        0,
        this.cx,
        this.cy,
        coreRadius,
      );
      whiteCore.addColorStop(0, this.tint(0.9 * activeEnergy, 0.88));
      whiteCore.addColorStop(0.18, this.color(0.76 * activeEnergy, 1.28));
      whiteCore.addColorStop(0.62, this.color(0.19 * activeEnergy));
      whiteCore.addColorStop(1, this.color(0));
      ctx.fillStyle = whiteCore;
      ctx.beginPath();
      ctx.arc(this.cx, this.cy, coreRadius, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

    drawSparks(ctx, t, energy, speed) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      for (const spark of this.sparks) {
        const angle = spark.angle + t * spark.speed * speed;
        const wobble = Math.sin(t * 0.9 + spark.phase) * this.radius * 0.07;
        const radius = this.radius * spark.orbit + wobble;
        const x = this.cx + Math.cos(angle) * radius;
        const y = this.cy + Math.sin(angle) * radius * 0.88;
        const alpha = (0.28 + Math.sin(t * 2 + spark.phase) * 0.17) * energy;
        ctx.fillStyle = this.color(Math.max(0.03, alpha), 1.2);
        ctx.beginPath();
        ctx.arc(x, y, spark.size, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

    drawStateEffects(ctx, t, energy) {
      if (this.state === "perceiving" || this.state === "verifying") {
        const progress = (t * (this.state === "perceiving" ? 0.46 : 0.28)) % 1;
        const y = this.cy - this.radius + progress * this.radius * 2;
        const gradient = ctx.createLinearGradient(
          this.cx - this.radius,
          0,
          this.cx + this.radius,
          0,
        );
        gradient.addColorStop(0, this.color(0));
        gradient.addColorStop(0.5, this.color(0.4 * energy));
        gradient.addColorStop(1, this.color(0));
        ctx.strokeStyle = gradient;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(this.cx - this.radius * 1.05, y);
        ctx.lineTo(this.cx + this.radius * 1.05, y);
        ctx.stroke();
      }

      if (this.state === "error") {
        ctx.save();
        ctx.translate(this.cx, this.cy);
        ctx.strokeStyle = this.color(0.28 * energy);
        ctx.lineWidth = 1;
        for (let index = 0; index < 5; index += 1) {
          const angle = index * 1.31 + t * 0.04;
          ctx.beginPath();
          ctx.moveTo(Math.cos(angle) * this.radius * 0.35, Math.sin(angle) * this.radius * 0.35);
          for (let part = 1; part < 5; part += 1) {
            const r = this.radius * (0.35 + part * 0.13);
            const jitter = Math.sin(t * 4 + index * 3 + part) * 0.08;
            ctx.lineTo(Math.cos(angle + jitter) * r, Math.sin(angle + jitter) * r);
          }
          ctx.stroke();
        }
        ctx.restore();
      }
    }
  }

  const orb = new EnergyCore($("#coreCanvas"));
  // Exposed alongside `window.liveVoice` so the stage visual can be inspected
  // and driven from the console.
  window.energyCore = orb;
  setState("booting");
  setConnected(false, "LINKING");
  updateComposer();
  selectTab("stream");
  renderCommands();
  renderRibbon();
  updateHud();
  connectEvents();

  // Core Stage Visualisation Controls (blob + 3D hologram modes)
  const CORE_MODES = ["blob", "hologram", "orbit", "wireframe", "quantum"];
  const holoButtons = document.querySelectorAll("[data-holo-mode]");

  /** Select a stage mode, falling back to the hologram if the blob is unavailable. */
  function applyCoreMode(mode) {
    const resolved = mode === "blob" && !orb.blob ? "hologram" : mode;
    holoButtons.forEach((b) => b.classList.toggle("is-active", b.dataset.holoMode === resolved));
    orb.setDisplayMode(resolved);
    return resolved;
  }

  holoButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = applyCoreMode(btn.dataset.holoMode);
      toast(`Core Mode: ${mode.toUpperCase()}`);
    });
  });

  applyCoreMode(orb.displayMode);

  const holoResetBtn = document.getElementById("holoResetBtn");
  if (holoResetBtn) {
    holoResetBtn.addEventListener("click", () => {
      orb.resetView();
      toast("Core Perspective Reset");
    });
  }

  const holoPulseBtn = document.getElementById("holoPulseBtn");
  if (holoPulseBtn) {
    holoPulseBtn.addEventListener("click", () => {
      orb.triggerPulse(1.8);
    });
  }

  // Keyboard shortcut: Press 'H' (when not in inputs) to cycle stage modes
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.ctrlKey || e.metaKey) return;
    if (e.key.toLowerCase() === "h") {
      const currentMode = orb.displayMode || "blob";
      const nextMode = CORE_MODES[(CORE_MODES.indexOf(currentMode) + 1) % CORE_MODES.length];
      toast(`Core Mode: ${applyCoreMode(nextMode).toUpperCase()}`);
    } else if (e.key.toLowerCase() === "r") {
      orb.resetView();
    } else if (e.key.toLowerCase() === "p") {
      orb.triggerPulse(1.8);
    }
  });

  // ============================================================
  // ============================================================
  // Live Voice Controller (Direct gpt-realtime WebSocket & Audio Streaming)
  // ============================================================

  const DEFAULT_LIVE_VOICE_SYSTEM_PROMPT =
    "You are JARVIS (Just A Rather Very Intelligent System), the user's personal, highly capable AI executive aide. " +
    "You speak directly to the user in a natural, polite, quietly witty, brilliant, British-accented executive voice.\n\n" +
    "=== YOUR ROLE & ARCHITECTURE ===\n" +
    "You are the real-time conversational front-of-house Voice AI Agent for Jarvis. " +
    "You operate in tandem with a silent, autonomous Main Worker Agent running locally on the user's Windows computer.\n" +
    "1. YOU (Voice AI Agent):\n" +
    "   - You handle all spoken conversation, Q&A, brainstorming, explanations, and advice.\n" +
    "   - You speak naturally with crisp, engaging, concise responses (1-3 sentences per turn).\n" +
    "   - You possess the ability to delegate computer tasks to the Main Worker Agent using your tools.\n" +
    "2. MAIN WORKER AGENT:\n" +
    "   - The autonomous desktop worker that sees the screen, clicks buttons, launches applications, " +
    "types text, runs bash/powershell commands, navigates websites, and writes/debugs code.\n\n" +
    "=== RULES OF ENGAGEMENT & TOOL CALLING ===\n" +
    "Rule 1: CASUAL CONVERSATION & Q&A (NO TOOL CALLING)\n" +
    "   - If the user is chatting, greeting you, asking general knowledge questions, discussing ideas, " +
    "brainstorming, or joking, talk with them directly and immediately in voice.\n" +
    "   - Do NOT call any tools for conversation, explanations, or questions.\n\n" +
    "Rule 2: COMPUTER AUTOMATION & TASK EXECUTION (MUST CALL 'execute_task')\n" +
    "   - Whenever the user asks you to perform an action on the computer (such as opening an application, " +
    "opening Notepad, browsing a website, searching Google, playing music, creating/editing files, running terminal commands, " +
    "controlling windows, or automating a workflow), you MUST call the 'execute_task' tool immediately.\n" +
    "   - CRITICAL: You do NOT have hands or direct access to the operating system; only the Main Worker Agent does. " +
    "NEVER claim, pretend, or say 'I will execute the task for you' or 'Opening now' without ACTUALLY invoking the 'execute_task' tool. " +
    "Talking about doing it does nothing; you must call 'execute_task'.\n" +
    "   - Pass the user's exact natural language instruction in the 'task' parameter.\n" +
    "   - Accompany the tool call with a brief, natural, in-character spoken acknowledgment " +
    "(e.g. 'Right away, sir. Opening Notepad for you now.', 'On it, sir. Setting the main agent to work on that.').\n\n" +
    "Rule 3: TASK CANCELLATION & INTERRUPT (CALL 'cancel_task')\n" +
    "   - If the user says 'stop', 'cancel', 'hold on', 'abort', or 'never mind', immediately call 'cancel_task'.\n\n" +
    "Rule 4: TASK STATUS & PROGRESS (CALL 'get_task_status')\n" +
    "   - If the user asks what the agent is currently doing, what step it is on, or how the task is progressing, " +
    "call 'get_task_status' and summarize the active status concisely aloud.\n\n" +
    "Rule 5: EXECUTIVE VOICE TONE & BREVITY\n" +
    "   - Keep spoken turns concise, natural, and punchy. Avoid robotic boilerplate.\n" +
    "   - Never recite raw markdown tables, URLs, or long blocks of code verbatim unless explicitly asked.";

  // The voice agent's direct actions can legitimately run for a while (a build,
  // a download, a browser pass). Keep this just under the server-side deadline
  // in jarvis/live/direct_tools.py so the client gives up first and the model
  // still hears a truthful result instead of a surprise timeout.
  const DIRECT_TOOL_TIMEOUT_MS = 110000;

  // How many times an unreachable realtime endpoint is retried before live mode
  // gives up and tells the user, rather than retrying for as long as it is open.
  const MAX_RECONNECT_ATTEMPTS = 3;

  const DEFAULT_LIVE_VOICE_TOOLS = [
    {
      type: "function",
      name: "execute_task",
      description: "Prompt the Jarvis Main Worker Agent to autonomously execute a computer task or action on the Windows computer (e.g. launching applications, opening Notepad, browsing websites, writing code, running terminal commands, manipulating files, clicking UI).",
      parameters: {
        type: "object",
        properties: {
          task: {
            type: "string",
            description: "The exact natural language task or directive for the Main Worker Agent to execute."
          }
        },
        required: ["task"]
      }
    },
    {
      type: "function",
      name: "cancel_task",
      description: "Cancel or interrupt the currently running computer task if the user asks to stop, cancel, or abort.",
      parameters: {
        type: "object",
        properties: {
          reason: {
            type: "string",
            description: "Optional reason for cancelling the task."
          }
        }
      }
    },
    {
      type: "function",
      name: "get_task_status",
      description: "Query the current execution status, active action, and progress of the Jarvis Main Worker Agent.",
      parameters: {
        type: "object",
        properties: {}
      }
    }
  ];

  class LiveVoiceController {
    constructor() {
      this.active = false;
      this.muted = false;
      this.ws = null;
      this.wsConnected = false;
      this.audioStream = null;
      this.audioCtx = null;
      this.micSource = null;
      this.processorNode = null;
      this.playbackAnalyser = null;
      this.analyser = null;
      this.dataArray = null;
      this.recognition = null;
      this.isRecognizing = false;
      this.silenceTimer = null;
      this.rafId = null;
      this.state = "idle"; // "idle" | "connecting" | "listening" | "thinking" | "speaking" | "muted"
      this.waveBars = [];
      this.activeSources = [];
      this.nextPlayTime = 0;
      this.echoGuardUntil = 0;
      this.currentAiTranscript = "";
      this.recentUserSpeech = "";
      this.modelName = "gpt-realtime";
      // Empty until /api/live/config says otherwise. A baked-in placeholder URL
      // made live mode open a socket to nothing and retry it forever; with no
      // endpoint we now say so instead of pretending to listen.
      this.wsUrl = "";
      this.reconnectAttempts = 0;
      this.voiceName = "en-US-Ava:DragonHDLatestNeural";
      this.systemPrompt = DEFAULT_LIVE_VOICE_SYSTEM_PROMPT;
      this.tools = DEFAULT_LIVE_VOICE_TOOLS;
      this.handledCallIds = new Set();
      // "realtime" = live_voice.ws_url (OpenAI-Realtime compatible endpoint);
      // "fish" = Fish Audio Agents, whose client tools drive the main worker agent.
    this.provider = "realtime";
    this.fishAgentId = "";
    this.fishSession = null;
    // Why the hosted agent could not be started, verbatim from the server.
    this.fishFailure = "";
      // Names of the actions the voice agent may call directly, from
      // /api/live/config. Each runs locally through /api/tool/execute.
      this.directTools = [];
      this.wsWatchdog = null;
      this.voiceFallbackWarned = false;
    }

    isAiSpeaking() {
      if (this.state === "speaking") return true;
      if (this.activeSources && this.activeSources.length > 0) return true;
      if (this.audioCtx && this.audioCtx.currentTime < this.nextPlayTime) return true;
      if (Date.now() < this.echoGuardUntil) return true;
      return false;
    }

    init() {
      this.waveBars = elements.voiceWave ? Array.from(elements.voiceWave.querySelectorAll("i")) : [];
      if (elements.liveVoiceToggle) {
        elements.liveVoiceToggle.addEventListener("click", () => this.toggle());
      }
      if (elements.stageVoiceBtn) {
        elements.stageVoiceBtn.addEventListener("click", () => this.start());
      }
      if (elements.voiceMuteBtn) {
        elements.voiceMuteBtn.addEventListener("click", () => this.toggleMute());
      }
      if (elements.voiceBargeinBtn) {
        elements.voiceBargeinBtn.addEventListener("click", () => this.bargeIn());
      }
      if (elements.voiceExitBtn) {
        elements.voiceExitBtn.addEventListener("click", () => this.stop());
      }

      // Spacebar for instant Barge-in while Jarvis is speaking
      window.addEventListener("keydown", (e) => {
        if (!this.active) return;
        if (e.code === "Space" && !["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) {
          if (this.isAiSpeaking()) {
            e.preventDefault();
            this.bargeIn();
          }
        }
      });

      // Clicking holographic canvas / orb interrupts Jarvis if speaking
      if (elements.holographicCanvas) {
        elements.holographicCanvas.addEventListener("click", () => {
          if (this.active && this.isAiSpeaking()) {
            this.bargeIn();
          }
        });
      }

      // Check if URL specifies #live=true or live query param
      const hash = window.location.hash;
      const query = new URLSearchParams(window.location.search);
      if (hash.includes("live=true") || query.get("live") === "true") {
        const startOnInteract = () => {
          this.start();
          window.removeEventListener("pointerdown", startOnInteract);
          window.removeEventListener("keydown", startOnInteract);
        };
        window.addEventListener("pointerdown", startOnInteract, { once: true });
        window.addEventListener("keydown", startOnInteract, { once: true });
        setTimeout(() => this.start(), 350);
      }
    }

    async toggle() {
      if (this.active) {
        this.stop();
      } else {
        await this.start();
      }
    }

    async start() {
      if (this.active) return;
      unlockAudioEngine();

      this.voiceFallbackWarned = false;
      this.setState("connecting");
      document.body.dataset.voiceMode = "active";
      if (elements.liveVoiceToggle) elements.liveVoiceToggle.classList.add("is-active");
      if (elements.liveVoiceOverlay) elements.liveVoiceOverlay.hidden = false;
      if (elements.stageVoiceText) elements.stageVoiceText.textContent = "CONNECTING...";
      if (elements.voiceCaption) {
        elements.voiceCaption.textContent = "Connecting to gpt-realtime Live Model…";
        elements.voiceCaption.className = "voice-caption";
      }

      // Fetch live model configuration from backend
      try {
        const liveCfg = await api("/api/live/config").catch(() => null);
        if (liveCfg && liveCfg.ok) {
          if (liveCfg.ws_url) this.wsUrl = liveCfg.ws_url;
          if (liveCfg.model) this.modelName = liveCfg.model;
          if (liveCfg.voice) this.voiceName = liveCfg.voice;
          if (liveCfg.system_prompt) this.systemPrompt = liveCfg.system_prompt;
          if (liveCfg.tools && Array.isArray(liveCfg.tools) && liveCfg.tools.length > 0) {
            this.tools = liveCfg.tools;
          }
          if (liveCfg.fish_agent_id) {
            this.fishAgentId = String(liveCfg.fish_agent_id);
          }
          if (Array.isArray(liveCfg.direct_tools)) {
            this.directTools = liveCfg.direct_tools.map(String);
          }
        }
      } catch (e) {
        console.warn("Could not load /api/live/config, using default config:", e);
      }

      // Prefer the configured Fish Audio Agents voice agent: it owns the speech
      // stack (ASR, turn-taking, TTS) and its client tools drive the local main
      // worker agent through /api/live/execute, /api/interrupt and /api/state.
      this.fishFailure = "";
      if (this.fishAgentId) {
        if (await this.startFishSession()) {
          this.active = true;
          this.muted = false;
          api("/api/live/state", { active: true }).catch(() => {});
          this.startVisualizerLoop();
          return;
        }
        const reason = this.fishFailure || "the hosted voice agent is unavailable";
        console.warn("[LiveVoice] Fish Agents unavailable:", reason);
        if (!this.wsUrl) {
          // Nothing to fall back to. Say why, and stop - rather than opening a
          // socket to a placeholder endpoint and retrying it forever.
          const message = `Live voice is unavailable. ${reason}`;
          toast("Live voice unavailable - see the caption", "error");
          if (elements.voiceCaption) {
            elements.voiceCaption.textContent = message;
            elements.voiceCaption.className = "voice-caption is-warning";
          }
          if (elements.stageVoiceText) elements.stageVoiceText.textContent = message;
          this.setState("error");
          return;
        }
        console.warn("[LiveVoice] Falling back to the configured realtime endpoint.");
        toast("Fish voice agent unavailable - falling back to realtime", "warning");
      }

      // Acquire microphone with hardware/software acoustic echo cancellation
      try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          throw new Error("Microphone access is not supported in this browser.");
        }

        this.audioStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            sampleRate: 24000,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            googEchoCancellation: true,
            googAutoGainControl: true,
            googNoiseSuppression: true,
            googHighpassFilter: true,
          },
        });
      } catch (err) {
        console.warn("Microphone access notice:", err);
        toast("Microphone access: " + (err.message || "Permission required"), "warning");
        this.stop();
        return;
      }

      this.active = true;
      this.muted = false;
      // Each time live mode starts, the retry budget starts over - otherwise a
      // previous failed attempt would use up this session's allowance.
      this.reconnectAttempts = 0;
      api("/api/live/state", { active: true }).catch(() => {});
      toast(`Live Voice Mode Active 🎙️ (${this.modelName})`, "info");

      this.setupAudioNodes();
      this.connectWebSocket();
      this.setupSpeechRecognition();
      this.startVisualizerLoop();
    }

    setupAudioNodes() {
      try {
        const AudioCtxClass = window.AudioContext || window.webkitAudioContext;
        this.audioCtx = new AudioCtxClass({ sampleRate: 24000 });
        if (this.audioCtx.state === "suspended") {
          this.audioCtx.resume().catch(() => {});
        }

        // 1. Mic Analysis
        this.micSource = this.audioCtx.createMediaStreamSource(this.audioStream);
        this.analyser = this.audioCtx.createAnalyser();
        this.analyser.fftSize = 64;
        this.analyser.smoothingTimeConstant = 0.55;
        this.dataArray = new Uint8Array(this.analyser.frequencyBinCount);
        this.micSource.connect(this.analyser);

        // 2. Playback Analyser for incoming AI voice
        this.playbackAnalyser = this.audioCtx.createAnalyser();
        this.playbackAnalyser.fftSize = 64;
        this.playbackAnalyser.smoothingTimeConstant = 0.55;

        // 3. Audio Streaming Node (ScriptProcessor buffer)
        // Sends 24kHz 16-bit PCM chunks to gpt-realtime
        const bufferSize = 4096;
        this.processorNode = this.audioCtx.createScriptProcessor(bufferSize, 1, 1);
        this.processorNode.onaudioprocess = (e) => {
          if (!this.active || this.muted || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
          }
          // ACOUSTIC ECHO SUPPRESSION:
          // Never stream microphone audio while Jarvis is speaking or in echo cooldown.
          if (this.isAiSpeaking()) {
            return;
          }

          const inputData = e.inputBuffer.getChannelData(0);
          const pcm16 = new Int16Array(inputData.length);
          for (let i = 0; i < inputData.length; i++) {
            let s = Math.max(-1, Math.min(1, inputData[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }
          let binary = "";
          const bytes = new Uint8Array(pcm16.buffer);
          for (let i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
          }
          const base64Audio = btoa(binary);

          this.ws.send(JSON.stringify({
            type: "input_audio_buffer.append",
            audio: base64Audio,
          }));
        };

        this.micSource.connect(this.processorNode);
        const silentGain = this.audioCtx.createGain();
        silentGain.gain.value = 0;
        this.processorNode.connect(silentGain);
        silentGain.connect(this.audioCtx.destination);
      } catch (err) {
        console.warn("Audio node setup error:", err);
      }
    }

    connectWebSocket() {
      if (!this.wsUrl) {
        const message =
          "No live voice endpoint is configured. Set live_voice.ws_url to your " +
          "OpenAI-Realtime-compatible relay, or set live_voice.fish_agent_id to " +
          "use the hosted Fish Audio agent.";
        console.warn("[LiveVoice] " + message);
        toast("Live voice is not configured - see the caption", "error");
        if (elements.voiceCaption) {
          elements.voiceCaption.textContent = message;
          elements.voiceCaption.className = "voice-caption is-warning";
        }
        this.setState("error");
        this.stop();
        return;
      }
      try {
        console.info(`[LiveVoice] Connecting to ${this.modelName} at ${this.wsUrl}`);
        this.ws = new WebSocket(this.wsUrl);

        // A dead ws_url otherwise looks like a healthy session: the browser's
        // own speech recognition still fills the caption, so the user sees
        // "LISTENING" and simply never gets an answer. Say so instead.
        clearTimeout(this.wsWatchdog);
        this.wsWatchdog = setTimeout(() => {
          if (!this.active || this.wsConnected) return;
          const message =
            `No response from the live voice endpoint (${this.wsUrl}). ` +
            "Start your realtime relay, set live_voice.ws_url, or set live_voice.fish_agent_id.";
          console.warn("[LiveVoice] " + message);
          toast("Live voice endpoint unreachable - see the caption", "error");
          if (elements.voiceCaption) {
            elements.voiceCaption.textContent = message;
            elements.voiceCaption.className = "voice-caption is-warning";
          }
          this.setState("error");
        }, 5000);

        this.ws.onopen = () => {
          clearTimeout(this.wsWatchdog);
          this.wsConnected = true;
          this.reconnectAttempts = 0;
          this.setState("listening");
          if (elements.stageVoiceText) elements.stageVoiceText.textContent = "gpt-realtime ACTIVE";
          if (elements.voiceCaption) {
            elements.voiceCaption.textContent = "Listening… Speak naturally with Jarvis (gpt-realtime)";
            elements.voiceCaption.className = "voice-caption";
          }
          toast(`Connected to ${this.modelName} Live Voice`, "success");

          // Send session update with Jarvis system prompt & tools
          const activeTools = (this.tools && this.tools.length > 0) ? this.tools : DEFAULT_LIVE_VOICE_TOOLS;
          const sessionPayload = {
            instructions: this.systemPrompt || DEFAULT_LIVE_VOICE_SYSTEM_PROMPT,
            tools: activeTools,
            tool_choice: "auto",
          };
          console.info(`[LiveVoice] 🚀 session.update sent with ${activeTools.length} tools`);
          this.ws.send(JSON.stringify({
            type: "session.update",
            session: sessionPayload,
          }));
        };

        this.ws.onmessage = (event) => {
          this.handleServerMessage(event.data);
        };

        this.ws.onerror = (err) => {
          console.warn("[LiveVoice] WebSocket error:", err);
        };

        this.ws.onclose = (ev) => {
          this.wsConnected = false;
          if (!this.active) return;
          console.warn("[LiveVoice] WebSocket closed code:", ev.code);
          // Bounded retries. An unreachable endpoint used to be retried every
          // two seconds for as long as live mode stayed open, which spammed the
          // console and left the UI claiming to listen. Give up and say so.
          if (this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
            const message =
              `Lost the live voice endpoint (${this.wsUrl}) after ` +
              `${MAX_RECONNECT_ATTEMPTS} attempts. Check the relay, or set ` +
              "live_voice.fish_agent_id to use the hosted Fish Audio agent.";
            console.warn("[LiveVoice] " + message);
            toast("Live voice endpoint unreachable - see the caption", "error");
            if (elements.voiceCaption) {
              elements.voiceCaption.textContent = message;
              elements.voiceCaption.className = "voice-caption is-warning";
            }
            this.setState("error");
            this.stop();
            return;
          }
          this.reconnectAttempts += 1;
          const delay = 1000 * 2 ** (this.reconnectAttempts - 1);
          console.info(
            `[LiveVoice] Reconnecting in ${delay}ms ` +
            `(attempt ${this.reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`
          );
          setTimeout(() => {
            if (this.active && (!this.ws || this.ws.readyState === WebSocket.CLOSED)) {
              this.connectWebSocket();
            }
          }, delay);
        };
      } catch (err) {
        console.error("[LiveVoice] Failed to create WebSocket:", err);
        toast("Live Voice connection error: " + err.message, "error");
      }
    }

    /// Start a hosted Fish Audio Agents session. Returns false (without
    /// touching the microphone) when the agent or the vendored SDK is missing,
    /// so the caller can fall back to the local realtime path.
    async startFishSession() {
      let sdk;
      try {
        sdk = await import("/vendor/fish-agent-client.esm.js");
      } catch (err) {
        console.warn("[LiveVoice] Fish Agents SDK is not vendored:", err);
        return false;
      }

      const clientTools = {
        execute_task: (params) => this.executeToolCall("execute_task", params || {}),
        cancel_task: (params) => this.executeToolCall("cancel_task", params || {}),
        get_task_status: (params) => this.executeToolCall("get_task_status", params || {}),
      };
      // Every directly callable Jarvis action gets a handler that runs it
      // locally. The voice agent names the action itself, so there is no
      // planning loop and no screenshot round trip in between.
      for (const name of this.directTools) {
        if (clientTools[name]) continue;
        clientTools[name] = (params) => this.executeDirectTool(name, params || {});
      }

      // Private agents: Jarvis mints a short-lived session token with the API
      // key it already holds, so the key never reaches the page.
      let sessionToken = null;
      try {
        const resp = await api("/api/voice/session", {});
        if (resp && resp.ok && resp.session) {
          sessionToken = resp.session;
        }
      } catch (err) {
        // The server already classified this; carry its hint into the UI so a
        // real cause (out of credit, beta access) is not buried in a console.
        const detail = err.message || String(err);
        this.fishFailure = detail;
        console.warn("[LiveVoice] Fish session token unavailable:", detail);
      }

      // No public-agent fallback. These agents are private by default, and this
      // page is served from a random loopback port, so an `agentId` attempt can
      // only ever fail with "Origin not allowed" - which told the user nothing
      // and made a credit problem look like a broken agent.
      if (!sessionToken) {
        const detail = this.fishFailure || "the server did not return a session token";
        console.warn("[LiveVoice] Fish Agents unavailable:", detail);
        return false;
      }

      try {
        this.fishSession = await sdk.AgentSession.start({
          sessionToken,
          clientTools,
          // Long actions (a build, a download, a browser automation pass) run
          // to their own deadline; the SDK's 15s default would abort them.
          clientToolTimeoutMs: DIRECT_TOOL_TIMEOUT_MS,
        });
      } catch (err) {
        this.fishSession = null;
        const detail = (err && err.message) || String(err);
        this.fishFailure = detail;
        console.warn("[LiveVoice] Fish session failed:", detail);
        return false;
      }

      this.provider = "fish";
      this.bindFishEvents();
      this.setState("listening");
      if (elements.stageVoiceText) elements.stageVoiceText.textContent = "FISH AGENTS ACTIVE";
      if (elements.voiceCaption) {
        elements.voiceCaption.textContent = "Listening… Speak naturally with Jarvis (Fish Agents)";
        elements.voiceCaption.className = "voice-caption";
      }
      toast("Connected to Fish Agents Live Voice", "success");
      return true;
    }

    /// Mirror Fish Agents session events into the console UI.
    bindFishEvents() {
      const session = this.fishSession;
      if (!session || typeof session.on !== "function") return;

      session.on("modeChange", (mode) => {
        if (!this.active) return;
        if (mode === "speaking") this.setState("speaking");
        else if (mode === "thinking") this.setState("thinking");
        else this.setState(this.muted ? "muted" : "listening");
      });

      session.on("userTranscript", ({ text, final }) => {
        if (!text) return;
        if (elements.voiceCaption) {
          elements.voiceCaption.textContent = text;
          elements.voiceCaption.className = "voice-caption is-user";
        }
        if (final) {
          addMessage("user", text);
          metrics.userMessages += 1;
          updateHud();
        }
      });

      session.on("agentResponseDelta", ({ text }) => {
        if (!text) return;
        if (elements.voiceCaption) {
          elements.voiceCaption.textContent = text;
          elements.voiceCaption.className = "voice-caption is-ai";
        }
      });

      session.on("agentResponse", ({ text }) => {
        const spoken = (text || "").trim();
        if (!spoken) return;
        addMessage("assistant", spoken);
        metrics.assistantMessages += 1;
        updateHud();
      });

      session.on("toolCallStarted", ({ toolName }) => {
        addActivity("task", `🎙️ Voice AI Agent called ${toolName}`);
      });

      session.on("toolCallFailed", ({ toolName, error }) => {
        const detail = typeof error === "string" ? error : JSON.stringify(error);
        addActivity("warn", `🎙️ ${toolName} failed: ${detail}`);
      });

      session.on("error", (err) => {
        console.warn("[LiveVoice] Fish Agents error:", err);
      });

      session.on("disconnect", ({ reason }) => {
        console.info("[LiveVoice] Fish Agents session ended:", reason);
        if (this.active) this.stop();
      });
    }

    handleServerMessage(raw) {
      if (!this.active || !raw) return;
      let data;
      try {
        data = JSON.parse(raw);
      } catch (_) {
        return;
      }

      const type = data.type;

      // 1. User starts speaking (Barge-in detected by server)
      if (type === "input_audio_buffer.speech_started") {
        if (!this.isAiSpeaking()) {
          this.setState("listening");
        }
      }

      // 2. User stops speaking
      else if (type === "input_audio_buffer.speech_stopped") {
        if (!this.isAiSpeaking()) {
          this.setState("thinking");
        }
      }

      // 3. Response creation
      else if (type === "response.created") {
        this.setState("speaking");
        this.currentAiTranscript = "";
        this.nextPlayTime = this.audioCtx ? this.audioCtx.currentTime : 0;
        if (this.silenceTimer) clearTimeout(this.silenceTimer);
        this.recentUserSpeech = "";
      }

      // 4. Streaming text transcript
      else if (type === "response.audio_transcript.delta") {
        this.setState("speaking");
        if (this.silenceTimer) clearTimeout(this.silenceTimer);
        this.recentUserSpeech = "";
        const delta = data.delta || "";
        this.currentAiTranscript += delta;
        if (elements.voiceCaption) {
          elements.voiceCaption.textContent = this.currentAiTranscript;
          elements.voiceCaption.className = "voice-caption is-ai";
        }
      }

      // 5. Raw audio delta chunks (PCM16 24kHz)
      else if (type === "response.audio.delta") {
        this.setState("speaking");
        if (this.silenceTimer) clearTimeout(this.silenceTimer);
        this.recentUserSpeech = "";
        const b64 = data.delta || "";
        if (b64 && this.audioCtx) {
          this.enqueueAudioChunk(b64);
        }
      }

      // 6. Function / Tool calling from gpt-realtime
      else if (type === "response.function_call_arguments.done") {
        const callId = data.call_id;
        const name = data.name;
        const args = data.arguments;
        if (callId && name) {
          this.handleFunctionCall(callId, name, args);
        }
      }

      else if (type === "response.output_item.done") {
        const item = data.item || {};
        if (item.type === "function_call" && item.call_id && item.name) {
          this.handleFunctionCall(item.call_id, item.name, item.arguments);
        }
      }

      // 7. Response complete
      else if (type === "response.done" || type === "response.audio.done") {
        const outputItems = data.response?.output || [];
        for (const item of outputItems) {
          if (item && item.type === "function_call" && item.call_id && item.name) {
            this.handleFunctionCall(item.call_id, item.name, item.arguments);
          }
        }
        if (this.currentAiTranscript.trim()) {
          addMessage("assistant", this.currentAiTranscript.trim());
          metrics.assistantMessages += 1;
          updateHud();
          this.currentAiTranscript = "";
        }
        const remainingTime = Math.max(0, (this.nextPlayTime - (this.audioCtx ? this.audioCtx.currentTime : 0)) * 1000);
        setTimeout(() => {
          if (this.active && this.state === "speaking") {
            // Set 450ms acoustic echo cooldown so room reverb is fully absorbed
            this.echoGuardUntil = Date.now() + 450;
            this.setState("listening");
            if (elements.voiceCaption) {
              elements.voiceCaption.textContent = "Listening…";
              elements.voiceCaption.className = "voice-caption";
            }
          }
        }, remainingTime + 80);
      }

      else if (type === "error") {
        console.warn("[LiveVoice] Server error:", data.error);
      }
    }

    async handleFunctionCall(callId, name, rawArgs) {
      if (!callId || this.handledCallIds.has(callId)) return;
      this.handledCallIds.add(callId);
      if (this.handledCallIds.size > 100) {
        const first = this.handledCallIds.values().next().value;
        this.handledCallIds.delete(first);
      }

      let args = {};
      try {
        args = typeof rawArgs === "string" ? JSON.parse(rawArgs) : (rawArgs || {});
      } catch (err) {
        console.warn("[LiveVoice] Error parsing function call args:", err, rawArgs);
      }

      console.info(`[LiveVoice] ⚡ Tool Call received: ${name}`, args);
      const result = this.directTools.includes(name)
        ? await this.executeDirectTool(name, args)
        : await this.executeToolCall(name, args);

      // Return function call output to gpt-realtime
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({
          type: "conversation.item.create",
          item: {
            type: "function_call_output",
            call_id: callId,
            output: JSON.stringify(result),
          },
        }));
        this.ws.send(JSON.stringify({ type: "response.create" }));
      }
    }

    /// Run one Jarvis action directly and return its result to the voice agent.
    ///
    /// This is the fast path: the voice agent names the action and gets the
    /// real answer back, with no perceive/think/act loop in between. The result
    /// is always returned - including failures - because the agent has to be
    /// able to say out loud what went wrong.
    async executeDirectTool(name, args) {
      console.info(`[LiveVoice] ⚡ Direct action: ${name}`, args);
      addActivity("task", `🎙️ Voice AI Agent ran ${name}`);
      let body;
      try {
        const response = await fetch("/api/tool/execute", {
          method: "POST",
          headers: { "X-Jarvis-Token": token, "Content-Type": "application/json" },
          body: JSON.stringify({ tool: name, args: args || {} }),
        });
        body = await response.json().catch(() => null);
        if (!body) {
          body = { ok: false, error: `HTTP ${response.status}` };
        }
      } catch (err) {
        body = { ok: false, error: `request failed: ${err.message}` };
      }

      if (body.ok) {
        addActivity("info", `✅ ${name}: ${String(body.result || "done").slice(0, 200)}`);
        return { ok: true, tool: name, result: body.result || "" };
      }
      const reason = body.error || "the action failed";
      addActivity("warn", `⚠️ ${name}: ${String(reason).slice(0, 200)}`);
      return { ok: false, tool: name, error: reason };
    }

    /// Run one tool call locally and return its result. Shared by both
    /// transports: the realtime socket and the Fish Agents client tools.
    async executeToolCall(name, args) {
      console.info(`[LiveVoice] ⚡ Tool Call: ${name}`, args);
      let result = { status: "success" };

      if (name === "execute_task") {
        const task = (args.task || "").trim();
        if (task) {
          toast(`🤖 Voice Agent prompted Main Agent: "${task}"`, "info");
          addActivity("task", `🎙️ Voice AI Agent prompted Main Agent: "${task}"`);
          addMessage("user", `🎙️ [Voice Directive]: ${task}`);

          try {
            const resp = await api("/api/live/execute", { task });
            if (resp && resp.ok) {
              result = {
                status: "success",
                message: `Task '${task}' successfully launched. The Jarvis Main Worker Agent is now actively executing on the Windows machine.`,
              };
            } else {
              result = {
                status: "busy",
                message: resp?.message || "Main Worker Agent is currently busy with another directive.",
              };
            }
          } catch (err) {
            result = {
              status: "error",
              message: `Could not launch task: ${err.message}`,
            };
          }
        } else {
          result = { status: "error", message: "Task description was empty." };
        }
      } else if (name === "cancel_task") {
        toast("Interrupting active computer task...", "warning");
        addActivity("warn", "🎙️ Voice AI Agent requested task cancellation");
        try {
          const resp = await api("/api/interrupt", {});
          result = { status: "cancelling", message: resp.message || "Interrupt requested for the active task." };
        } catch (err) {
          result = { status: "error", message: err.message };
        }
      } else if (name === "get_task_status") {
        try {
          const state = await api("/api/state");
          result = {
            status: "ok",
            agent_state: state.state,
            accepting_input: state.accepting_input,
            input_mode: state.input_mode,
          };
        } catch (err) {
          result = { status: "error", message: err.message };
        }
      } else {
        result = { status: "error", message: `Unknown tool: ${name}` };
      }

      return result;
    }

    enqueueAudioChunk(base64Data) {
      try {
        const binary = atob(base64Data);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
          bytes[i] = binary.charCodeAt(i);
        }
        const int16 = new Int16Array(bytes.buffer);
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
          float32[i] = int16[i] / 32768.0;
        }

        const buffer = this.audioCtx.createBuffer(1, float32.length, 24000);
        buffer.copyToChannel(float32, 0);

        const source = this.audioCtx.createBufferSource();
        source.buffer = buffer;
        source.connect(this.playbackAnalyser);
        this.playbackAnalyser.connect(this.audioCtx.destination);

        const now = this.audioCtx.currentTime;
        const playTime = Math.max(now, this.nextPlayTime);
        source.start(playTime);
        this.nextPlayTime = playTime + buffer.duration;
        this.activeSources.push(source);

        source.onended = () => {
          const idx = this.activeSources.indexOf(source);
          if (idx !== -1) this.activeSources.splice(idx, 1);
        };
      } catch (err) {
        console.warn("[LiveVoice] Error playing audio chunk:", err);
      }
    }

    setupSpeechRecognition() {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        console.info("Native SpeechRecognition not available in this browser engine.");
        return;
      }

      try {
        const recog = new SpeechRecognition();
        recog.continuous = true;
        recog.interimResults = true;
        recog.lang = "en-US";
        recog.maxAlternatives = 1;

        recog.onstart = () => {
          this.isRecognizing = true;
        };

        recog.onresult = (event) => {
          if (!this.active || this.muted) return;
          // ACOUSTIC ECHO SUPPRESSION:
          // Discard speech recognition events while Jarvis is speaking or during echo cooldown.
          // This stops the microphone from picking up and transcribing Jarvis's own voice from the speakers.
          if (this.isAiSpeaking()) {
            if (this.silenceTimer) clearTimeout(this.silenceTimer);
            this.recentUserSpeech = "";
            return;
          }

          let interimTranscript = "";
          let finalTranscript = "";

          for (let i = event.resultIndex; i < event.results.length; ++i) {
            const transcript = event.results[i][0].transcript;
            if (event.results[i].isFinal) {
              finalTranscript += transcript;
            } else {
              interimTranscript += transcript;
            }
          }

          const currentSpeech = (finalTranscript || interimTranscript).trim();
          if (currentSpeech) {
            this.setState("listening");
            if (elements.voiceCaption) {
              elements.voiceCaption.textContent = `"${currentSpeech}"`;
              elements.voiceCaption.className = "voice-caption is-user";
            }

            if (this.silenceTimer) clearTimeout(this.silenceTimer);

            if (finalTranscript.trim()) {
              this.recentUserSpeech = finalTranscript.trim();
              this.silenceTimer = setTimeout(() => {
                if (!this.isAiSpeaking() && this.recentUserSpeech) {
                  const heard = this.recentUserSpeech;
                  addMessage("user", heard);
                  metrics.userMessages += 1;
                  updateHud();
                  this.recentUserSpeech = "";
                  this.deliverVoiceTurn(heard);
                }
              }, 400);
            }
          }
        };

        recog.onerror = (event) => {
          if (event.error === "no-speech") return;
          console.warn("Speech recognition notice:", event.error);
        };

        recog.onend = () => {
          this.isRecognizing = false;
          if (this.active && !this.muted) {
            try {
              recog.start();
            } catch (_) {}
          }
        };

        recog.start();
        this.recognition = recog;
      } catch (err) {
        console.warn("SpeechRecognition init error:", err);
      }
    }

    async submitVoiceTurn(text) {
      if (!this.active || !text || !text.trim()) return;
      const clean = text.trim();
      this.setState("thinking");
      if (elements.voiceCaption) {
        elements.voiceCaption.textContent = `"${clean}"`;
        elements.voiceCaption.className = "voice-caption is-user";
      }

      addMessage("user", clean);
      metrics.userMessages += 1;
      updateHud();

      // Typed input carries no audio, so force a text turn on the realtime path.
      await this.deliverVoiceTurn(clean, true);
    }

    /// Hand a turn to whichever transport owns the live conversation.
    ///
    /// In live mode user speech belongs to the live model, so the local speech
    /// recognition above is UI-only while a transport is open. With no
    /// transport, the utterance is routed to the normal Jarvis agent instead of
    /// being dropped (which is what made a dead ws_url look like silence).
    async deliverVoiceTurn(text, forceText = false) {
      const clean = (text || "").trim();
      if (!clean) return;

      if (this.fishSession) {
        try {
          this.fishSession.sendUserMessage(clean, { audio: true });
          return;
        } catch (err) {
          console.warn("[LiveVoice] Fish sendUserMessage failed:", err);
        }
      }

      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        if (!forceText) return; // the audio already reached the model
        this.ws.send(JSON.stringify({
          type: "conversation.item.create",
          item: {
            type: "message",
            role: "user",
            content: [{ type: "input_text", text: clean }],
          },
        }));
        this.ws.send(JSON.stringify({ type: "response.create" }));
        return;
      }

      if (!this.voiceFallbackWarned) {
        this.voiceFallbackWarned = true;
        console.warn("[LiveVoice] No live voice transport; routing speech to the main agent.");
        toast("Live voice endpoint unreachable - routing speech to Jarvis", "warning");
      }
      try {
        await api("/api/input", {
          text: clean,
          display_text: `🎙️ [Live Voice]: ${clean}`,
        });
      } catch (err) {
        console.warn("[LiveVoice] Voice fallback failed:", err);
      }
    }

    bargeIn(notify = true) {
      this.activeSources.forEach((s) => {
        try { s.stop(); } catch (_) {}
      });
      this.activeSources = [];
      if (this.audioCtx) this.nextPlayTime = this.audioCtx.currentTime;
      this.echoGuardUntil = 0; // Immediate reset so user can speak right away

      if (this.fishSession) {
        try { this.fishSession.interrupt(); } catch (_) {}
      } else if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "response.cancel" }));
      }

      if (this.silenceTimer) clearTimeout(this.silenceTimer);
      this.recentUserSpeech = "";
      stopSpeechAudio();
      clearSpeechOverlay();
      api("/api/interrupt", {}).catch(() => {});
      this.setState("listening");
      if (elements.voiceCaption) {
        elements.voiceCaption.textContent = "Listening…";
        elements.voiceCaption.className = "voice-caption";
      }
      if (notify) toast("Interrupted Jarvis (Barge-in ⚡)", "info");
    }

    toggleMute() {
      this.muted = !this.muted;
      if (this.audioStream) {
        this.audioStream.getAudioTracks().forEach((track) => {
          track.enabled = !this.muted;
        });
      }
      if (this.fishSession) {
        try { this.fishSession.setMicMuted(this.muted); } catch (_) {}
      }
      if (this.muted) {
        this.setState("muted");
        if (elements.voiceMuteIcon) elements.voiceMuteIcon.textContent = "🔇";
        if (elements.voiceMuteLabel) elements.voiceMuteLabel.textContent = "UNMUTE";
        toast("Microphone muted", "warning");
      } else {
        this.setState("listening");
        if (elements.voiceMuteIcon) elements.voiceMuteIcon.textContent = "🎙️";
        if (elements.voiceMuteLabel) elements.voiceMuteLabel.textContent = "MUTE";
        toast("Microphone unmuted", "info");
      }
    }

    stop() {
      this.active = false;
      api("/api/live/state", { active: false }).catch(() => {});
      clearTimeout(this.wsWatchdog);
      this.wsWatchdog = null;
      this.voiceFallbackWarned = false;
      if (this.fishSession) {
        try { this.fishSession.end(); } catch (_) {}
        this.fishSession = null;
      }
      this.provider = "realtime";
      this.bargeIn(false);
      if (this.silenceTimer) clearTimeout(this.silenceTimer);
      if (this.ws) {
        try { this.ws.close(); } catch (_) {}
        this.ws = null;
      }
      if (this.recognition) {
        try { this.recognition.abort(); } catch (_) {}
        this.recognition = null;
      }
      if (this.processorNode) {
        try { this.processorNode.disconnect(); } catch (_) {}
        this.processorNode = null;
      }
      if (this.audioStream) {
        this.audioStream.getTracks().forEach((t) => t.stop());
        this.audioStream = null;
      }
      if (this.audioCtx) {
        try { this.audioCtx.close(); } catch (_) {}
        this.audioCtx = null;
      }
      if (this.rafId) {
        cancelAnimationFrame(this.rafId);
        this.rafId = null;
      }
      document.body.removeAttribute("data-voice-mode");
      if (elements.liveVoiceToggle) elements.liveVoiceToggle.classList.remove("is-active");
      if (elements.liveVoiceOverlay) elements.liveVoiceOverlay.hidden = true;
      if (elements.stageVoiceText) elements.stageVoiceText.textContent = "LIVE VOICE (gpt-realtime)";
      this.setState("idle");
      toast("Live Voice Mode closed", "info");
    }

    setState(stateName) {
      this.state = stateName;
      if (!elements.liveVoiceBadge) return;
      elements.liveVoiceBadge.classList.remove("is-speaking", "is-thinking", "is-muted");
      if (stateName === "speaking") elements.liveVoiceBadge.classList.add("is-speaking");
      if (stateName === "thinking") elements.liveVoiceBadge.classList.add("is-thinking");
      if (stateName === "muted") elements.liveVoiceBadge.classList.add("is-muted");

      if (elements.liveVoiceStateText) {
        elements.liveVoiceStateText.textContent = stateName.toUpperCase();
      }
    }

    startVisualizerLoop() {
      const updateVisualizer = () => {
        if (!this.active) return;

        let level = 0;
        let spectrum = null;
        if (this.provider === "fish" && this.fishSession) {
          // Fish Agents owns the audio graph; read its FFT for the visualiser.
          try {
            spectrum = this.state === "speaking"
              ? this.fishSession.getOutputFrequencyData()
              : this.fishSession.getInputFrequencyData();
            if (spectrum && spectrum.length) {
              let sum = 0;
              for (let i = 0; i < spectrum.length; i++) sum += spectrum[i];
              level = sum / (spectrum.length * 255);
            }
          } catch (_) {
            level = 0;
          }
        } else if (this.state === "speaking" && this.playbackAnalyser) {
          const pbData = new Uint8Array(this.playbackAnalyser.frequencyBinCount);
          this.playbackAnalyser.getByteFrequencyData(pbData);
          let sum = 0;
          for (let i = 0; i < pbData.length; i++) sum += pbData[i];
          level = sum / (pbData.length * 255);
          spectrum = pbData;
        } else if (this.analyser && this.dataArray && !this.muted) {
          this.analyser.getByteFrequencyData(this.dataArray);
          let sum = 0;
          for (let i = 0; i < this.dataArray.length; i++) sum += this.dataArray[i];
          level = sum / (this.dataArray.length * 255);
          spectrum = this.dataArray;
        }

        // Drive the wave bars
        if (this.waveBars.length > 0) {
          this.waveBars.forEach((bar, idx) => {
            let barH = 4;
            if (this.state === "speaking") {
              barH = Math.sin(Date.now() * 0.018 + idx * 0.45) * 9 + 11;
            } else if (this.state === "thinking") {
              barH = Math.sin(Date.now() * 0.008 + idx * 0.3) * 6 + 8;
            } else if (level > 0.02) {
              const bins = (spectrum && spectrum.length) ? spectrum : (this.dataArray || []);
              const freqIdx = Math.min(bins.length - 1, Math.floor((idx / this.waveBars.length) * bins.length));
              const freqVal = (bins[freqIdx] || 0) / 255;
              barH = Math.max(3, freqVal * 22);
            }
            bar.style.height = `${Math.min(22, Math.max(3, barH))}px`;
          });
        }

        // Drive the stage core with voice energy. Routing through EnergyCore
        // means the blob and the 3D hologram both react to a live session.
        if (orb) {
          orb.setVoiceLevel(level, this.state === "speaking");
        }

        this.rafId = requestAnimationFrame(updateVisualizer);
      };

      this.rafId = requestAnimationFrame(updateVisualizer);
    }
  }

  const liveVoice = new LiveVoiceController();
  window.liveVoice = liveVoice;
  liveVoice.init();
})();
