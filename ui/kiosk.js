// Kiosk client (M5a, light theme): websocket events -> view state, captions, the blob.
// Spec: docs/ui-spec.md v3. Events: docs/ui-spec.md sections 9 (v2, still used) and 10.
//
// The server owns sessions, turns, guards, the idle clock, idle-act timing, and the kill
// switch. This page owns the look: blob state, captions lighting up word by word, idle
// acts it was told to play, waking/listening on a tap, and reconnecting.
// Every bot word arrives from the server, already guarded; it is put on screen as text
// nodes only (never innerHTML), so nothing in a reply can become markup.
// Nothing is kept in browser storage. Reload = idle.
//
// Not built yet, hooks only:
//   M6 voice: `audio_level {source, level}` feeds the blob; `wake {source}` from the server
//             (wake word) does what a tap does; `mic {on}` drives the mic indicator.
//   M5b cards: `show_card`, `card_mini`, `hide_card` are accepted and ignored; the blob's
//             card pose is `blob.showing(true)` (try it in the debug panel).
import { createBlob, ACT_IDS } from './blob.js';
import { createCards } from './cards.js';

const $ = id => document.getElementById(id);
const stage = $('stage'), input = $('input'), hint = $('hint'), caps = $('captions');
const boot = JSON.parse(($('boot') || {}).textContent || '{}');
const DEBUG = new URLSearchParams(location.search).has('debug');

// Until M6 there is no microphone: a tap opens the staff typing box so the screen stays
// usable. M6 replaces this with the mic. Barge-in ("say stop") is M6c.
const TAP_OPENS_TYPING = true;
const BARGE_IN = false;
const WORD_MS = 300;          // caption pace without TTS word timings (spec 4: estimate)
const LISTEN_GIVE_UP_MS = 10000;
const GOODBYE_HOLD_MS = 2500;

let S = boot.ui_strings || {};
let pingS = 10, warnBeforeS = 30;
const SAFETY = new Set(['line:emergency', 'line:crisis_support', 'line:medical_legal']);
const CONFUSED = new Set(['line:guard_fallback', 'line:didnt_catch']);

const blob = createBlob($('blob'));
// Cards (M5b): drawn here, decided by the server (content/cards.yaml + check-in loop).
const cards = createCards(stage, { blob, send: o => send(o), strings: () => S });
const media = matchMedia('(prefers-reduced-motion: reduce)');
let forceReduced = false, reduced = false;
function applyMotion() {
  reduced = forceReduced || media.matches;
  blob.reduced(reduced);
  stage.classList.toggle('reduced', reduced);
  send({ type: 'screen', reduced_motion: reduced });
}
media.addEventListener('change', applyMotion);

// "**AI** · text" -> <b>AI</b> · text, as text nodes
function renderMarked(el, text) {
  el.textContent = '';
  String(text || '').split(/(\*\*[^*]+\*\*)/).forEach(part => {
    if (!part) return;
    if (part.startsWith('**') && part.endsWith('**')) {
      const b = document.createElement('b'); b.textContent = part.slice(2, -2); el.appendChild(b);
    } else el.appendChild(document.createTextNode(part));
  });
}
function renderCopy() {
  renderMarked($('notice'), S.notice);
  $('resttitle').textContent = S.rest_title || '';
  $('restbody').textContent = S.rest_body || '';
  renderTalk(); renderMic();
}

// ---------------------------------------------------------------- view state
// Normal: idle, waking, listening, thinking, speaking, ready (after a reply), confused.
// Overlays win: out (kill switch) > lost / reconnecting (server or Ollama unreachable).
let view = 'idle';
let ooo = false;
let conn = 'connecting';          // connecting | ok | reconnecting | lost | replaced
let backendOk = true;
let sessionActive = false;        // a visitor has talked since the last reset
let turnOpen = false, turnId = null, turnSafety = false;
let micOpen = false;              // only real audio capture (M6) turns the indicator on

const STATUS_WORD = { listening: 'status_listening', thinking: 'status_thinking',
  reconnecting: 'status_reconnecting', lost: 'status_connection_lost' };
const BLOB_STATE = { idle: 'idle', ready: 'idle', waking: 'waking', listening: 'listening',
  thinking: 'thinking', speaking: 'speaking', confused: 'confused', out: 'out', lost: 'lost',
  reconnecting: 'lost' };

function setView(v) {
  view = v;
  stage.classList.toggle('out', v === 'out');
  stage.classList.toggle('lost', v === 'lost' || v === 'reconnecting');
  blob.state(BLOB_STATE[v] || 'idle');
  $('statusword').textContent = STATUS_WORD[v] ? (S[STATUS_WORD[v]] || '') : '';
  if (v === 'out' || v === 'lost' || v === 'reconnecting') closeTyping();
  input.placeholder = (sessionActive ? S.placeholder_ready : S.placeholder_idle) || '';
  renderTalk();
  debugSync();
}
function renderTalk() {
  const busy = view === 'thinking' || view === 'speaking';
  const key = view === 'listening' || view === 'waking' ? 'talk_listening'
    : busy && BARGE_IN ? 'talk_interrupt' : 'talk_idle';
  renderMarked($('talktext'), S[key]);
  $('talk').classList.toggle('dim', busy && !BARGE_IN);
  $('talk').setAttribute('aria-label', (S[key] || '').replace(/\*\*/g, ''));
}
function renderMic() {
  $('mic').classList.toggle('on', micOpen);
  $('mictext').textContent = (micOpen ? S.mic_on : S.mic_off) || '';
}
const blocked = () => ooo || conn === 'lost' || conn === 'replaced' || conn === 'reconnecting' || !backendOk;

// ---------------------------------------------------------------- captions
// Sentences queue up and light word by word at speaking pace; the blob pulses meanwhile.
// With TTS (M6) the pace comes from the audio; until then it's an estimate.
const NUM_RE = /^\(?(911|988|\d{3}\)?[-.\s]?\d{3}[-.\s]\d{4})[).,!?;:]*$/;
let queue = [], speakingTimer = null, afterSpeech = null, ttsAt = 0;

function sentenceNode(text, { bold = false, muted = false } = {}) {
  const d = document.createElement('div');
  d.className = 'sent' + (muted ? ' muted' : '');
  String(text).split(/(\s+)/).forEach(tok => {
    if (!tok) return;
    if (/^\s+$/.test(tok)) { d.appendChild(document.createTextNode(' ')); return; }
    const w = document.createElement('span');
    w.className = 'w';
    if (bold && NUM_RE.test(tok)) { const b = document.createElement('b'); b.textContent = tok; w.appendChild(b); }
    else w.textContent = tok;
    d.appendChild(w);
  });
  return d;
}
function pushCaption(node) {
  caps.querySelectorAll('.sent').forEach(s => s.classList.add('old'));
  caps.appendChild(node);
  while (caps.children.length > 4) caps.removeChild(caps.firstChild);
}
function showStatic(text, opts = {}) {   // shown at once, fully lit (status-like lines)
  clearCaptions();
  const n = sentenceNode(text, { muted: true, ...opts });
  n.querySelectorAll('.w').forEach(w => w.classList.add('said'));
  pushCaption(n);
}
function clearCaptions() {
  queue = []; clearTimeout(speakingTimer); speakingTimer = null; blob.talking(false);
  caps.replaceChildren(); caps.classList.remove('long');
}
function enqueue(text, bold) {
  queue.push({ text, bold });
  if (!speakingTimer) speakNext();
}
function speakNext() {
  const item = queue.shift();
  if (!item) { speakingTimer = null; blob.talking(false); onSpeechDone(); return; }
  const node = sentenceNode(item.text, { bold: item.bold });
  pushCaption(node);
  const words = [...node.querySelectorAll('.w')];
  if (view !== 'confused') setView('speaking');
  blob.talking(performance.now() - ttsAt > 1000);   // steady stand-in swell until TTS (M6)
  let i = 0;
  const step = () => {
    if (i < words.length) {
      words[i].classList.add('said');
      if (reduced) { words.forEach(w => w.classList.add('said')); }
      i++;
      speakingTimer = setTimeout(step, WORD_MS + Math.min(120, words[i - 1].textContent.length * 8));
    } else speakingTimer = setTimeout(speakNext, 250);
  };
  step();
}
function onSpeechDone() {
  if (turnOpen || blocked()) return;
  if (view === 'speaking' || view === 'confused') setView('ready');
  if (afterSpeech) { const f = afterSpeech; afterSpeech = null; f(); }
}
const speaking = () => !!speakingTimer || queue.length > 0;

// ---------------------------------------------------------------- idle acts
let actTimers = [], debugHoldUntil = 0;
function playIdleAct(e, { forced = false } = {}) {
  if (!forced) {
    if (performance.now() < debugHoldUntil) return;   // the debug panel is driving the blob
    if (view !== 'idle' || sessionActive || blocked() || typingOpen || cards.current) return;
    if (reduced && e.reduced_motion === 'skip') return;
  }
  stopIdleAct();
  const dur = Math.max(1, +e.duration_s || 5);
  if (!blob.act(e.id, dur)) return;
  if (e.caption) {
    const c = $('idlecap');
    c.textContent = e.caption; c.classList.add('on');
    actTimers.push(setTimeout(() => c.classList.remove('on'), Math.max(4000, dur * 1000 - 600)));
  }
  if (e.id === 'dozing' && !reduced) {
    let n = 0;
    const zz = () => {
      if (n++ > 6 || blob.acting !== 'dozing') return;
      const z = document.createElement('div'); z.className = 'z'; z.textContent = 'z';
      z.style.left = '1090px'; z.style.top = '260px';
      stage.appendChild(z); actTimers.push(setTimeout(() => z.remove(), 3300)); actTimers.push(setTimeout(zz, 1100));
    };
    zz();
  }
  actTimers.push(setTimeout(stopIdleAct, dur * 1000 + 100));
  debugSync();
}
function stopIdleAct() {   // instant: any interaction, any turn, kill switch
  actTimers.forEach(clearTimeout); actTimers = [];
  if (blob.acting) blob.stopAct();
  $('idlecap').classList.remove('on');
  stage.querySelectorAll('.z').forEach(z => z.remove());
}

// ---------------------------------------------------------------- "Still there?"
let stillTimer = null;
function showStill(seconds) {
  stopStill();
  let s = Math.max(1, Math.round(seconds));
  const draw = () => showStatic((S.still_there || '').replace('{{seconds}}', s));
  draw();
  stillTimer = setInterval(() => { if (s > 1) { s--; draw(); } else stopStill(); }, 1000);
}
function stopStill() { if (stillTimer) { clearInterval(stillTimer); stillTimer = null; } }

// ---------------------------------------------------------------- transitions
let idleLater = null, wakeTimer = null, listenTimer = null;
function toIdle() {
  clearTimeout(idleLater); idleLater = null; afterSpeech = null;
  stopStill(); turnOpen = false; turnId = null; sessionActive = false;
  closeTyping();
  if (blocked()) return renderBlocked();
  clearCaptions(); setView('idle');
}
function toRest() {   // listening abandoned: idle, or ready mid-session
  if (sessionActive) { setView('ready'); } else toIdle();
}
function renderBlocked() {
  stopIdleAct(); stopStill(); clearTimeout(wakeTimer); clearTimeout(listenTimer);
  if (ooo) { clearCaptions(); setView('out'); return; }
  if (conn === 'lost' || conn === 'replaced' || !backendOk) {
    setView('lost'); showStatic(S.connection_lost_line || ''); return;
  }
  if (conn === 'reconnecting') { clearCaptions(); setView('reconnecting'); }
}
function unblock() { if (!blocked()) toIdle(); else renderBlocked(); }

function wake(source, { tell = true } = {}) {
  stopIdleAct();
  if (blocked()) return;
  if (tell) send({ type: 'wake', source });
  if (turnOpen || speaking()) return;   // no barge-in yet (M6c)
  clearTimeout(idleLater); idleLater = null; stopStill();
  clearCaptions(); setView('waking');
  clearTimeout(wakeTimer);
  wakeTimer = setTimeout(() => { if (view === 'waking') startListening(); }, 500);
  if (TAP_OPENS_TYPING) openTyping();
}
function startListening() {
  setView('listening');
  clearTimeout(listenTimer);
  listenTimer = setTimeout(() => { if (view === 'listening' && !input.value) { closeTyping(); toRest(); } }, LISTEN_GIVE_UP_MS);
}

// ---------------------------------------------------------------- server events
const handlers = {
  hello(e) {
    S = e.ui_strings || S; renderCopy();
    pingS = e.ping_s || 10; warnBeforeS = e.warn_before_s || 30;
    forceReduced = !!e.reduced_motion; applyMotion();
    backendOk = e.backend_ok !== false;
    ooo = !!e.out_of_order;
    toIdle();
  },
  turn_start(e) {
    clearTimeout(idleLater); idleLater = null; afterSpeech = null;
    stopIdleAct(); stopStill(); clearCaptions(); clearTimeout(listenTimer);
    turnOpen = true; turnId = e.turn_id; turnSafety = false; sessionActive = true;
    if (!blocked()) setView('thinking');
  },
  sentence(e) {
    if (e.turn_id !== turnId || blocked()) return;
    turnSafety = SAFETY.has(e.source);
    if (CONFUSED.has(e.source) && !speaking() && caps.children.length === 0) {
      setView('confused');   // wobble once, then the line plays
    }
    enqueue(e.text, turnSafety);
  },
  reply_retracted(e) { if (e.turn_id === turnId) clearCaptions(); },
  action() {},   // `wave` on a visitor's first message: robot arm later (M7)
  turn_end(e) {
    if (e.turn_id !== turnId) return;
    turnOpen = false;
    if (blocked() || e.source === 'cancelled') return;
    if (!speaking()) onSpeechDone();
  },
  busy() { showHint(); },
  session_warning(e) {
    if (blocked() || turnOpen || speaking() || view === 'listening') return;
    showStill(e.seconds_left || warnBeforeS);
  },
  session_reset() {
    sessionActive = false; stopStill(); cards.hide();
    // The goodbye finishes on screen and stays a moment, then idle.
    clearTimeout(idleLater);
    const later = () => { idleLater = setTimeout(toIdle, GOODBYE_HOLD_MS); };
    if (speaking() || turnOpen) afterSpeech = later;
    else if (caps.children.length && view === 'ready') later();
    else toIdle();
  },
  out_of_order(e) {
    if (e.on) { cards.hide(); ooo = true; turnOpen = false; micOpen = false; renderMic(); renderBlocked(); }
    else { ooo = false; unblock(); }
  },
  backend(e) { backendOk = !!e.ok; if (backendOk) unblock(); else renderBlocked(); },
  ping(e) { lastPing = Date.now(); send({ type: 'pong', t: e.t }); },
  idle_act(e) { playIdleAct(e); },
  // ---- M6 hooks (the server doesn't send these yet) ----
  audio_level(e) {
    if (e.source === 'tts') { ttsAt = performance.now(); blob.talking(false); }
    if (e.source === 'mic' && view !== 'listening') return;
    blob.level(e.level, e.source);
  },
  wake(e) { wake(e.source || 'word', { tell: false }); },
  mic(e) { micOpen = !!e.on; renderMic(); },
  // ---- M5b cards: the server picks the card and runs the check-in loop ----
  show_card(e) { if (!blocked()) { stopIdleAct(); cards.show({ ...(e.data || {}), id: e.id }); } },
  card_state(e) { cards.state(e.id, e.state, e); },
  card_mini(e) { cards.mini(e.id); },
  hide_card(e) { cards.hide(e.id); },
};

// ---------------------------------------------------------------- websocket + reconnecting
let ws = null, lastPing = 0, retryMs = 1000, downSince = 0, retryTimer = null, viewTimers = [];
function send(obj) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj)); }

function connect() {
  clearTimeout(retryTimer);
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  let sock;
  try { sock = new WebSocket(`${proto}://${location.host}/ws`); } catch (_) { return scheduleRetry(); }
  ws = sock;
  sock.onopen = () => {
    conn = 'ok'; retryMs = 1000; downSince = 0; lastPing = Date.now();
    viewTimers.forEach(clearTimeout); viewTimers = [];
    send({ type: 'screen', reduced_motion: reduced });
  };
  sock.onmessage = ev => {
    let e; try { e = JSON.parse(ev.data); } catch (_) { return; }
    const h = handlers[e && e.type]; if (h) h(e);
  };
  sock.onclose = ev => {
    if (ws !== sock) return;
    ws = null; turnOpen = false;
    if (ev.code === 4001) { conn = 'replaced'; renderBlocked(); return; }   // a newer screen took over
    if (conn === 'ok') conn = 'connecting';   // reconnecting after 3 s, lost after 30 s
    if (!downSince) {
      downSince = Date.now();
      viewTimers.push(setTimeout(() => { if (conn === 'connecting') { conn = 'reconnecting'; renderBlocked(); } }, 3000));
      viewTimers.push(setTimeout(() => { if (conn !== 'ok') { conn = 'lost'; renderBlocked(); } }, 30000));
    }
    scheduleRetry();
  };
}
function scheduleRetry() { retryTimer = setTimeout(connect, retryMs); retryMs = Math.min(5000, retryMs * 1.5); }
setInterval(() => {   // two missed pings = gone, even if the socket hasn't noticed
  if (ws && ws.readyState === WebSocket.OPEN && Date.now() - lastPing > 2.2 * pingS * 1000) ws.close();
}, 1000);

// ---------------------------------------------------------------- tap + staff typing box
let typingOpen = false;
function openTyping() {
  if (blocked()) return;
  typingOpen = true; stage.classList.add('typing');
  input.placeholder = (sessionActive ? S.placeholder_ready : S.placeholder_idle) || '';
  setTimeout(() => input.focus(), 0);
}
function closeTyping() {
  typingOpen = false; stage.classList.remove('typing'); input.value = ''; input.blur();
}
$('talk').addEventListener('click', () => wake('tap'));
$('tap').addEventListener('click', () => wake('tap'));
document.addEventListener('pointerdown', () => stopIdleAct(), true);

let hintTimer = null;
function showHint() {
  hint.textContent = S.hint_busy || '';
  hint.classList.add('on'); clearTimeout(hintTimer);
  hintTimer = setTimeout(() => hint.classList.remove('on'), 1800);
}

document.addEventListener('keydown', e => {
  stopIdleAct();
  if (e.key === '`' && e.target !== input) {   // staff: open the typing box
    e.preventDefault();
    if (!blocked()) { if (typingOpen) closeTyping(); else openTyping(); }
  } else if (e.key === ' ' && e.target === document.body) {
    e.preventDefault(); wake('tap');
  }
});

let lastTypingSent = 0, lastEsc = 0, msgSeq = 0;
input.addEventListener('input', () => {
  if (blocked()) return;
  stopStill(); stopIdleAct();
  const now = Date.now();
  if (now - lastTypingSent > 5000) { lastTypingSent = now; send({ type: 'typing' }); }
  if (!turnOpen && !speaking() && ['idle', 'ready', 'confused', 'waking'].includes(view)) {
    clearTimeout(idleLater); idleLater = null; clearCaptions(); startListening();
  }
  if (view === 'listening') { blob.level(.3, 'mic'); startListening(); }   // M6: real mic level
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
  if (turnOpen || speaking()) { showHint(); return; }
  input.value = '';
  clearTimeout(listenTimer); stopStill(); clearCaptions();
  turnOpen = true; setView('thinking');   // the server's turn_start follows
  send({ type: 'message', text, client_msg_id: `m${++msgSeq}` });
});

// ---------------------------------------------------------------- fit 1920x1080 to the screen
function fit() {
  const s = Math.min(innerWidth / 1920, innerHeight / 1080);
  stage.style.transform = `translate(-50%, -50%) scale(${s})`;
  blob.resize(1920, 1080, (window.devicePixelRatio || 1) * s);
}
addEventListener('resize', fit);

// ---------------------------------------------------------------- staff debug panel (?debug=1)
let debugSync = () => {};
if (DEBUG) {
  const panel = document.createElement('div'); panel.id = 'debug';
  const row = (label, items) => {
    const r = document.createElement('div'); r.className = 'row';
    const l = document.createElement('span'); l.className = 'lbl'; l.textContent = label; r.appendChild(l);
    items.forEach(([text, fn, key]) => {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = text;
      if (key) b.dataset.key = key;
      b.addEventListener('click', ev => {
        ev.stopPropagation(); debugHoldUntil = performance.now() + 20000; fn(); debugSync();
      });
      r.appendChild(b);
    });
    panel.appendChild(r); return r;
  };
  const sample = "A transformer reads every word at once. Its big trick is called attention.";
  let showingOn = false, simMic = null;
  const force = v => { stopIdleAct(); clearTimeout(wakeTimer); clearTimeout(listenTimer); setView(v); };
  row('State', [
    ['Idle', () => { clearCaptions(); force('idle'); }, 's:idle'],
    ['Waking', () => force('waking'), 's:waking'],
    ['Listening', () => force('listening'), 's:listening'],
    ['Thinking', () => force('thinking'), 's:thinking'],
    ['Speaking', () => { clearCaptions(); enqueue(sample, false); }, 's:speaking'],
    ['Confused', () => { clearCaptions(); setView('confused'); enqueue("Sorry, I didn't catch that. Could you say it again?", false); }, 's:confused'],
    ['Card pose', () => { showingOn = !showingOn; blob.showing(showingOn); }, 'showing'],
    ['Out of order', () => { clearCaptions(); force('out'); }, 's:out'],
    ['Lost', () => { force('lost'); showStatic(S.connection_lost_line || ''); }, 's:lost'],
  ]);
  row('Act', ACT_IDS.map(id => [id.replace(/_/g, ' '), () => {
    force('idle');
    playIdleAct({ id, duration_s: id === 'juggling' || id === 'juggling_success' ? 9 : 7 }, { forced: true });
  }, 'a:' + id]));
  row('Audio', [
    ['Sim mic level', () => {
      if (simMic) { clearInterval(simMic); simMic = null; }
      else simMic = setInterval(() => blob.level(.3 + .3 * Math.abs(Math.sin(Date.now() / 380)), 'mic'), 100);
    }, 'simmic'],
    ['Mic indicator', () => { micOpen = !micOpen; renderMic(); }, 'mic'],
    ['Reduced motion', () => { forceReduced = !forceReduced; applyMotion(); }, 'reduced'],
  ]);
  const info = document.createElement('div'); info.className = 'row note'; panel.appendChild(info);
  document.body.appendChild(panel);
  debugSync = () => {
    panel.querySelectorAll('button').forEach(b => {
      const k = b.dataset.key || '';
      b.classList.toggle('on', k === 's:' + view || k === 'a:' + blob.acting
        || (k === 'showing' && showingOn) || (k === 'simmic' && !!simMic)
        || (k === 'mic' && micOpen) || (k === 'reduced' && reduced));
    });
  };
  setInterval(() => {
    info.textContent = `${blob.fps()} fps · ${blob.cpuMs()} ms/frame · view ${view} · blob ${blob.current}`
      + (blob.acting ? ` · act ${blob.acting}` : '') + ' · forced states are visual only';
    debugSync();
  }, 500);
  window.__ada = { blob, handlers, get view() { return view; } };
}

renderCopy();
fit();
applyMotion();
setView('idle');
connect();
