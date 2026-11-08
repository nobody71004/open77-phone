// open77_phone -- the page's behaviour.
//
// Lua (client/main.lua) tells this page what the phone is doing -- open or put away, asking, busy, the ticket to open
// Android's screen with -- and this page draws it and drives the screen: noVNC's RFB client over the web server's
// WebSocket, which lets a ticket through once. What the page sees goes back to Lua (and from there to the client's
// and the server's logs): every connection, every loss, and every minute a line of numbers. No ticket is ever logged.
(function () {
  "use strict";

  // ---- the bridge to Lua ------------------------------------------------------------------------------------------
  const bridge = window.Open77 || { on() {}, emit() {}, ready() {} };
  const emit = (event, payload) => { try { bridge.emit(event, payload || {}); } catch (e) { console.warn(e); } };
  const on = (event, fn) => bridge.on(event, (payload) => {
    try { fn(payload || {}); } catch (e) { log("warn", `${event}: ${e && e.message}`); }
  });

  let logBudget = 60, logWindow = Date.now();
  function log(level, text) {
    const now = Date.now();
    if (now - logWindow > 60000) { logWindow = now; logBudget = 60; }
    console[level === "warn" ? "warn" : "log"]("[phone] " + text);
    if (logBudget-- > 0) emit("phone:log", { level, text: String(text).slice(0, 400) });
  }

  const $ = (id) => document.getElementById(id);
  const body = document.body, dock = $("dock"), device = $("device"), card = $("card"), statusBox = document.querySelector(".status");

  // ---- what Lua said last ----------------------------------------------------------------------------------------
  const state = { open: false, phase: "closed", key: "F1", keyMode: "mapping", label: "Android", holding: false,
    prefs: { size: "standard", side: "right", dim: true, stats: false, pointer: "game" } };
  let openedAt = 0;

  // ---- sizes: the screen keeps Android's own shape ---------------------------------------------------------------
  const SIZES = { compact: 820, standard: 1000, large: 1200 };          // the long side of a sideways screen
  const UPRIGHT = { compact: 720, standard: 820, large: 920 };          // the long side of an upright one
  let fb = { w: 1280, h: 720 };
  function layout() {
    const portrait = fb.h > fb.w;
    const aspect = fb.w / fb.h;
    let sw, sh;
    if (portrait) { sh = UPRIGHT[state.prefs.size] || UPRIGHT.standard; sw = Math.round(sh * aspect); }
    else { sw = SIZES[state.prefs.size] || SIZES.standard; sh = Math.round(sw / aspect); }
    // never larger than the page leaves room for (the shelf under it, a margin round it)
    const maxW = window.innerWidth - 2 * 96 - 2 * 21, maxH = window.innerHeight - 150 - 2 * 21 - 40;
    const k = Math.min(1, maxW / sw, maxH / sh);
    sw = Math.floor(sw * k); sh = Math.floor(sh * k);
    device.style.setProperty("--sw", sw + "px");
    device.style.setProperty("--sh", sh + "px");
    device.classList.toggle("portrait", portrait);
    device.classList.toggle("landscape", !portrait);
    dock.classList.remove("side-left", "side-right", "side-center");
    dock.classList.add("side-" + (state.prefs.side || "right"));
    document.documentElement.style.setProperty("--scrim-x",
      state.prefs.side === "left" ? "28%" : state.prefs.side === "center" ? "50%" : "72%");
    body.classList.toggle("dim", !!state.prefs.dim);
    // Over Android's screen: the game's own arrow (instant), or none (when Android draws its own pointer, two
    // would show -- one late)
    body.classList.toggle("pointer-android", state.prefs.pointer === "android");
  }

  // ---- open and put away -----------------------------------------------------------------------------------------
  let leaveTimer = 0;
  function showDock(open) {
    clearTimeout(leaveTimer);
    if (open) {
      if (!dock.classList.contains("closed")) return;
      openedAt = Date.now();
      layout();
      dock.classList.add("entering");
      dock.classList.remove("closed", "leaving");
      body.classList.add("open");
      requestAnimationFrame(() => requestAnimationFrame(() => dock.classList.remove("entering")));
    } else {
      if (dock.classList.contains("closed")) return;
      body.classList.remove("open");
      dock.classList.add("leaving");
      closeSettings();
      leaveTimer = setTimeout(() => { dock.classList.add("closed"); dock.classList.remove("leaving"); }, 260);
    }
  }
  function putAway(why) {
    emit("phone:close", { why });
  }

  // ---- the card on the screen when it is not showing Android -----------------------------------------------------
  const ICONS = {
    phone: '<svg viewBox="0 0 24 24"><rect x="7" y="2.5" width="10" height="19" rx="2.6"/><path d="M10.5 18.6h3"/></svg>',
    lock: '<svg viewBox="0 0 24 24"><rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/><path d="M12 14.5v2.5"/></svg>',
    off: '<svg viewBox="0 0 24 24"><path d="M3 3l18 18"/><path d="M8.5 5.5A12 12 0 0 1 21 9"/><path d="M3 9a12 12 0 0 1 2.6-1.8"/><path d="M6.5 12.5a7 7 0 0 1 4-1.9"/><path d="M14 11a7 7 0 0 1 3.5 1.8"/><circle cx="12" cy="17" r="1.2"/></svg>',
    gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/></svg>',
    hand: '<svg viewBox="0 0 24 24"><path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V11M11 10.5V4a1.5 1.5 0 0 1 3 0v6.5M14 10.5V5.5a1.5 1.5 0 0 1 3 0V13"/><path d="M17 9.5a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-1a7 7 0 0 1-5.6-2.8L4 15.3a1.6 1.6 0 0 1 2.4-2.1L8 14.5"/></svg>',
  };
  function showCard(kind, title, text, opts) {
    opts = opts || {};
    card.hidden = false;
    card.className = "card " + kind;
    $("cardIcon").innerHTML = kind === "connecting" ? '<div class="spinner"></div>' : (ICONS[opts.icon] || ICONS.phone);
    $("cardTitle").textContent = title;
    $("cardText").textContent = text;
    const actions = $("cardActions");
    actions.hidden = !opts.again && !opts.away;
    $("cardAgain").hidden = !opts.again;
    $("cardAgain").textContent = opts.againText || "Try again";
    $("cardAway").hidden = !opts.away;
    const old = card.querySelector(".steps");
    if (old) old.remove();
    if (opts.step) {
      const steps = document.createElement("div");
      steps.className = "steps";
      for (let i = 1; i <= 3; i++) { const s = document.createElement("i"); if (i <= opts.step) s.className = "on"; steps.appendChild(s); }
      card.appendChild(steps);
    }
  }
  function hideCard() { card.hidden = true; }
  function setStatus(kind, text) {
    statusBox.className = "status " + kind;
    $("statusText").textContent = text;
    device.classList.toggle("live", kind === "live");
    const navOk = kind === "live";
    for (const id of ["navBack", "navHome", "volDownPill", "volUpPill"]) $(id).disabled = !navOk;
  }
  function since(ms) {
    const s = Math.max(0, Math.round((ms || 0) / 1000));
    if (s < 60) return "just now";
    const m = Math.round(s / 60);
    return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  }

  // ---- Android's screen ------------------------------------------------------------------------------------------
  // Bytes and messages through the WebSocket, counted for the numbers under the phone and the minute's log line.
  const counters = { bytes: 0, msgs: 0, flips: 0 };
  // A real WebSocket comes back (noVNC checks the channel's own prototype for send, close and the rest), with one
  // more listener on it.
  const NativeWS = window.WebSocket;
  function CountingWebSocket(url, protocols) {
    const ws = protocols === undefined ? new NativeWS(url) : new NativeWS(url, protocols);
    ws.addEventListener("message", (e) => {
      counters.msgs++;
      counters.bytes += (e.data && (e.data.byteLength || e.data.size || e.data.length)) || 0;
    });
    return ws;
  }
  CountingWebSocket.prototype = NativeWS.prototype;
  for (const k of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) CountingWebSocket[k] = NativeWS[k];
  window.WebSocket = CountingWebSocket;

  let rfb = null, live = false, attempt = 0, failures = 0, againTimer = 0, statsTimer = 0, lastInput = Date.now();
  let connectedAt = 0;

  function hostPath(url) {
    try { const u = new URL(url); return u.host + u.pathname; } catch (e) { return "?"; }
  }

  function disconnect(why) {
    clearTimeout(againTimer);
    clearInterval(statsTimer);
    $("stats").textContent = "";
    if (rfb) {
      const r = rfb;
      rfb = null;
      try { r.disconnect(); } catch (e) { /* already closed */ }
      log("info", `screen closed (${why})`);
    }
    live = false;
    $("vnc").innerHTML = "";
  }

  function connect(url, ticket) {
    disconnect("a new ticket");
    if (!window.NoVNC || !window.NoVNC.RFB) {
      showCard("error", "The phone cannot start", "Its screen's client did not load (vendor/novnc-rfb.js).", { icon: "off", away: true });
      setStatus("error", "No screen client");
      emit("phone:report", { status: "failed", detail: "noVNC did not load" });
      return;
    }
    if (!/^wss?:\/\//.test(url || "") || !/^[0-9a-f]{32}$/.test(ticket || "")) {
      showCard("error", "The phone cannot start", "The server's answer had no screen to open.", { icon: "off", again: true, away: true });
      setStatus("error", "Nothing to open");
      emit("phone:report", { status: "failed", detail: "no url or ticket in the server's answer" });
      return;
    }
    attempt++;
    const n = attempt;
    showCard("connecting", "Opening the screen", `Connecting to ${state.label}`, { step: 2 });
    setStatus("connecting", "Connecting");
    log("info", `connecting to ${hostPath(url)} (attempt ${n})`);
    const target = url + (url.includes("?") ? "&" : "?") + "t=" + ticket;
    let r;
    try {
      r = new window.NoVNC.RFB($("vnc"), target, { shared: true });
    } catch (e) {
      log("warn", `the screen client refused the address: ${e && e.message}`);
      emit("phone:report", { status: "failed", detail: "the client refused the address" });
      return;
    }
    rfb = r;
    r.scaleViewport = true;
    r.resizeSession = false;
    r.clipViewport = false;
    r.background = "#000";
    r.qualityLevel = 6;
    r.compressionLevel = 2;
    r.focusOnClick = true;
    r.showDotCursor = false;
    let opened = false;
    r.addEventListener("connect", () => {
      if (rfb !== r) return;
      opened = true; live = true; failures = 0; connectedAt = Date.now();
      hideCard();
      const canvas = $("vnc").querySelector("canvas");
      if (canvas && canvas.width && canvas.height) {
        const portraitNow = canvas.height > canvas.width, portraitBefore = fb.h > fb.w;
        fb = { w: canvas.width, h: canvas.height };
        if (portraitNow !== portraitBefore || Math.abs(fb.w / fb.h - 16 / 9) > 0.01) layout();
      }
      try { if (r._display && typeof r._display.flip === "function") {
        const flip = r._display.flip.bind(r._display);
        r._display.flip = function () { counters.flips++; return flip.apply(null, arguments); };
      } } catch (e) { /* the numbers are only numbers */ }
      setStatus("live", "Live");
      r.focus();
      log("info", `screen live: ${fb.w}x${fb.h}`);
      emit("phone:report", { status: "live", detail: `${fb.w}x${fb.h}, attempt ${n}` });
      startStats();
    });
    r.addEventListener("disconnect", (e) => {
      if (rfb !== r) return;
      rfb = null; live = false;
      clearInterval(statsTimer);
      $("stats").textContent = "";
      const clean = e && e.detail && e.detail.clean;
      if (!state.open) return;
      if (opened) {
        const lasted = Math.round((Date.now() - connectedAt) / 1000);
        log("warn", `screen lost after ${lasted} s (${clean ? "closed by the other end" : "the connection dropped"})`);
        emit("phone:report", { status: "lost", detail: `after ${lasted} s, ${clean ? "closed" : "dropped"}` });
        showCard("error", "The screen went away", "The connection to the phone dropped. Picking it up again…",
          { icon: "off", again: true, away: true, againText: "Reconnect" });
        setStatus("error", "Reconnecting");
        failures = 0;
        againTimer = setTimeout(() => askAgain("lost"), 1500);
      } else {
        failures++;
        log("warn", `the screen refused or could not be reached (attempt ${n}, ${failures} in a row)`);
        emit("phone:report", { status: "failed", detail: `refused or unreachable, ${failures} in a row` });
        if (failures < 3) {
          showCard("connecting", "Trying again", "The screen did not answer; asking for a new ticket", { step: 1 });
          setStatus("connecting", "Trying again");
          againTimer = setTimeout(() => askAgain("refused"), 1200 * failures);
        } else {
          showCard("error", "Can't reach the phone",
            "The phone's screen did not answer three times. The server may be restarting it; try again in a moment.",
            { icon: "off", again: true, away: true });
          setStatus("error", "Not reachable");
        }
      }
    });
    r.addEventListener("credentialsrequired", () => {
      log("warn", "the screen asks for a password; the phone has none to give");
      emit("phone:report", { status: "failed", detail: "the VNC server asked for a password" });
      disconnect("a password was asked for");
      showCard("error", "The phone is locked", "Its screen asks for a password, which the phone does not have.", { icon: "lock", away: true });
      setStatus("error", "Locked");
    });
    r.addEventListener("securityfailure", (e) => {
      log("warn", `the screen's security failed: ${e && e.detail && e.detail.reason}`);
    });
  }

  function askAgain(why) {
    if (!state.open) return;
    log("info", `asking for a new ticket (${why})`);
    showCard("connecting", "Picking up the phone", "Asking the server", { step: 1 });
    setStatus("connecting", "Asking");
    emit("phone:again", { why });
  }

  function startStats() {
    clearInterval(statsTimer);
    let last = { t: Date.now(), bytes: counters.bytes, flips: counters.flips };
    statsTimer = setInterval(() => {
      const now = Date.now(), dt = (now - last.t) / 1000 || 1;
      const kbps = Math.round(((counters.bytes - last.bytes) * 8) / 1000 / dt);
      const ups = Math.round((counters.flips - last.flips) / dt);
      last = { t: now, bytes: counters.bytes, flips: counters.flips };
      $("stats").textContent = state.prefs.stats && live ? `${fb.w}×${fb.h} · ${ups} fps · ${(kbps / 1000).toFixed(1)} Mbit/s` : "";
      emit("phone:stats", { w: fb.w, h: fb.h, kbps, ups, idleMs: now - lastInput });
    }, 5000);
  }

  // Android's own keys, sent as keys: Back and Home, and the volume.
  const KEYSYM = { back: 0x1008ff26, home: 0x1008ff18, volup: 0x1008ff13, voldown: 0x1008ff11 };
  function androidKey(name) {
    lastInput = Date.now();
    if (!rfb || !live) return;
    rfb.sendKey(KEYSYM[name], null);
    log("info", `sent ${name}`);
    rfb.focus();
  }

  // ---- the keys this page keeps for itself: the phone key and Escape ----------------------------------------------
  const PUNCT = new Set("+[]{};:,./\\`~!?@#$%^*<>|ù²&é\"'(-è_çà)=".split(""));
  const NAMED = { " ": "SPACE", Enter: "ENTER", Tab: "TAB", Shift: "SHIFT", Control: "CTRL", Alt: "ALT", CapsLock: "CAPSLOCK",
    Backspace: "BACKSPACE", Insert: "INSERT", Delete: "DELETE", Home: "HOME", End: "END", PageUp: "PAGEUP",
    PageDown: "PAGEDOWN", ArrowUp: "UP", ArrowDown: "DOWN", ArrowLeft: "LEFT", ArrowRight: "RIGHT" };
  const NUMPAD = { NumpadAdd: "NUMPADPLUS", NumpadSubtract: "NUMPADMINUS", NumpadMultiply: "NUMPADMULTIPLY",
    NumpadDivide: "NUMPADDIVIDE", NumpadDecimal: "NUMPADDECIMAL" };
  // The game's key registry's name for a key press (keybindings: logical keys on the player's own layout).
  function registryKey(e) {
    const code = e.code || "", key = e.key || "";
    if (/^Numpad[0-9]$/.test(code)) return "NUMPAD" + code.slice(6);
    if (NUMPAD[code]) return NUMPAD[code];
    if (/^F([1-9]|1[0-2])$/.test(key)) return key;
    if (NAMED[key]) return NAMED[key];
    if (key.length === 1) {
      if (/[a-z0-9]/i.test(key)) return key.toUpperCase();
      if (PUNCT.has(key)) return key;
    }
    return null;
  }
  window.registryKeyForTest = registryKey;

  let capturing = false;
  const pressedHere = {};          // the phone key / Escape pressed while this page had the keyboard (see keyup below)
  window.addEventListener("keydown", (e) => {
    lastInput = Date.now();
    if (!state.open) return;
    if (capturing) {
      e.preventDefault(); e.stopImmediatePropagation();
      if (e.key === "Escape") { stopCapture("Cancelled"); return; }
      const name = registryKey(e);
      if (!name) { feedback("bad", `${e.key} cannot be the phone key`); return; }
      stopCapture(`Asking the game for ${name}…`);
      emit("phone:rebind", { key: name });
      return;
    }
    if (isOurs(e)) {
      e.preventDefault(); e.stopImmediatePropagation();
      if (e.repeat || Date.now() - openedAt < 400) return;            // the press that opened it, still held
      pressedHere[e.code || e.key] = true;
    }
  }, true);
  // The phone key and Escape act when they are LET GO, and only if this page saw them pressed. Put away on the
  // press, the game got the keyboard back while the key was still down and its own key binding opened the phone
  // again 20 ms later (Staging, 2026-11-07: F1 could not close it); and the release of the press that opened the
  // phone, which arrives here, must not close it.
  function isOurs(e) {
    const name = registryKey(e);
    return e.key === "Escape" || (!!name && name.toUpperCase() === String(state.key || "").toUpperCase());
  }
  window.addEventListener("keyup", (e) => {
    if (!state.open || capturing || !isOurs(e)) return;
    e.preventDefault(); e.stopImmediatePropagation();                // never on to Android either
    const id = e.code || e.key;
    if (!pressedHere[id]) return;
    delete pressedHere[id];
    if (e.key === "Escape" && $("settings").classList.contains("open")) { closeSettings(); return; }
    putAway(e.key === "Escape" ? "escape" : "key");
  }, true);
  for (const type of ["pointerdown", "wheel"]) window.addEventListener(type, () => { lastInput = Date.now(); }, true);
  let moveSeen = 0;
  window.addEventListener("pointermove", () => { const n = Date.now(); if (n - moveSeen > 1000) { moveSeen = n; lastInput = n; } }, true);
  // A paste on the page goes to Android's clipboard (Ctrl+V on the screen then pastes it there).
  window.addEventListener("paste", (e) => {
    if (!rfb || !live) return;
    const text = e.clipboardData && e.clipboardData.getData("text/plain");
    if (text) { rfb.clipboardPasteFrom(text); log("info", `pasted ${text.length} characters to the phone's clipboard`); }
  });

  // ---- settings ----------------------------------------------------------------------------------------------------
  function feedback(kind, text) { const f = $("keyFeedback"); f.className = "feedback " + (kind || ""); f.textContent = text || ""; }
  function openSettings(capture) {
    $("settings").classList.add("open");
    $("settings").setAttribute("aria-hidden", "false");
    renderSettings();
    if (capture) startCapture();
  }
  function closeSettings() {
    stopCapture();
    $("settings").classList.remove("open");
    $("settings").setAttribute("aria-hidden", "true");
    if (rfb && live) rfb.focus();
  }
  function startCapture() {
    capturing = true;
    $("keyCap").textContent = "…";
    $("keyCap").classList.add("listening");
    feedback("", "Press the key the phone should open with (Esc cancels)");
  }
  function stopCapture(text) {
    if (!capturing) return;
    capturing = false;
    $("keyCap").classList.remove("listening");
    $("keyCap").textContent = state.key || "F1";
    if (text) feedback("", text);
  }
  function renderSettings() {
    $("keyCap").textContent = capturing ? "…" : (state.key || "F1");
    for (const b of $("segSize").children) b.classList.toggle("on", b.dataset.v === state.prefs.size);
    for (const b of $("segSide").children) b.classList.toggle("on", b.dataset.v === state.prefs.side);
    $("swDim").setAttribute("aria-checked", String(!!state.prefs.dim));
    $("swStats").setAttribute("aria-checked", String(!!state.prefs.stats));
    for (const b of $("segPointer").children) b.classList.toggle("on", b.dataset.v === (state.prefs.pointer || "game"));
    const noMove = state.keyMode === "none";
    $("keyChange").disabled = noMove; $("keyReset").disabled = noMove;
    $("keyNote").textContent = noMove ? "This game cannot register a key: /phone opens the phone" : "Opens and puts away the phone";
  }
  function setPref(name, value) {
    state.prefs = Object.assign({}, state.prefs, { [name]: value });
    renderSettings();
    layout();
    if (!(state.prefs.stats && live)) $("stats").textContent = "";
    emit("phone:prefs", state.prefs);
  }

  // ---- the controls ------------------------------------------------------------------------------------------------
  $("navBack").onclick = () => androidKey("back");
  $("navHome").onclick = () => androidKey("home");
  $("volUp").onclick = $("volUpPill").onclick = () => androidKey("volup");
  $("volDown").onclick = $("volDownPill").onclick = () => androidKey("voldown");
  $("power").onclick = $("putAway").onclick = $("cardAway").onclick = () => putAway("button");
  $("cardAgain").onclick = () => { failures = 0; askAgain("button"); };
  $("openSettings").onclick = () => ($("settings").classList.contains("open") ? closeSettings() : openSettings(false));
  $("settingsClose").onclick = () => closeSettings();
  $("keyChange").onclick = () => startCapture();
  $("keyReset").onclick = () => { stopCapture(); feedback("", "Asking the game for the default key…"); emit("phone:resetKey", {}); };
  for (const b of $("segSize").children) b.onclick = () => setPref("size", b.dataset.v);
  for (const b of $("segSide").children) b.onclick = () => setPref("side", b.dataset.v);
  $("swDim").onclick = () => setPref("dim", !state.prefs.dim);
  $("swStats").onclick = () => setPref("stats", !state.prefs.stats);
  for (const b of $("segPointer").children) b.onclick = () => setPref("pointer", b.dataset.v);

  // ---- toast -------------------------------------------------------------------------------------------------------
  let toastTimer = 0;
  function toast(title, text) {
    $("toastTitle").textContent = title;
    $("toastBody").textContent = text;
    $("toast").classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $("toast").classList.remove("show"), 6500);
  }

  // ---- what Lua says -----------------------------------------------------------------------------------------------
  on("phone:state", (s) => {
    const wasOpen = state.open;
    Object.assign(state, s);
    if (s.prefs) state.prefs = Object.assign({ size: "standard", side: "right", dim: true, stats: false, pointer: "game" }, s.prefs);
    $("hintKey").textContent = state.key || "F1";
    renderSettings();
    layout();
    if (state.open !== wasOpen) for (const k of Object.keys(pressedHere)) delete pressedHere[k];
    if (state.open && !wasOpen) {
      $("toast").classList.remove("show");
      failures = 0;
      showDock(true);
      showCard("connecting", "Picking up the phone", "Asking the server", { step: 1 });
      setStatus("connecting", "Asking");
      log("info", "opened");
    } else if (!state.open && wasOpen) {
      disconnect("put away");
      showDock(false);
      log("info", "put away");
    }
  });
  on("phone:connect", (m) => {
    if (!state.open) return;
    if (m.label) state.label = m.label;
    connect(m.url, m.ticket);
  });
  on("phone:busy", (m) => {
    disconnect("busy");
    showCard("busy", "In use", `${m.holder || "Somebody"} has the phone (${since(m.heldMs)}). You will be put through when it is free.`,
      { icon: "hand", again: true, away: true, againText: "Ask again" });
    setStatus("busy", `In use by ${m.holder || "somebody"}`);
  });
  const REASONS = {
    not_configured: ["Not set up here", "This server has no phone screen yet. An admin can set it up."],
    no_answer: ["The server did not answer", "Asked three times with no answer. Try again in a moment."],
  };
  on("phone:error", (m) => {
    disconnect("refused");
    const [title, text] = REASONS[m.reason] || ["The phone said no", `The server refused the phone (${m.reason || "no reason"}).`];
    showCard("error", title, text, { icon: m.reason === "not_configured" ? "gear" : (m.reason === "no_answer" ? "off" : "lock"),
      again: m.reason === "no_answer", away: true });
    setStatus("error", title);
  });
  const REVOKED = {
    idle: "You had not touched it for a while and somebody else was waiting.",
    quiet: "Your game stopped answering the server for a while.",
    lost: "The server no longer had you as the phone's holder.",
  };
  on("phone:revoked", (m) => {
    disconnect("taken back");
    showCard("error", "The phone was handed on", REVOKED[m.reason] || "The server took the phone back.",
      { icon: "hand", again: true, away: true, againText: "Pick it up again" });
    setStatus("error", "Handed on");
  });
  on("phone:free", (m) => {
    if (!state.open) toast("The phone is free", `Press ${m.key || state.key || "F1"} to pick it up`);
  });
  on("phone:keyResult", (m) => {
    if (m.key) state.key = m.key;
    $("hintKey").textContent = state.key;
    feedback(m.ok ? "ok" : "bad", m.text || "");
    renderSettings();
  });
  on("phone:settings", (m) => {
    openSettings(!!m.capture);
  });

  window.addEventListener("resize", () => layout());
  layout();
  // The game draws its own cursor while the page has the mouse: let it follow this page's CSS (a hand on the
  // buttons, none where the page asks for none). An older game has no Open77.cursor; its arrow is the cursor then.
  if (bridge.cursor && typeof bridge.cursor.useCss === "function") {
    Promise.resolve(bridge.cursor.useCss()).then((st) => log("info", `the game's cursor follows the page (${st && st.style})`),
      (e) => log("warn", `the game's cursor stays its arrow (${e})`));
  }
  log("info", `page loaded (${window.NoVNC ? "noVNC " + window.NoVNC.version : "NO noVNC"})`);
  emit("phone:ready", {});
  try { bridge.ready(); } catch (e) { /* an older bridge */ }
})();
