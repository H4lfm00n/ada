// Preview driver for ui/mockups/idle-acts.html: plays each act on the real orb.
import { createBlob, ACT_IDS } from '../blob.js';

const $ = id => document.getElementById(id);
const stage = $('stage'), cap = $('idlecap');
const blob = createBlob($('blob'));
const DUR = { neural_doodle: 7, loading_bar: 8, sneeze: 4.5, chase_glow: 7, peekaboo: 4.5, what_am_i: 9, sigh: 4,
  goodbye_wave: 3.4, juggling: 9, juggling_success: 8, dozing: 8 };
const CAP = { neural_doodle: 'Doodling a tiny neural network.', sneeze: 'Achoo! Sorry. Dust in my pixels.',
  chase_glow: 'Must… catch… the light.', what_am_i: 'Guess what I am! Tap to find out.', sigh: 'So… quiet in here.' };
let timers = [], guess = null;
const later = (ms, fn) => timers.push(setTimeout(fn, ms));
function fit() {
  const s = Math.min(innerWidth / 1920, innerHeight / 1080);
  stage.style.transform = `translate(-50%, -50%) scale(${s})`;
  blob.resize(1920, 1080, (window.devicePixelRatio || 1) * s);
}
addEventListener('resize', fit); fit();
function caption(t) { cap.textContent = t; cap.classList.add('on'); }
function play(id) {
  timers.forEach(clearTimeout); timers = []; cap.classList.remove('on'); guess = null;
  const old = $('loadbar'); if (old) old.remove();
  const dur = DUR[id] || 6, opts = {};
  if (id === 'what_am_i') { opts.shape = window.forceShape || ['star', 'cube', 'heart'][Math.floor(Math.random() * 3)]; guess = opts.shape;
    later(dur * 740, () => reveal(false)); }
  blob.act(id, dur, opts);
  if (CAP[id]) caption(CAP[id]);
  if (id === 'peekaboo') later(dur * 620, () => caption('Peekaboo!'));
  if (id === 'loading_bar') bar(dur);
  later(dur * 1000 - 500, () => cap.classList.remove('on'));
}
function reveal(tapped) {
  if (!guess) return; const g = guess; guess = null;
  blob.reveal(tapped ? 2.8 : 2.2);
  caption(tapped ? `It's a ${g}! Got a question for me?` : `It was a ${g}!`);
}
function bar(dur) {
  const b = document.createElement('div'); b.id = 'loadbar';
  b.innerHTML = '<div class="lbl"></div><div class="track"><i></i></div>';
  stage.appendChild(b);
  const t0 = performance.now(), stall = dur * .62, fill = b.querySelector('i'), lbl = b.querySelector('.lbl');
  let pct = 0;
  (function tick() {
    if (!b.isConnected) return;
    const el = (performance.now() - t0) / 1000;
    if (el < stall) pct = Math.min(99, Math.round(99 * Math.pow(el / stall, .7)));
    fill.style.transform = `scaleX(${pct / 100})`;
    lbl.textContent = el < stall + .9 ? `Loading something fun… ${pct}%` : '99%… nope. Never mind.';
    b.classList.toggle('stuck', el >= stall + .9);
    requestAnimationFrame(tick);
  })();
  requestAnimationFrame(() => b.classList.add('on'));
  later(dur * 1000 - 700, () => b.classList.remove('on'));
  later(dur * 1000, () => b.remove());
}
stage.addEventListener('pointerdown', () => reveal(true));
const row = $('acts');
for (const id of ACT_IDS.filter(a => !a.startsWith('try_asking'))) {
  const btn = document.createElement('button'); btn.textContent = id; btn.onclick = () => play(id); row.append(btn);
}
window.play = play;
