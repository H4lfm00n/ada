// Driver for ui/mockups/ada.html (designer mockup, docs/ui-spec.md v3.1). Plays fake events
// through the REAL kiosk modules: ../blob.js (glass orb) and ../cards.js. No server.
// Copy below mirrors content/ui_strings.yaml v3 and content/cards.yaml; keep them in sync.
import { createBlob, ACT_IDS } from '../blob.js';
import { createCards } from '../cards.js';

const $ = id => document.getElementById(id);
const stage = $('stage'), caps = $('captions'), sw = $('statusword');
const blob = createBlob($('blob'));
let timers = [];
const later = (ms, fn) => timers.push(setTimeout(fn, ms));
const stopAll = () => { timers.forEach(clearTimeout); timers = []; blob.talking(false); };

// ------------------------------------------------------------ copy (ui_strings.yaml v3)
const NAME = 'Ada';
const STR = {
  screen: {
    notice: `<b>AI</b>${NAME} is an AI and can make mistakes · Chats are saved as text on this computer · No audio or video`,
    talk_idle: `<b>Tap here</b> to ask ${NAME} a question`,
    talk_listening: 'Type your question below',
  },
  spoken: {
    notice: `<b>AI</b>${NAME} is an AI and can make mistakes · Listens only after “Hey ${NAME}” or a tap · No audio recorded`,
    talk_idle: `Say <b>“Hey ${NAME}”</b> or tap to talk`,
    talk_listening: "I'm listening…",
  },
};
let mode = 'screen';
// Talk-pill icon: a mic implies voice, so screen mode uses a chat bubble (ui-spec 2).
const ICON = {
  screen: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4.6A8 8 0 1 1 21 12z"/><path d="M8.5 12h.01M12 12h.01M15.5 12h.01"/>',
  spoken: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
};

// ------------------------------------------------------------ cards (cards.yaml, MVP)
const AI_STUDENTS = 'https://ai.sfsu.edu/ai-students';   // placeholder URL approved by the PM for the MVP
const CARDS = {
  qr_project_proposal: { type: 'qr', title: 'Proposal form: coming soon', url: AI_STUDENTS,
    qr_caption: "For now, scan for SFSU's AI for Students page.",
    say_before: "The proposal form link is coming soon. For now, I've put a QR code on the screen for SFSU's AI for Students page.",
    confirm: 'Did you get it?', on_yes: 'Great! A lab member can tell you more about proposals, too. Anything else?',
    on_no: 'No rush, take your time.', auto_hide_s: 60 },
  qr_discord: { type: 'qr', title: 'Discord invite: coming soon', url: AI_STUDENTS,
    qr_caption: "For now, scan for SFSU's AI for Students page.",
    say_before: "Our Discord invite is coming soon. For now, I've put a QR code on the screen for SFSU's AI for Students page.",
    confirm: 'Did you scan it?', on_yes: 'Nice! Check back soon for the Discord invite. Anything else?',
    on_no: 'No rush, take your time.', auto_hide_s: 60 },
};

// ------------------------------------------------------------ idle acts (idle_acts.yaml v2)
const ACTS = {
  daydream_cube: [6], daydream_star: [6], juggling: [9], juggling_success: [8], dozing: [12],
  hello_wave: [4, `Hi! I'm ${NAME}.`], try_asking_proposal: [6, 'Try asking: "How do I submit a project?"'],
  try_asking_ai: [6, `Try asking: "What's a neural network?"`],
  try_asking_where: [6, 'Try asking: "What equipment do you have?"'],
  stretch: [4], look_around: [5], shy_shrink: [4], wireframe_wave: [5], spin_on_axis: [3],
  fans_spinning: [5, 'Just thinking about robots. My fans are spinning up.'],
  count_vertices: [6, 'Trying to count to a million. Lost count. Again.'],
};   // daydream_arm removed (PM 2026-11-18: no robot-arm teasing)

// ------------------------------------------------------------ screen plumbing
const cards = createCards(stage, { blob, send: o => o.type === 'card_reply' && reply(o.answer) });
function fit() {
  const vp = $('viewport').getBoundingClientRect();
  const s = Math.min(vp.width / 1920, vp.height / 1080);
  stage.style.transform = `translate(-50%, -50%) scale(${s})`;
  blob.resize(1920, 1080, (window.devicePixelRatio || 1) * s);
}
addEventListener('resize', fit); fit();

function applyMode(m) {
  mode = m;
  stage.classList.toggle('mode-screen', m === 'screen');
  $('notice').innerHTML = STR[m].notice;              // static strings from this file only
  $('talktext').innerHTML = STR[m].talk_idle;
  document.querySelector('#talk svg').innerHTML = ICON[m];   // static strings from this file only
  $('mictext').textContent = 'Mic off';
  $('resttitle').textContent = `${NAME} is resting`;
  $('restbody').textContent = 'Back soon. A lab member can help you in the meantime.';
  document.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('on', b.dataset.mode === m));
  reset();
}
function setWord(w) { sw.textContent = w; sw.classList.toggle('on', w === 'Listening'); }
function micOn(on) { $('mic').classList.toggle('on', on); $('mictext').textContent = on ? 'Listening' : 'Mic off'; }
function typing(on) {
  stage.classList.toggle('typing', on);
  if (on) { $('input').placeholder = 'Type a question, then press Enter'; $('input').focus(); }
}
function say(text, then) {
  blob.state('speaking'); blob.talking(true);
  const n = document.createElement('div'); n.className = 'sent'; n.textContent = text;
  caps.replaceChildren(n);
  later(Math.max(1800, text.split(' ').length * 300), () => { blob.talking(false); blob.state('idle'); then && then(); });
}
function visitor(text) {
  const n = document.createElement('div'); n.className = 'sent muted'; n.textContent = `Visitor: "${text}"`;
  caps.replaceChildren(n);
}
function stopIdle() { if (blob.acting) blob.stopAct(); $('idlecap').classList.remove('on'); }
function reset() {
  stopAll(); stopIdle(); cards.hide(); caps.replaceChildren(); typing(false); micOn(false); setWord('');
  stage.classList.remove('out', 'lost'); blob.state('idle'); flowCard = null;
}

// ------------------------------------------------------------ a visit
function start() {   // tap on the pill or the orb
  if (stage.classList.contains('out')) return;
  stopAll(); stopIdle(); caps.replaceChildren();
  blob.state('waking');
  later(550, () => {
    blob.state('listening');
    $('talktext').innerHTML = STR[mode].talk_listening;
    if (mode === 'screen') typing(true);
    else { micOn(true); setWord('Listening'); }
  });
}
function answer(q) {
  stopAll(); typing(false); micOn(false); setWord('Thinking'); blob.state('thinking'); visitor(q);
  $('talktext').innerHTML = STR[mode].talk_idle;
  const proposal = /propos|submit|project/i.test(q), discord = /discord|join/i.test(q);
  later(1300, () => {
    setWord('');
    if (proposal) return runCard('qr_project_proposal');
    if (discord) return runCard('qr_discord');
    say(mode === 'screen'
      ? 'General meetings are in LIB 260. For dates and times, email ai@sfsu.edu or ask a lab member.'
      : "Meetings are in LIB two-sixty, on the library's second floor. A lab member can tell you the next date.");
  });
}
$('talk').onclick = start; $('tap').onclick = start; $('blob').onclick = start;
$('input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.value.trim()) { const q = e.target.value.trim(); e.target.value = ''; answer(q); }
  if (e.key === 'Escape') reset();
});

// ------------------------------------------------------------ check-in loop (spec 2.2)
let flowCard = null, asked = 0;
function runCard(id, after) {
  const d = { ...CARDS[id], id };
  flowCard = d; asked = 0;
  cards.show(d); cards.state(id, 'intro');
  say(d.say_before, () => later(1500, () => ask(d)));
  if (after) after(d);
}
function ask(d) {
  if (flowCard !== d) return;
  say(d.confirm, () => {
    cards.state(d.id, 'asking');
    if (mode === 'spoken') { micOn(true); setWord('Listening'); blob.state('listening'); }
  });
}
function reply(ans) {
  if (!flowCard) return;
  const d = flowCard; stopAll(); micOn(false); setWord('');
  if (ans === 'yes') {
    visitor('Yes, got it');
    later(700, () => { cards.state(d.id, 'yes'); say(d.on_yes); flowCard = null; });
  } else {
    visitor('Not yet, one sec'); asked++;
    later(700, () => { say(d.on_no); cards.state(d.id, 'waiting', { seconds: 10 }); });
    later(asked > 1 ? 9000 : 7000, () => ask(d));
  }
}
const FLOWS = {
  yes() { reset(); runCard('qr_project_proposal'); later(14500, () => reply('yes')); },
  notyet() { reset(); runCard('qr_discord'); later(14500, () => reply('no')); later(14500 + 9500, () => reply('yes')); },
  else() {
    reset(); runCard('qr_project_proposal');
    later(5200, () => { stopAll(); visitor('Wait, what equipment do you have?'); cards.mini('qr_project_proposal'); blob.state('thinking');
      later(1100, () => say('Students can use robotic dogs, robotic arms, a 3D printer and robotics kits for projects. The QR code is still in the corner.')); });
  },
  timeout() { reset(); runCard('qr_discord');
    later(16000, () => { stopAll(); micOn(false); setWord(''); caps.replaceChildren(); cards.state('qr_discord', 'timeout'); blob.state('idle'); flowCard = null; }); },
};
document.querySelectorAll('[data-flow]').forEach(b => b.onclick = () => FLOWS[b.dataset.flow]());

// ------------------------------------------------------------ states panel
document.querySelectorAll('[data-s]').forEach(b => b.onclick = () => {
  reset(); const s = b.dataset.s;
  if (s === 'out') { stage.classList.add('out'); blob.state('out'); return; }
  if (s === 'lost') { stage.classList.add('lost'); blob.state('lost'); setWord('Connection lost');
    const n = document.createElement('div'); n.className = 'sent'; n.textContent = "I'm having trouble connecting. Please ask a lab member.";
    caps.replaceChildren(n); return; }
  if (s === 'listening') return start();
  if (s === 'thinking') { setWord('Thinking'); blob.state('thinking'); return; }
  if (s === 'speaking') return say("It's the design behind most modern language models, including me. Its big trick is called attention.");
  if (s === 'confused') { blob.state('confused'); return say('Oops, I fumbled that answer. Could you ask it a different way?'); }
  blob.state(s);
});

// ------------------------------------------------------------ idle acts
function playAct(id) {
  reset(); const [dur, cap] = ACTS[id];
  if (!blob.act(id, dur)) return;
  if (cap) { $('idlecap').textContent = cap; $('idlecap').classList.add('on'); later(Math.max(4000, dur * 1000 - 600), () => $('idlecap').classList.remove('on')); }
  later(dur * 1000 + 100, stopIdle);
}
for (const id of Object.keys(ACTS)) {
  if (!ACT_IDS.includes(id)) continue;
  const b = document.createElement('button'); b.textContent = id.replace(/_/g, ' '); b.onclick = () => playAct(id);
  $('acts').append(b);
}
let autoOn = false, lastAct = null, autoT = null;
$('auto').onclick = () => {
  autoOn = !autoOn; $('auto').classList.toggle('on', autoOn); clearTimeout(autoT);
  const tick = () => {
    if (!autoOn) return;
    const pool = Object.keys(ACTS).filter(a => a !== lastAct && a !== 'dozing');
    lastAct = pool[Math.floor(Math.random() * pool.length)]; playAct(lastAct);
    autoT = setTimeout(tick, 9000 + Math.random() * 5000);   // spec: 45–120 s; shortened for review
  };
  tick();
};

// ------------------------------------------------------------ demo + misc
$('demo').onclick = () => {
  reset();
  playAct('hello_wave');
  later(4200, start);
  later(5600, () => answer(mode === 'screen' ? 'how do I submit a project?' : 'hey ada how do I submit a project'));
  later(5600 + 1300 + 14500, () => reply('yes'));
};
$('rm').checked = matchMedia('(prefers-reduced-motion: reduce)').matches;
blob.reduced($('rm').checked);
$('rm').onchange = e => { blob.reduced(e.target.checked); stage.classList.toggle('reduced', e.target.checked); };
document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => applyMode(b.dataset.mode));
applyMode('screen');
