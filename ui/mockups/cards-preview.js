// Preview driver for ui/mockups/cards.html: plays the events the server will send.
// The check-in timings and lines come from content/cards.yaml (spec 2.2). Fake data only.
import { createBlob } from '../blob.js';
import { createCards } from '../cards.js';

const $ = id => document.getElementById(id);
const stage = $('stage'), caps = $('captions'), sw = $('statusword'), mic = $('mic'), mictext = $('mictext');
const blob = createBlob($('blob'));
let timers = [];
const later = (ms, fn) => timers.push(setTimeout(fn, ms));
const stopAll = () => { timers.forEach(clearTimeout); timers = []; };

// Visitor taps on the card come here (the real kiosk sends them to the server).
const cards = createCards(stage, { blob, send: o => o.type === 'card_reply' && reply(o.answer) });

function fit() {
  const s = Math.min(innerWidth / 1920, innerHeight / 1080);
  stage.style.transform = `translate(-50%, -50%) scale(${s})`;
  blob.resize(1920, 1080, (window.devicePixelRatio || 1) * s);
}
addEventListener('resize', fit); fit();

function say(text) {
  blob.state('speaking');
  const n = document.createElement('div'); n.className = 'sent'; n.textContent = text;
  caps.replaceChildren(n);
  const t = setInterval(() => blob.level(.45 + Math.random() * .3, 'tts'), 120);
  later(Math.max(1600, text.split(' ').length * 300), () => { clearInterval(t); blob.state('idle'); });
}
function listen(on, word = 'Listening') {
  sw.textContent = on ? word : ''; sw.classList.toggle('on', on);
  mic.classList.toggle('on', on); mictext.textContent = on ? 'Listening' : 'Mic off';
  if (on) blob.state('listening');
}
function visitor(text) {
  const n = document.createElement('div'); n.className = 'sent muted'; n.textContent = `Visitor: "${text}"`;
  caps.replaceChildren(n);
}

const SAMPLE = 'https://ai.sfsu.edu/ai-students';   // public page, preview only
const CARDS = {
  qr_project_proposal: { type: 'qr', title: 'Submit a project proposal', url: SAMPLE, sample: true,
    say_before: "I've put a QR code on the screen. Point your phone camera at it to open the proposal form.",
    confirm: 'Did you get it?', on_yes: 'Awesome! The team will follow up with you. Anything else?',
    on_no: 'No rush, take your time.', auto_hide_s: 60 },
  qr_discord: { type: 'qr', title: 'Join the Student AI Commons Discord', url: SAMPLE, sample: true,
    say_before: "I've put the Discord QR code on the screen. Scan it with your phone camera to join.",
    confirm: 'Did you scan it?', on_yes: 'Nice, see you in there! Anything else?', on_no: 'No rush, take your time.', auto_hide_s: 60 },
  map_where_we_are: { type: 'directions', title: 'Where we are',
    lines: ['AI Studio · LIB 260', '2nd floor, J. Paul Leonard Library', 'Inside the Digital Media Studio and MakerSpace'],
    hours: 'Mon–Thu 10 AM–7 PM · Fri 10 AM–5 PM', say_before: "Here's where to find us.", auto_hide_s: 30 },
  events_this_month: { type: 'list', title: "What's happening",
    fallback_items: ['Student AI Commons meetings · LIB 260 · times from ai@sfsu.edu',
      "Weekly AI club meetings · see the clubs' GatorXperience listings"],
    say_before: "Here's what's coming up. It's all on the screen.", auto_hide_s: 45 },
  equipment_spotlight: { type: 'spotlight', title: 'Equipment for student projects',
    items: ['Robotic dogs', 'Robotic arms', '3D printer', 'Robotics kits', 'NVIDIA GB10 checkout', 'Jetson Orin checkout'],
    footer: 'To use it: submit a project proposal.', say_before: "Here's some of the gear students can use for projects.", auto_hide_s: 40 },
  compute_rules: { type: 'checklist', title: 'Computer rules',
    items: ['No remote access to AI Lab computers', 'Overnight jobs only if started in person at the lab', 'GPU booking: ask the AI Lab team (ai@sfsu.edu)'],
    say_before: 'Here are the computer rules.', auto_hide_s: 40 },
  campus_help: { type: 'contact', title: 'Get help now',
    items: ['Emergency: 911', 'From a cell phone on campus (University Police): 415-338-2222',
      'Crisis or thoughts of suicide, call or text: 988', 'Counseling (CAPS), Mon–Fri: 415-338-2208'],
    say_before: 'If this is an emergency, call 911 now. Help numbers are on the screen.', auto_hide_s: 0 },
  ai_fact: { type: 'fun', title: 'Fun fact', facts: [
    'Ada Lovelace wrote what is often called the first computer program, in the 1840s.',
    'Transformers read every word in a sentence at once, using a trick called attention.',
    'A GPU has thousands of tiny cores, which is why AI loves them.'], say_before: "Here's a fun one.", auto_hide_s: 20 },
};

let flowCard = null, asked = 0;
function show(id) {
  stopAll(); listen(false); flowCard = null;
  const d = { ...CARDS[id], id };
  cards.show(d); cards.state(id, 'intro');
  say(d.say_before);
  return d;
}
function ask(d) {
  say(d.confirm);
  later(1400, () => { cards.state(d.id, 'asking'); listen(true); });
}
function reply(answer) {
  if (!flowCard) return;
  const d = flowCard;
  stopAll(); listen(false);
  if (answer === 'yes') { visitor('Yes, got it'); later(700, () => { cards.state(d.id, 'yes'); say(d.on_yes); flowCard = null; }); }
  else {
    visitor('Not yet, one sec'); asked++;
    later(700, () => { say(d.on_no); cards.state(d.id, 'waiting', { seconds: 10 }); listen(true, 'Listening'); });
    later(asked > 1 ? 9000 : 7000, () => ask(d));
  }
}

const FLOWS = {
  yes() { const d = flowCard = show('qr_project_proposal'); asked = 0; later(6000, () => ask(d)); later(10500, () => reply('yes')); },
  notyet() { const d = flowCard = show('qr_discord'); asked = 0; later(6000, () => ask(d)); later(10500, () => reply('no'));
    later(10500 + 8600, () => reply('yes')); },
  else() { const d = flowCard = show('qr_project_proposal'); asked = 0;
    later(4500, () => { listen(false); visitor('Wait, do you have robot dogs?'); cards.mini(d.id); });
    later(5800, () => { blob.state('thinking'); });
    later(6900, () => say('We do! Students can use robotic dogs for projects. The QR code is still in the corner if you need it.')); },
  timeout() { const d = flowCard = show('qr_discord'); asked = 0; later(6000, () => ask(d));
    later(11000, () => { listen(false); caps.replaceChildren(); cards.state(d.id, 'timeout'); blob.state('idle'); flowCard = null; }); },
};

document.querySelectorAll('[data-flow]').forEach(b => b.onclick = () => FLOWS[b.dataset.flow]());
document.querySelectorAll('[data-card]').forEach(b => b.onclick = () => {
  const d = show(b.dataset.card);
  if (d.type === 'qr') { flowCard = d; asked = 0; later(6000, () => ask(d)); }
});
document.querySelector('[data-hide]').onclick = () => { stopAll(); listen(false); flowCard = null; cards.hide(); caps.replaceChildren(); blob.state('idle'); };
later(600, () => FLOWS.yes());
