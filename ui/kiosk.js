// Kiosk client (M5): websocket events -> states, captions, orb. Spec: docs/ui-spec.md.
//
// The server owns sessions, turns, guards, the idle clock, and the kill switch. This page
// owns idle <-> listening, the attract greetings, visual timers, and reconnecting.
// Every bot word arrives from the server, already guarded; it is put on screen as text
// nodes only (never innerHTML), so nothing in a reply can become markup.
// Nothing is kept in browser storage. Reload = idle.
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const stage = $('stage'), input = $('input'), hint = $('hint'), caps = $('captions');
  const boot = JSON.parse(($('boot') || {}).textContent || '{}');

  // ---------------- settings from the server (boot now, hello on every connect) ----------------
  let S = boot.ui_strings || {};
  let greetings = [];
  let pingS = 10, warnBeforeS = 30;
  const SAFETY = new Set(['line:emergency', 'line:crisis_support', 'line:medical_legal']);
  const FALLBACK = 'line:guard_fallback';
  const media = matchMedia('(prefers-reduced-motion: reduce)');
  let forceReduced = false;
  const applyMotion = () => {
    const rm = forceReduced || media.matches;
    Orb.reduced(rm); stage.classList.toggle('reduced', rm);
  };
  media.addEventListener('change', applyMotion);

  function renderNotice(text) {   // "**AI** · ..." -> <b>AI</b> · ...
    const n = $('notice'); n.textContent = '';
    String(text || '').split(/(\*\*[^*]+\*\*)/).forEach(part => {
      if (!part) return;
      if (part.startsWith('**') && part.endsWith('**')) {
        const b = document.createElement('b'); b.textContent = part.slice(2, -2); n.appendChild(b);
      } else n.appendChild(document.createTextNode(part));
    });
  }
  renderNotice(S.notice);

  // ---------------- view state ----------------
  // Normal states: idle, listening, thinking, replying, ready, fallback.
  // Overlays (win over normal states): ooo (kill switch) > lost / reconnecting (connection
  // or Ollama down).
  let state = 'idle';
  let ooo = false, oooLine = '';
  let conn = 'connecting';          // connecting | ok | reconnecting | lost | replaced
  let backendOk = true;
  let sessionActive = false;        // a visitor has talked since the last reset
  let turnOpen = false, turnId = null, turnLines = [], turnSafety = false;
  let goodbyeShownAt = 0;
  const STATUS = {
    listening: 'status_listening', thinking: 'status_thinking',
    reconnecting: 'status_reconnecting', ooo: 'status_out_of_order', lost: 'status_connection_lost',
  };
  const PLACEHOLDER = { idle: 'placeholder_idle', ready: 'placeholder_ready', fallback: 'placeholder_fallback' };

  function setState(s, orb) {
    state = s;
    stage.className = 'st-' + s + (stage.classList.contains('reduced') ? ' reduced' : '');
    Orb.state(orb || s);
    $('status').textContent = STATUS[s] ? (S[STATUS[s]] || '') : '';
    input.placeholder = PLACEHOLDER[s] ? (S[PLACEHOLDER[s]] || '') : '';
    const off = s === 'reconnecting' || s === 'ooo' || s === 'lost';
    input.disabled = off;
    if (off) input.value = '';
    else focusInput();
  }
  const blocked = () => ooo || conn === 'lost' || conn === 'replaced' || conn === 'reconnecting' || !backendOk;

  // ---------------- captions ----------------
  const NUM_RE = /(\b911\b|\b988\b|\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b)/;
  function capNode(text, cls, bold) {
    const d = document.createElement('div');
    d.className = 'cap' + cls;
    if (!bold) { d.textContent = text; return d; }
    String(text).split(NUM_RE).forEach((part, i) => {   // odd parts are the numbers
      if (!part) return;
      if (i % 2) { const b = document.createElement('b'); b.textContent = part; d.appendChild(b); }
      else d.appendChild(document.createTextNode(part));
    });
    return d;
  }
  function showCaptions(lines, { muted = false, bold = false } = {}) {
    caps.className = 'captions' + (lines.length > 3 ? ' long' : '');
    const shown = lines.slice(-2);   // newest sentence + the one before it, dimmed
    caps.replaceChildren(...shown.map((s, i) =>
      capNode(s, (muted ? ' muted' : '') + (i < shown.length - 1 ? ' prev' : ''), bold)));
  }
  const clearCaptions = () => { caps.className = 'captions'; caps.replaceChildren(); };

  // ---------------- attract (idle greetings, rotating every 7 s) ----------------
  let attractTimer = null, lastGreeting = -1;
  function nextGreeting() {
    if (!greetings.length) return clearCaptions();
    let i = Math.floor(Math.random() * greetings.length);
    if (greetings.length > 1 && i === lastGreeting) i = (i + 1) % greetings.length;
    lastGreeting = i;
    showCaptions([greetings[i]], { muted: true });
  }
  function startAttract() { stopAttract(); nextGreeting(); attractTimer = setInterval(nextGreeting, 7000); }
  function stopAttract() { clearInterval(attractTimer); attractTimer = null; }

  // ---------------- "Still there?" ----------------
  let stillTimer = null;
  function showStill(seconds) {
    stopStill();
    let s = Math.max(1, Math.round(seconds));
    const draw = () => showCaptions([(S.still_there || '').replace('{{seconds}}', s)], { muted: true });
    draw();
    stillTimer = setInterval(() => { if (s > 1) { s--; draw(); } else stopStill(); }, 1000);
  }
  function stopStill() { if (stillTimer) { clearInterval(stillTimer); stillTimer = null; } }

  // ---------------- transitions ----------------
  let idleLater = null;
  function toIdle() {
    clearTimeout(idleLater); idleLater = null;
    stopStill(); turnOpen = false; turnId = null; turnLines = []; sessionActive = false;
    input.value = '';
    if (blocked()) return renderBlocked();
    clearCaptions(); setState('idle'); startAttract();
  }
  function toRest() {   // after listening is abandoned: idle, or ready mid-session
    if (sessionActive) { clearCaptions(); setState('ready'); } else toIdle();
  }
  function renderBlocked() {
    stopAttract(); stopStill();
    if (ooo) { setState('ooo'); showCaptions([oooLine], { muted: true }); return; }
    if (conn === 'lost' || conn === 'replaced' || !backendOk) {
      setState('lost'); showCaptions([S.connection_lost_line || ''], { muted: true }); return;
    }
    if (conn === 'reconnecting') { setState('reconnecting'); clearCaptions(); }
  }
  function unblock() { if (!blocked()) toIdle(); else renderBlocked(); }

  // ---------------- server events ----------------
  const handlers = {
    hello(e) {
      S = e.ui_strings || S; renderNotice(e.notice || S.notice);
      greetings = e.idle_greetings || [];
      pingS = e.ping_s || 10; warnBeforeS = e.warn_before_s || 30;
      forceReduced = !!e.reduced_motion; applyMotion();
      backendOk = e.backend_ok !== false;
      ooo = !!e.out_of_order; oooLine = e.out_of_order_line || oooLine;
      toIdle();
    },
    turn_start(e) {
      clearTimeout(idleLater); idleLater = null;
      stopAttract(); stopStill(); clearCaptions();
      turnOpen = true; turnId = e.turn_id; turnLines = []; turnSafety = false; sessionActive = true;
      if (!blocked()) setState('thinking');
    },
    sentence(e) {
      if (e.turn_id !== turnId || blocked()) return;
      const fb = e.source === FALLBACK;
      turnSafety = SAFETY.has(e.source);
      if (e.source === 'line:goodbye') goodbyeShownAt = performance.now();
      setState(fb ? 'fallback' : 'replying', turnSafety ? 'safety' : null);
      if (fb && turnLines.length === 0) Orb.shake();
      turnLines.push(e.text);
      showCaptions(turnLines, { bold: turnSafety });
      Orb.talk(e.text);
    },
    reply_retracted(e) { if (e.turn_id === turnId) { turnLines = []; clearCaptions(); } },
    action(e) { if (e.name === 'wave') Orb.kick(); },   // robot arm later (M7)
    turn_end(e) {
      if (e.turn_id !== turnId) return;
      turnOpen = false;
      if (blocked() || e.source === 'cancelled') return;
      if (e.source === FALLBACK) setState('fallback');
      else setState('ready', turnSafety ? 'safety' : null);
    },
    busy() { showHint(); },
    session_warning(e) {
      if (blocked() || turnOpen || state === 'listening') return;
      showStill(e.seconds_left || warnBeforeS);
    },
    session_reset() {
      sessionActive = false; stopStill();
      // The goodbye stays on screen as a normal reply for 4 s, then idle.
      const wait = goodbyeShownAt ? Math.max(0, 4000 - (performance.now() - goodbyeShownAt)) : 0;
      goodbyeShownAt = 0;
      clearTimeout(idleLater);
      idleLater = wait ? setTimeout(toIdle, wait) : null;
      if (!wait) toIdle();
    },
    out_of_order(e) {
      if (e.on) { ooo = true; oooLine = e.line || oooLine; turnOpen = false; renderBlocked(); }
      else { ooo = false; unblock(); }
    },
    backend(e) { backendOk = !!e.ok; if (backendOk) unblock(); else renderBlocked(); },
    ping(e) { lastPing = Date.now(); send({ type: 'pong', t: e.t }); },
  };

  // ---------------- websocket + reconnecting ----------------
  let ws = null, lastPing = 0, retryMs = 1000, downSince = 0, retryTimer = null, viewTimers = [];
  const send = obj => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj)); };

  function connect() {
    clearTimeout(retryTimer);
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    let sock;
    try { sock = new WebSocket(`${proto}://${location.host}/ws`); }
    catch (_) { return scheduleRetry(); }
    ws = sock;
    sock.onopen = () => {
      conn = 'ok'; retryMs = 1000; downSince = 0; lastPing = Date.now();
      viewTimers.forEach(clearTimeout); viewTimers = [];
    };
    sock.onmessage = ev => {
      let e; try { e = JSON.parse(ev.data); } catch (_) { return; }
      const h = handlers[e && e.type]; if (h) h(e);
    };
    sock.onclose = ev => {
      if (ws !== sock) return;
      ws = null; turnOpen = false;
      if (ev.code === 4001) { conn = 'replaced'; renderBlocked(); return; }   // a newer screen took over
      if (conn === 'ok') conn = 'connecting';   // shown as reconnecting after 3 s, lost after 30 s
      if (!downSince) {
        downSince = Date.now();
        viewTimers.push(setTimeout(() => { if (conn === 'connecting') { conn = 'reconnecting'; renderBlocked(); } }, 3000));
        viewTimers.push(setTimeout(() => { if (conn !== 'ok') { conn = 'lost'; renderBlocked(); } }, 30000));
      }
      scheduleRetry();
    };
  }
  function scheduleRetry() {
    retryTimer = setTimeout(connect, retryMs);
    retryMs = Math.min(5000, retryMs * 1.5);
  }
  // Two missed pings = the connection is gone even if the socket hasn't noticed.
  setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN && Date.now() - lastPing > 2.2 * pingS * 1000) ws.close();
  }, 1000);

  // ---------------- input ----------------
  function focusInput() { if (!input.disabled && document.activeElement !== input) input.focus(); }
  input.addEventListener('blur', () => setTimeout(focusInput, 0));
  document.addEventListener('pointerdown', () => setTimeout(focusInput, 0));

  let hintTimer = null;
  function showHint() {
    hint.textContent = S.hint_busy || '';
    hint.classList.add('on'); clearTimeout(hintTimer);
    hintTimer = setTimeout(() => hint.classList.remove('on'), 1800);
  }

  let lastTypingSent = 0, idleType = null, lastEsc = 0, msgSeq = 0;
  input.addEventListener('input', () => {
    if (blocked()) return;
    stopStill();
    const now = Date.now();
    if (now - lastTypingSent > 5000) { lastTypingSent = now; send({ type: 'typing' }); }
    if (!turnOpen && ['idle', 'ready', 'fallback'].includes(state)) {
      clearTimeout(idleLater); idleLater = null;
      stopAttract(); clearCaptions(); setState('listening');
    }
    if (state === 'listening') Orb.kick();   // M6: mic level instead
    clearTimeout(idleType);
    idleType = setTimeout(() => { if (state === 'listening' && !input.value) toRest(); }, 10000);
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      e.preventDefault();
      const now = Date.now(), immediate = now - lastEsc < 2000;
      lastEsc = now;
      send({ type: 'reset', immediate });
      input.value = '';
      if (immediate || !sessionActive) toIdle();
      return;
    }
    if (e.key !== 'Enter' || e.isComposing) return;
    e.preventDefault();
    const text = input.value.trim();
    if (!text || blocked()) return;
    if (turnOpen) { showHint(); return; }
    input.value = '';
    stopAttract(); stopStill(); clearCaptions();
    turnOpen = true; setState('thinking');   // the server's turn_start follows
    send({ type: 'message', text, client_msg_id: `m${++msgSeq}` });
  });

  // ---------------- fit the 1920x1080 stage to the screen ----------------
  function fit() {
    const s = Math.min(innerWidth / 1920, innerHeight / 1080);
    stage.style.transform = `translate(-50%, -50%) scale(${s})`;
  }
  addEventListener('resize', fit); fit();

  applyMotion();
  setState('idle');
  connect();
})();
