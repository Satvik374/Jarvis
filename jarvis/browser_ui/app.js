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
    copyTerminal: $("#copyTerminal"),
    endSession: $("#endSession"),
    fileInput: $("#fileInput"),
    messageFollow: $("#messageFollow"),
    messages: $("#messages"),
    prompt: $("#promptInput"),
    removeAttachment: $("#removeAttachment"),
    send: $("#sendButton"),
    sessionTimer: $("#sessionTimer"),
    stateDetail: $("#stateDetail"),
    stateIndex: $("#stateIndex"),
    stateTitle: $("#stateTitle"),
    terminalDrawer: $("#terminalDrawer"),
    terminalFollow: $("#terminalFollow"),
    terminalOutput: $("#terminalOutput"),
    terminalToggle: $("#terminalToggle"),
    toastRegion: $("#toastRegion"),
    welcome: $("#welcomeCard"),
  };

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

  const sessionStarted = Date.now();
  let acceptingInput = false;
  let inputMode = "command";
  let inputPrompt = "";
  let connected = false;
  let attachment = null;
  let attachmentUploading = false;
  let uploadVersion = 0;
  let terminalText = "";
  let currentState = "booting";
  let eventSource = null;
  let powerArmed = false;
  let powerTimer = null;
  let eventGeneration = 0;
  let activeUtteranceId = 0;
  let speechIdWatermark = 0;
  let speechExpiryTimer = null;

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

  function setConnected(value, label) {
    connected = value;
    root.dataset.connected = value ? "true" : "false";
    const status = label || (value ? "LINKED" : "OFFLINE");
    elements.connectionText.textContent = status;
    elements.connectionPill.setAttribute(
      "aria-label",
      `Connection status: ${status.toLowerCase()}`,
    );
    elements.connectionPill.title = value
      ? "Connected to the local terminal runtime"
      : "Terminal link unavailable";
  }

  function setState(name, detail) {
    if (!(name in stateMeta)) name = "working";
    currentState = name;
    const [index, title, fallback] = stateMeta[name];
    root.dataset.state = name;
    elements.stateIndex.textContent = index;
    elements.stateTitle.textContent = title;
    elements.stateDetail.textContent = String(detail || fallback).replace(/\s+/g, " ").trim();
    orb.setState(name);
    if (name === "offline") {
      acceptingInput = false;
      clearSpeechOverlay();
      updateComposer();
    }
  }

  function updateComposer(promptText) {
    if (promptText !== undefined) inputPrompt = String(promptText || "");
    const enabled = connected && acceptingInput && currentState !== "offline";
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
    } else if (currentState === "offline") {
      elements.prompt.placeholder = "Terminal session has ended";
    } else {
      elements.prompt.placeholder = "Jarvis is working…";
    }
    updateSendEnabled();
    document.querySelectorAll("[data-suggestion]").forEach((button) => {
      button.disabled = !enabled || inputMode !== "command";
    });
  }

  function updateSendEnabled() {
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
      throw new Error(body.error || body.message || `HTTP ${response.status}`);
    }
    return body;
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

  function addActivity(kind, message, timestamp) {
    const text = String(message || "").replace(/\s+/g, " ").trim();
    if (!text) return;
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

  function clearSpeechOverlay() {
    if (speechExpiryTimer !== null) {
      clearTimeout(speechExpiryTimer);
      speechExpiryTimer = null;
    }
    activeUtteranceId = 0;
    root.dataset.speaking = "false";
    orb.setSpeaking({ active: false });
  }

  function applySpeech(payload) {
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
      orb.setSpeaking({
        active: true,
        durationMs,
        elapsedMs,
        levels: payload.levels,
      });
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
    clearSpeechOverlay();
  }

  function handleEvent(payload) {
    switch (payload.event) {
      case "session":
        setConnected(Boolean(payload.alive), payload.alive ? "LINKED" : "OFFLINE");
        if (!payload.alive) {
          clearSpeechOverlay();
          setState("offline", payload.message);
        }
        updateComposer();
        break;
      case "state":
        setState(payload.state, payload.label);
        break;
      case "input_request":
        acceptingInput = true;
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
        acceptingInput = false;
        addMessage("user", payload.message, payload.timestamp);
        updateComposer();
        break;
      case "assistant":
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
      default:
        break;
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
    eventSource.onerror = () => {
      setConnected(false, "RELINKING");
      updateComposer();
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
    submitDirective();
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
  elements.closeTerminal.addEventListener("click", () => toggleTerminal(false));

  elements.copyTerminal.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(terminalText);
      toast("Terminal transcript copied.");
    } catch {
      toast("Clipboard access was unavailable.", "error");
    }
  });

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

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (!elements.prompt.disabled) elements.prompt.focus();
    } else if (event.key === "Escape" && elements.terminalDrawer.classList.contains("open")) {
      toggleTerminal(false);
    }
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
  }, 1000);
  elements.clock.textContent = nowTime();

  class EnergyCore {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d", { alpha: true });
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
      this.current = {
        speed: 0.2,
        deform: 0.25,
        energy: 0.28,
        color: [255, 157, 61],
        pulse: 0.3,
      };
      this.target = { ...this.current, color: [...this.current.color] };
      this.nodes = [];
      this.links = [];
      this.sparks = [];
      this.profiles = {
        booting:       { speed: 0.18, deform: 0.18, energy: 0.42, pulse: 0.38, color: [255, 139, 48] },
        listening:     { speed: 0.25, deform: 0.3, energy: 0.94, pulse: 1.15, color: [255, 160, 58] },
        working:       { speed: 0.54, deform: 0.5, energy: 0.79, pulse: 0.72, color: [255, 157, 61] },
        planning:      { speed: 0.72, deform: 0.42, energy: 0.74, pulse: 0.82, color: [255, 168, 65] },
        perceiving:    { speed: 0.42, deform: 0.23, energy: 0.72, pulse: 0.48, color: [83, 184, 255] },
        thinking:      { speed: 1.05, deform: 0.82, energy: 1.0, pulse: 1.05, color: [255, 151, 43] },
        acting:        { speed: 1.45, deform: 0.58, energy: 1.0, pulse: 1.4, color: [255, 102, 39] },
        verifying:     { speed: 0.52, deform: 0.16, energy: 0.82, pulse: 0.56, color: [255, 189, 84] },
        transcribing:  { speed: 0.63, deform: 0.68, energy: 0.78, pulse: 1.3, color: [255, 171, 71] },
        responding:    { speed: 0.38, deform: 0.37, energy: 0.78, pulse: 0.96, color: [255, 202, 115] },
        success:       { speed: 0.22, deform: 0.16, energy: 1.0, pulse: 1.55, color: [189, 246, 120] },
        warning:       { speed: 0.58, deform: 0.7, energy: 0.84, pulse: 0.92, color: [255, 190, 60] },
        error:         { speed: 0.3, deform: 0.88, energy: 0.68, pulse: 0.6, color: [255, 78, 69] },
        offline:       { speed: 0.04, deform: 0.08, energy: 0.08, pulse: 0.1, color: [164, 57, 47] },
      };
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(canvas.parentElement);
      this.seedGeometry();
      this.bind();
      this.schedule();
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
      const rect = this.canvas.getBoundingClientRect();
      this.dpr = Math.min(window.devicePixelRatio || 1, 1.55);
      this.width = Math.max(1, rect.width);
      this.height = Math.max(1, rect.height);
      this.canvas.width = Math.round(this.width * this.dpr);
      this.canvas.height = Math.round(this.height * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
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
      if (!this.profiles[name]) name = "working";
      this.state = name;
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

    setSpeaking(payload) {
      this.speaking = Boolean(payload?.active);
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
      this.pointer.x = this.mix(this.pointer.x, this.pointer.tx, 0.03 * dt);
      this.pointer.y = this.mix(this.pointer.y, this.pointer.ty, 0.03 * dt);
    }

    color(alpha, multiplier = 1) {
      const [r, g, b] = this.current.color.map((value) =>
        Math.max(0, Math.min(255, Math.round(value * multiplier))));
      return `rgba(${r},${g},${b},${alpha})`;
    }

    draw(time) {
      const ctx = this.ctx;
      const t = (time - this.startTime) / 1000;
      const { energy, speed, deform, pulse } = this.current;
      ctx.clearRect(0, 0, this.width, this.height);
      ctx.save();
      ctx.translate(this.pointer.x, this.pointer.y);

      this.drawOuterHud(ctx, t, energy, speed);
      this.drawPulseRings(ctx, t, energy, pulse);
      const projected = this.projectNodes(t, deform, speed);
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
      if (voice < 0.008) return;

      const compact = Math.min(this.width, this.height) < 520;
      const count = compact ? 32 : 60;
      const baseRadius = this.radius * (compact ? 0.39 : 0.4);
      const maxExcursion = this.radius * (compact ? 0.075 : 0.105);
      const overall = 0.24 + this.speechLevel * 0.76;

      ctx.save();
      ctx.translate(this.cx, this.cy);
      ctx.globalCompositeOperation = "lighter";
      ctx.lineCap = "round";
      ctx.shadowColor = `rgba(255,154,48,${0.62 * voice})`;
      ctx.shadowBlur = compact ? 6 : 10;

      for (let index = 0; index < count; index += 1) {
        const angle = (index / count) * Math.PI * 2 - Math.PI / 2;
        const offset = (index - count / 2) * 0.13;
        const sample = this.speechSample(t, offset);
        const harmonic =
          0.52 +
          Math.abs(Math.sin(index * 0.71 - t * 8.8)) * 0.34 +
          Math.abs(Math.sin(index * 1.83 + t * 4.2)) * 0.14;
        const level = Math.min(
          1,
          (0.22 + sample * 0.78) * harmonic * overall,
        );
        const bar = maxExcursion * (0.18 + level * 0.82) * voice;
        const inner = baseRadius - bar * 0.16;
        const outer = baseRadius + bar;
        ctx.beginPath();
        ctx.moveTo(Math.cos(angle) * inner, Math.sin(angle) * inner);
        ctx.lineTo(Math.cos(angle) * outer, Math.sin(angle) * outer);
        const green = Math.round(160 + level * 76);
        const blue = Math.round(54 + level * 96);
        ctx.strokeStyle = `rgba(255,${green},${blue},${
          (0.24 + level * 0.58) * voice * (0.74 + energy * 0.26)
        })`;
        ctx.lineWidth = compact ? 0.85 + level * 0.4 : 1.05 + level * 0.72;
        ctx.stroke();
      }

      for (let layer = 0; layer < 2; layer += 1) {
        ctx.beginPath();
        for (let index = 0; index <= count; index += 1) {
          const wrapped = index % count;
          const angle = (index / count) * Math.PI * 2 - Math.PI / 2;
          const sample = this.speechSample(
            t,
            (wrapped - count / 2) * 0.12,
          );
          const carrier = Math.sin(
            angle * (layer ? 9 : 6) - t * (layer ? 7.2 : 9.6) + layer * 1.7,
          );
          const ripple = Math.sin(angle * 15 + t * 3.4 + layer) * 0.12;
          const signal = (0.2 + sample * 0.8) * (0.62 + Math.abs(carrier) * 0.38);
          const radius = baseRadius + maxExcursion * voice * (
            0.08 + signal * (layer ? 0.58 : 0.76) + ripple
          );
          const x = Math.cos(angle) * radius;
          const y = Math.sin(angle) * radius;
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.strokeStyle = layer
          ? `rgba(255,167,64,${0.26 * voice * overall})`
          : `rgba(255,222,151,${0.58 * voice * overall})`;
        ctx.lineWidth = layer ? 0.8 : compact ? 1.15 : 1.55;
        ctx.stroke();
      }

      const ribbonWidth = this.radius * (compact ? 0.92 : 1.18);
      const ribbonSegments = compact ? 42 : 64;
      ctx.rotate(-0.045);
      for (let layer = 0; layer < 2; layer += 1) {
        ctx.beginPath();
        for (let index = 0; index <= ribbonSegments; index += 1) {
          const progress = index / ribbonSegments;
          const x = (progress - 0.5) * ribbonWidth;
          const taper = Math.sin(progress * Math.PI) ** 0.72;
          const sample = this.speechSample(
            t,
            (progress - 0.5) * this.speechEnvelope.length * 0.4,
          );
          const carrier = Math.sin(
            progress * Math.PI * (layer ? 13 : 9)
            - t * (layer ? 10.2 : 8.1)
            + layer * 1.4,
          );
          const secondary = Math.sin(
            progress * Math.PI * 23 + t * 3.2 + layer,
          ) * 0.22;
          const amplitude = this.radius
            * (compact ? 0.055 : 0.072)
            * (0.28 + sample * 0.72)
            * voice
            * taper;
          const y = (carrier + secondary) * amplitude;
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = layer
          ? `rgba(255,143,42,${0.22 * voice * overall})`
          : `rgba(255,226,163,${0.52 * voice * overall})`;
        ctx.lineWidth = layer ? 0.72 : compact ? 0.95 : 1.25;
        ctx.stroke();
      }
      ctx.restore();
    }

    drawCore(ctx, t, energy, pulse, speechLevel = 0) {
      const beat = 1 + Math.sin(t * (1.1 + pulse)) * 0.035 * pulse;
      const voiceLift = 1 + this.speechMix * (0.012 + speechLevel * 0.028);
      const coreRadius = this.radius * 0.28 * beat * voiceLift;
      const activeEnergy = Math.min(1, energy + this.speechMix * 0.08);
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
      whiteCore.addColorStop(0, `rgba(255,245,218,${0.9 * activeEnergy})`);
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
  setState("booting");
  setConnected(false, "LINKING");
  updateComposer();
  connectEvents();
})();
