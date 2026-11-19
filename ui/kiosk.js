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
//   M5b cards: drawn by cards.js, decided by the server (app/cards.py); card lines Ada says
//             outside a reply arrive as `say {text, source}` and are captioned.
import { createBlob, ACT_IDS } from './blob.js';
import { createCards } from './cards.js';

const $ = id => document.getElementById(id);
const stage = $('stage'), input = $('input'), hint = $('hint'), caps = $('captions');
const boot = JSON.parse(($('boot') || {}).textContent || '{}');
const DEBUG = new URLSearchParams(location.search).has('debug');
// screen = typed MVP (no mic, no LISTENING word); spoken = voice (M6). DES54, D69.
let mode = boot.output_mode === 'spoken' ? 'spoken' : 'screen';
function applyMode() { stage.classList.toggle('mode-screen', mode === 'screen'); }
applyMode();

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
const cards = createCards(stage, { blob, send: o => send(o), strings: () => S, mode: () => mode });
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
  const word = v === 'listening' && mode === 'screen' ? null : STATUS_WORD[v];   // the typing box is the cue
  $('statusword').textContent = word ? (S[word] || '') : '';
  if (v === 'out' || v === 'lost' || v === 'reconnecting') closeTyping();
  input.placeholder = (sessionActive ? S.placeholder_ready : S.placeholder_idle) || '';
  renderTalk();
  debugSync();
}
function renderTalk() {
  const busy = view === 'thinking' || view === 'speaking';
  const idleKey = !voice.wake_word && S.talk_idle_tap ? 'talk_idle_tap' : 'talk_idle';   // no wake word yet
  const key = view === 'listening' || view === 'waking' ? 'talk_listening'
    : busy && BARGE_IN ? 'talk_interrupt' : idleKey;
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
  stopAudio();
  queue = []; clearTimeout(speakingTimer); speakingTimer = null; blob.talking(false);
  caps.replaceChildren(); caps.classList.remove('long');
}
function enqueue(text, bold) {
  queue.push({ text, bold });
  if (!speakingTimer) speakNext();
}
function speakNext() {
  const item = queue.shift();
  if (!item) { speakingTimer = null; blob.talking(false); if (!audioBusy()) onSpeechDone(); return; }
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
const speaking = () => !!speakingTimer || queue.length > 0 || audioBusy();

// ---------------------------------------------------------------- Ada's voice (M6a)
// The server sends one binary frame per spoken part (app/voice/speaker.py): a JSON header
// {ref, part, last, words, sr} + int16 PCM. A `sentence`/`say` with tts:true holds its
// caption until its audio starts; then its words light up over the real audio length.
// The blob pulses with the real output level. Nothing is stored; buffers are dropped
// after playing. Volume never goes above the server's cap (config tts_volume).
let voice = { tts: false, stt: false, volume: 0.8, constraints: {} };
const TTS_WAIT_MS = 8000;      // the next line's audio hasn't started by then: caption it silently
let actx = null, outGain = null, analyser = null, nextAt = 0, levelRaf = 0;
const pend = new Map();        // ref -> {text, bold, node, words, said, timer, started}
let sources = new Set();
function audioOut() {
  if (!actx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    actx = new AC();
    outGain = actx.createGain();
    analyser = actx.createAnalyser(); analyser.fftSize = 1024;
    outGain.connect(analyser); analyser.connect(actx.destination);
  }
  outGain.gain.value = Math.max(0, Math.min(1, +voice.volume || 0));
  if (actx.state === 'suspended') actx.resume().catch(() => {});
  return actx;
}
// Browsers only allow sound after a gesture: any tap or key unlocks it.
['pointerdown', 'keydown'].forEach(t => document.addEventListener(t, () => { if (voice.tts) audioOut(); }, true));

function audioBusy() { return sources.size > 0 || pend.size > 0; }
function holdCaption(ref, text, bold) {
  pend.set(ref, { text, bold, node: null, words: [], said: 0, started: false, timer: null });
  armWait();
}
// Only the oldest waiting line has a give-up timer: later lines queue behind its audio.
function armWait() {
  for (const [ref, it] of pend) {
    if (it.started) continue;
    if (!it.timer) it.timer = setTimeout(() => silentCaption(ref), TTS_WAIT_MS);
    return;
  }
}
function silentCaption(ref) {   // synthesis failed or too slow: captions still work
  const item = pend.get(ref);
  if (!item) return;
  clearTimeout(item.timer); pend.delete(ref);
  if (item.started === true) { item.words.forEach(w => w.classList.add('said')); armWait(); checkAudioDone(); return; }
  enqueue(item.text, item.bold);
  armWait();
}
function onAudioFrame(buf) {
  if (buf.byteLength < 8) return;
  const n = new DataView(buf).getUint32(0, true);
  let h; try { h = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, n))); } catch (_) { return; }
  const item = pend.get(h.ref);
  const ctx = audioOut();
  if (!item || !ctx || blocked()) return;   // flushed, retracted or blocked: drop it
  clearTimeout(item.timer); item.timer = null; item.started = item.started || 'queued';
  const pcm = new Int16Array(buf.slice(4 + n));
  if (!pcm.length) { if (h.last) silentCaption(h.ref); return; }
  const ab = ctx.createBuffer(1, pcm.length, h.sr || 24000);
  const ch = ab.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = ctx.createBufferSource(); src.buffer = ab; src.connect(outGain);
  const at = Math.max(ctx.currentTime + 0.04, nextAt);
  nextAt = at + ab.duration;
  src.start(at); sources.add(src);
  src.onended = () => {
    sources.delete(src);
    if (h.last && pend.get(h.ref) === item) {
      item.words.forEach(w => w.classList.add('said'));
      pend.delete(h.ref);
      armWait();
    }
    checkAudioDone();
  };
  const startIn = Math.max(0, (at - ctx.currentTime) * 1000);
  const durMs = ab.duration * 1000;
  setTimeout(() => {
    if (pend.get(h.ref) !== item) return;
    if (item.started !== true) {
      item.started = true;
      if (h.part === 0) send({ type: 'tts_playing', ref: h.ref });
      item.node = sentenceNode(item.text, { bold: item.bold });
      pushCaption(item.node);
      item.words = [...item.node.querySelectorAll('.w')];
      if (view !== 'confused') setView('speaking');
      blob.talking(false); startLevel();
    }
    const from = item.said, to = Math.min(item.words.length, h.last ? item.words.length : from + (h.words || 0));
    item.said = to;
    const span = to - from;
    for (let k = 0; k < span; k++) {
      const w = item.words[from + k];
      if (reduced) { w.classList.add('said'); continue; }
      setTimeout(() => w.classList.add('said'), (durMs * k) / Math.max(1, span));
    }
  }, startIn);
}
function startLevel() {
  if (levelRaf) return;
  const data = new Float32Array(analyser.fftSize);
  const loop = () => {
    if (!sources.size) { levelRaf = 0; return; }
    analyser.getFloatTimeDomainData(data);
    let sum = 0; for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length);
    ttsAt = performance.now();
    blob.level(Math.min(1, rms * 5), 'tts');
    levelRaf = requestAnimationFrame(loop);
  };
  levelRaf = requestAnimationFrame(loop);
}
function stopAudio() {
  pend.forEach(it => clearTimeout(it.timer)); pend.clear();
  sources.forEach(src => { src.onended = null; try { src.stop(); } catch (_) { /* not started */ } });
  sources.clear(); nextAt = 0;
  if (levelRaf) { cancelAnimationFrame(levelRaf); levelRaf = 0; }
}
function checkAudioDone() {
  if (audioBusy() || speakingTimer || queue.length) return;
  blob.talking(false); blob.level(0, 'tts');
  onSpeechDone();
}
// ---------------------------------------------------------------- Ada's ears (M6b, tap-to-talk)
// A tap (or space) opens the mic; the page streams 16 kHz int16 frames to the server, whose
// VAD decides when the visitor is done and sends `mic {on:false, reason}`. Half-duplex: the
// mic never opens while Ada thinks or talks. The indicator is on only while capturing, and
// the tracks are stopped after every question (no always-open mic). Nothing is recorded.
let mic = null, micWanted = false, lastHeard = '';
const useMic = () => mode === 'spoken' && !!voice.stt && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
async function startMic() {
  if (mic) return true;
  micWanted = true;
  let stream = null, ctx = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { ...(voice.constraints || {}), channelCount: 1 } });
    if (!micWanted) { stream.getTracks().forEach(t => t.stop()); return false; }
    let src;
    try {   // a 16 kHz context: the browser resamples the mic properly
      ctx = new AudioContext({ sampleRate: 16000 });
      await ctx.audioWorklet.addModule('mic-worklet.js');
      src = ctx.createMediaStreamSource(stream);
    } catch (_) {   // some browsers refuse a rate mismatch: the worklet resamples instead
      if (ctx) ctx.close().catch(() => {});
      ctx = new AudioContext();
      await ctx.audioWorklet.addModule('mic-worklet.js');
      src = ctx.createMediaStreamSource(stream);
    }
    if (!micWanted) throw new Error('cancelled');
    const node = new AudioWorkletNode(ctx, 'ada-mic');
    const sink = ctx.createGain(); sink.gain.value = 0;   // keeps the graph pulled; no monitoring
    send({ type: 'mic_start' });
    node.port.onmessage = ev => {
      if (!mic || mic.node !== node) return;
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(ev.data.pcm);
      if (view === 'listening') blob.level(Math.min(1, ev.data.rms * 8), 'mic');
    };
    src.connect(node); node.connect(sink); sink.connect(ctx.destination);
    mic = { stream, ctx, node };
    micOpen = true; renderMic();
    return true;
  } catch (err) {
    if (stream) stream.getTracks().forEach(t => t.stop());
    if (ctx) ctx.close().catch(() => {});
    if (micWanted) console.warn('mic unavailable:', err && err.name);
    return false;
  }
}
function stopMic(tellServer) {
  micWanted = false;
  if (!mic) return;
  const m = mic; mic = null;
  try { m.node.port.onmessage = null; m.node.disconnect(); } catch (_) { /* gone */ }
  m.stream.getTracks().forEach(t => t.stop());
  m.ctx.close().catch(() => {});
  micOpen = false; renderMic();
  if (tellServer) send({ type: 'mic_stop' });
}

function captionLine(e, bold) {   // a sentence or card line: spoken (tts) or paced captions
  if (e.tts && voice.tts && e.ref) { audioOut(); holdCaption(e.ref, e.text, bold); }
  else enqueue(e.text, bold);
}

// ---------------------------------------------------------------- idle acts
let actTimers = [], debugHoldUntil = 0, guess = null;
const GUESS_SHAPES = ['star', 'cube', 'heart'];
const CAPTION_AT = { peekaboo: .62 };   // caption appears this far into the act (fraction)
function idleCaption(text, ms) {
  const c = $('idlecap');
  c.textContent = text; c.classList.add('on');
  if (ms) actTimers.push(setTimeout(() => c.classList.remove('on'), ms));
}
function str(key, fallback, vars = {}) {
  return (S[key] || fallback).replace(/\{\{(\w+)\}\}/g, (m, k) => (k in vars ? vars[k] : m));
}
// "Loading something fun..." bar that counts to 99 % and gives up (idle act loading_bar)
function loadingBar(dur) {
  const bar = document.createElement('div'); bar.id = 'loadbar';
  const lbl = document.createElement('div'); lbl.className = 'lbl';
  const track = document.createElement('div'); track.className = 'track';
  const fill = document.createElement('i'); track.append(fill);
  bar.append(lbl, track); stage.appendChild(bar);
  const t0 = performance.now(), stallAt = dur * .62;
  let pct = 0;
  const tick = () => {
    if (!bar.isConnected) return;
    const el = (performance.now() - t0) / 1000;
    if (el < stallAt) pct = Math.min(99, Math.round(99 * Math.pow(el / stallAt, .7)));
    fill.style.transform = `scaleX(${pct / 100})`;
    lbl.textContent = el < stallAt + .9 ? `${str('idle_loading', 'Loading something fun…')} ${pct}%`
      : str('idle_loading_fail', '99%… nope. Never mind.');
    bar.classList.toggle('stuck', el >= stallAt + .9);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(() => bar.classList.add('on'));
  tick();
  actTimers.push(setTimeout(() => bar.classList.remove('on'), dur * 1000 - 700));
}
function playIdleAct(e, { forced = false } = {}) {
  if (!forced) {
    if (performance.now() < debugHoldUntil) return;   // the debug panel is driving the blob
    if (view !== 'idle' || sessionActive || blocked() || typingOpen || cards.current) return;
    if (reduced && e.reduced_motion === 'skip') return;
  }
  stopIdleAct();
  const dur = Math.max(1, +e.duration_s || 5);
  const opts = {};
  if (e.id === 'what_am_i') opts.shape = GUESS_SHAPES[Math.floor(Math.random() * GUESS_SHAPES.length)];
  if (!blob.act(e.id, dur, opts)) return;
  if (e.caption) {
    const at = (CAPTION_AT[e.id] || 0) * dur * 1000;
    const show = () => idleCaption(e.caption, Math.max(at ? 2200 : 4000, dur * 1000 - at - 600));
    if (at) actTimers.push(setTimeout(show, at)); else show();
  }
  if (e.id === 'loading_bar') loadingBar(dur);
  if (e.id === 'what_am_i') {
    guess = { shape: opts.shape, revealed: false };
    // nobody tapped: she gives the answer away herself near the end
    actTimers.push(setTimeout(() => revealGuess(false), dur * 1000 * .74));
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
function revealGuess(tapped) {
  if (!guess || guess.revealed || blob.acting !== 'what_am_i') return false;
  guess.revealed = true;
  blob.reveal(tapped ? 2.8 : 2.2);
  const shape = str(`guess_${guess.shape}`, guess.shape);
  idleCaption(tapped ? str('guess_reveal_tap', "It's a {{shape}}! Got a question for me?", { shape })
    : str('guess_reveal', 'It was a {{shape}}!', { shape }));
  return true;
}
// Ada's goodbye: a bow, a wave, a hop and sparkles when a conversation ends
function playGoodbye() {
  if (blocked() || reduced) return;
  stopIdleAct();
  blob.act('goodbye_wave', 3.4);
  actTimers.push(setTimeout(() => { if (blob.acting === 'goodbye_wave') blob.stopAct(); }, 3500));
}
function stopIdleAct() {   // instant: any interaction, any turn, kill switch
  actTimers.forEach(clearTimeout); actTimers = []; guess = null;
  const lb = $('loadbar'); if (lb) lb.remove();
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
  if (mic || micWanted) {   // tapped again while listening: "I'm done"
    const open = !!mic;
    stopMic(open);
    if (open && (view === 'listening' || view === 'waking')) setView('thinking');   // the server decides
    else if (!open) toRest();
    return;
  }
  if (tell) send({ type: 'wake', source });
  if (turnOpen || speaking()) return;   // half-duplex: no mic while Ada talks (barge-in = M6c)
  clearTimeout(idleLater); idleLater = null; stopStill();
  clearCaptions(); setView('waking');
  clearTimeout(wakeTimer);
  if (useMic()) {
    audioOut();   // this tap also unlocks Ada's voice
    startMic().then(ok => {
      if (ok) { if (view === 'waking') startListening(); else stopMic(true); return; }
      if (micWanted || view === 'waking') { micWanted = false; voice.stt = false; renderTalk(); openTyping(); startListening(); }
    });
    return;
  }
  wakeTimer = setTimeout(() => { if (view === 'waking') startListening(); }, 500);
  if (TAP_OPENS_TYPING) openTyping();
}
function startListening() {
  setView('listening');
  clearTimeout(listenTimer);
  if (mic) return;   // the server's VAD ends a spoken question (no speech -> mic closes)
  listenTimer = setTimeout(() => { if (view === 'listening' && !input.value) { closeTyping(); toRest(); } }, LISTEN_GIVE_UP_MS);
}

// ---------------------------------------------------------------- server events
const handlers = {
  hello(e) {
    S = e.ui_strings || S;
    if (e.output_mode) { mode = e.output_mode === 'spoken' ? 'spoken' : 'screen'; applyMode(); }
    voice = { ...voice, ...(e.voice || {}) };
    if (outGain) outGain.gain.value = Math.max(0, Math.min(1, +voice.volume || 0));
    renderCopy();
    pingS = e.ping_s || 10; warnBeforeS = e.warn_before_s || 30;
    forceReduced = !!e.reduced_motion; applyMotion();
    backendOk = e.backend_ok !== false;
    ooo = !!e.out_of_order;
    toIdle();
  },
  turn_start(e) {
    if (mic) stopMic(true);
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
    captionLine(e, turnSafety);
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
  session_reset(e) {
    sessionActive = false; stopStill(); cards.hide();
    if (e && (e.reason === 'farewell' || e.reason === 'timeout' || e.reason === 'visitor')) playGoodbye();
    // The goodbye finishes on screen and stays a moment, then idle.
    clearTimeout(idleLater);
    const later = () => { idleLater = setTimeout(toIdle, GOODBYE_HOLD_MS); };
    if (speaking() || turnOpen) afterSpeech = later;
    else if (caps.children.length && view === 'ready') later();
    else toIdle();
  },
  out_of_order(e) {
    if (e.on) { cards.hide(); stopMic(false); ooo = true; turnOpen = false; micOpen = false; renderMic(); renderBlocked(); }
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
  mic(e) {   // the server ends a capture: done talking, nothing said, busy, or no STT
    if (e.on) return;
    stopMic(false);
    if (e.reason === 'end') { if (view === 'listening' || view === 'waking') setView('thinking'); return; }
    if (e.reason === 'unavailable') { voice.stt = false; renderTalk(); openTyping(); return; }
    if (e.reason === 'busy') showHint();
    if (view === 'listening' || view === 'waking' || view === 'thinking') toRest();
  },
  heard(e) { lastHeard = e.text || ''; },   // staff debug panel only, never on the big screen
  // ---- M5b cards: the server picks the card and runs the check-in loop ----
  show_card(e) { if (!blocked()) { stopIdleAct(); cards.show({ ...(e.data || {}), id: e.id }); } },
  card_state(e) { cards.state(e.id, e.state, e); },
  // A card line Ada says outside a reply ("Did you scan it?", "Nice, see you in there!"):
  // captioned like any sentence (TTS speaks it later, M6a). Inside a reply it's a `sentence`.
  say(e) { if (!blocked() && e.text) { stopIdleAct(); captionLine(e, false); } },
  tts_skip(e) { silentCaption(e.ref); },
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
  sock.binaryType = 'arraybuffer';
  sock.onopen = () => {
    conn = 'ok'; retryMs = 1000; downSince = 0; lastPing = Date.now();
    viewTimers.forEach(clearTimeout); viewTimers = [];
    send({ type: 'screen', reduced_motion: reduced });
  };
  sock.onmessage = ev => {
    if (ev.data instanceof ArrayBuffer) { onAudioFrame(ev.data); return; }
    let e; try { e = JSON.parse(ev.data); } catch (_) { return; }
    const h = handlers[e && e.type]; if (h) h(e);
  };
  sock.onclose = ev => {
    if (ws !== sock) return;
    ws = null; turnOpen = false; stopMic(false);
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
$('tap').addEventListener('click', () => { if (!guessWake) wake('tap'); });
let guessWake = null;
document.addEventListener('pointerdown', ev => {
  // "What am I?": a tap on the screen reveals the answer, then Ada listens for a question
  const onButton = ev.target.closest && ev.target.closest('#talk, #typing, #debug, button, input');
  if (!onButton && guess && revealGuess(true)) {
    ev.stopPropagation();
    guessWake = setTimeout(() => { guessWake = null; wake('tap'); }, 2600);
    return;
  }
  if (guessWake) { clearTimeout(guessWake); guessWake = null; }
  stopIdleAct();
}, true);

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
      + (blob.acting ? ` · act ${blob.acting}` : '') + ' · forced states are visual only'
      + (lastHeard ? ` · heard: “${lastHeard}”` : '');
    debugSync();
  }, 500);
  window.__ada = { blob, handlers, get view() { return view; } };
}

renderCopy();
fit();
applyMotion();
setView('idle');
connect();
