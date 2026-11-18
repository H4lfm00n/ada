// Cards (M5b UI): things Ada puts on the screen. docs/ui-spec.md section 5, PHASE2-SPEC 2.
//
// The server owns everything that matters: which card, its approved content (content/cards.yaml),
// and the check-in loop (spec 2.2). This module only draws what it is told.
//
// API (createCards(stage, { blob, send, strings }) returns):
//   show(data)          data = one cards.yaml entry as sent in `show_card` {id, data}
//   state(id, s, opts)  check-in state from `card_state`: intro | asking | waiting | yes | timeout
//   mini(id)            `card_mini`: shrink to the corner tile (QR stays scannable)
//   hide(id)            `hide_card`: slide out
//   current             id of the card on screen, or null
//
// Visitor taps on "Yes" / "Not yet" go back as {type: 'card_reply', id, answer: 'yes'|'no'}
// (voice is the main path; taps help in a loud room). The server decides what happens next.
//
// Safety in the UI too (defense in depth): only `type: qr` cards get a code, only for an
// https URL; text is always set with textContent; no inline style attributes (CSP).
import qrcode from './vendor/qrcode.mjs';

const ICON = {
  qr: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3M21 14v.01M14 21h3M21 18v3"/></svg>',
  phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4"/><path d="M9 8l2 2 4-4"/></svg>',
  pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  cal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4 14h7l-1 8 9-12h-7z"/></svg>',
  rules: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/></svg>',
  help: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17v.01"/></svg>',
  spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M18 6l-2.5 2.5M8.5 15.5L6 18"/></svg>',
  no: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
  mail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>',
  dot: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/></svg>',
  ear: '<svg class="ear" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg>',
  // Original line art for the equipment card (Ada's style; no product photos or logos).
  dog: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><rect x="14" y="24" width="30" height="13" rx="4"/><path d="M44 27l7-6h6v8l-6 2h-7"/><circle cx="53" cy="24.5" r="1"/><path d="M18 37l-3 13M23 37l1 13M36 37l-2 13M41 37l3 13M14 27l-6-4"/></svg>',
  arm: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 54h24M17 54v-5h14v5"/><circle cx="24" cy="44" r="3.5"/><path d="M24 40.5L30 22"/><circle cx="31" cy="19" r="3.5"/><path d="M34 18l14 6"/><path d="M48 24l5-6M48 24l6 3"/></svg>',
  printer: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><rect x="10" y="10" width="44" height="44" rx="4"/><path d="M10 20h44"/><path d="M28 20v10l4 5 4-5V20"/><path d="M22 46h20M25 42h14"/></svg>',
  kit: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="24" cy="30" r="9"/><circle cx="24" cy="30" r="3"/><path d="M24 17v4M24 39v4M11 30h4M33 30h4M15 21l3 3M30 36l3 3M33 21l-3 3M18 36l-3 3"/><circle cx="44" cy="44" r="6"/><path d="M44 35v3M44 50v3M35 44h3M50 44h3"/></svg>',
  chip: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><rect x="18" y="18" width="28" height="28" rx="4"/><rect x="26" y="26" width="12" height="12" rx="2"/><path d="M24 12v6M32 12v6M40 12v6M24 46v6M32 46v6M40 46v6M12 24h6M12 32h6M12 40h6M46 24h6M46 32h6M46 40h6"/></svg>',
  building: '<svg viewBox="0 0 120 120" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M14 104h92"/><rect x="24" y="30" width="72" height="74" rx="4"/><path d="M24 62h72"/><path d="M34 44h10M54 44h10M74 44h10M34 78h10M74 78h10"/><path d="M52 104V84h16v20"/><circle cx="92" cy="40" r="17" fill="#fff"/><text x="92" y="47" text-anchor="middle" font-family="Inter, sans-serif" font-weight="700" font-size="19" fill="currentColor" stroke="none">2F</text><path d="M60 62v-6" /></svg>',
};
const EYEBROW = {
  qr: ['qr', 'Scan me'], directions: ['pin', 'Find us'], list: ['cal', "What's happening"],
  spotlight: ['bolt', 'Equipment'], checklist: ['rules', 'Good to know'], contact: ['help', 'Safety'],
  fun: ['spark', 'Did you know?'],
};
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = String(text);
  return n;
}
function icon(name, cls) {
  const t = document.createElement('template');
  t.innerHTML = ICON[name] || ICON.dot;   // static strings from this file only
  const svg = t.content.firstElementChild;
  if (cls) svg.classList.add(cls);
  svg.setAttribute('aria-hidden', 'true');
  return svg;
}

// QR as one SVG path (no data URLs, no inline styles). Error correction M, quiet zone 4.
function qrSvg(url) {
  const q = qrcode(0, 'M');
  q.addData(url);
  q.make();
  const n = q.getModuleCount(), qz = 4, size = n + qz * 2;
  let d = '';
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) if (q.isDark(r, c)) d += `M${c + qz} ${r + qz}h1v1h-1z`;
  }
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
  svg.setAttribute('role', 'img');
  const bg = document.createElementNS(NS, 'rect');
  bg.setAttribute('width', size); bg.setAttribute('height', size); bg.setAttribute('fill', '#fff');
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('d', d); p.setAttribute('fill', '#000');
  svg.append(bg, p);
  return svg;
}
const okUrl = u => typeof u === 'string' && /^https:\/\/[^\s<>"]+$/.test(u);

function gearIcon(s) {
  const t = s.toLowerCase();
  if (t.includes('dog')) return 'dog';
  if (t.includes('arm')) return 'arm';
  if (t.includes('print')) return 'printer';
  if (t.includes('kit')) return 'kit';
  return 'chip';
}
function ruleIcon(s) {
  const t = s.toLowerCase();
  if (/^no\b|not allowed|can't|cannot/.test(t)) return ['no', 'no'];
  if (t.includes('overnight') || t.includes('night')) return ['moon', ''];
  if (t.includes('@') || t.includes('ask') || t.includes('book')) return ['mail', ''];
  return ['check', ''];
}

// ---------------------------------------------------------------- body builders per type
const BUILD = {
  qr(d, body, card) {
    const wrap = el('div', 'qrwrap'), box = el('div', 'qrbox pulse');
    if (okUrl(d.url)) {
      box.append(qrSvg(d.url));
      box.setAttribute('aria-label', 'QR code');
    } else {
      // Never shown in production (the server doesn't send cards without an approved URL).
      box.append(qrSvg('ADA PREVIEW'), el('div', 'qrpending', d.pending_label || 'Sample code · URL waiting for PM approval'));
    }
    for (const k of ['tl', 'tr', 'bl', 'br']) box.append(el('span', `corner ${k}`));
    const hint = el('div', 'qrhint');
    hint.append(icon('phone'), el('span', null, d.qr_caption || card.strings.qr_caption || 'Point your phone camera at the code.'));
    wrap.append(box, hint);
    if (d.sample) wrap.append(el('div', 'qrsample', 'Preview only: sample link, not the real form'));
    body.append(wrap);
  },
  directions(d, body) {
    const where = el('div', 'where');
    const txt = el('div');
    const [first, ...rest] = d.lines || [];
    const m = /^(.*?)·\s*(.+)$/.exec(first || '');
    txt.append(el('div', 'room', m ? m[2].trim() : first || ''));
    if (m) txt.append(el('div', 'sub', m[1].trim()));
    where.append(icon('building'), txt);
    body.append(where);
    if (rest.length) {
      const ul = el('ul', 'lines');
      rest.forEach(l => ul.append(el('li', null, l)));
      body.append(ul);
    }
    if (d.you_are_here) {
      const p = el('div', 'pill here'); p.append(icon('pin'), el('span', null, "You're here!")); body.append(p);
    }
    if (d.hours) {
      const p = el('div', 'pill'); p.append(icon('clock'), el('span', null, d.hours)); body.append(p);
    }
  },
  list(d, body) {
    const items = (d.items && d.items.length) ? d.items : (d.fallback_items || []);
    const ul = el('ul', 'events');
    for (const it of items.slice(0, 4)) {
      const li = el('li'), date = el('div', 'date');
      const o = typeof it === 'string' ? (() => { const [what, ...meta] = it.split(' · '); return { what, meta: meta.join(' · ') }; })() : it;
      const dt = o.date ? new Date(`${o.date}T12:00:00`) : null;
      if (dt && !isNaN(dt)) { date.append(el('b', null, dt.getDate()), el('span', null, MONTHS[dt.getMonth()])); }
      else { date.classList.add('none'); date.append(icon('cal'), el('span', null, 'Ongoing')); }
      const txt = el('div');
      txt.append(el('div', 'what', o.what || ''));
      if (o.meta) txt.append(el('div', 'meta', o.meta));
      li.append(date, txt);
      ul.append(li);
    }
    body.append(ul);
  },
  spotlight(d, body) {
    const g = el('div', 'gear');
    (d.items || []).slice(0, 6).forEach(s => { const c = el('div'); c.append(icon(gearIcon(s)), el('span', null, s)); g.append(c); });
    body.append(g);
    if (d.footer) body.append(el('div', 'cardnote', d.footer));
  },
  checklist(d, body) {
    const ul = el('ul', 'rules');
    (d.items || []).forEach(s => {
      const [ic, kind] = ruleIcon(s), li = el('li'), box = el('span', `ico ${kind}`);
      box.append(icon(ic)); li.append(box, el('span', null, s)); ul.append(li);
    });
    body.append(ul);
  },
  contact(d, body) {
    const ul = el('ul', 'helprows');
    (d.items || []).forEach((s, i) => {
      const k = s.lastIndexOf(': ');
      const li = el('li', i === 0 ? 'first' : null);
      if (k > 0) li.append(el('div', 'lbl', s.slice(0, k)), el('div', 'num', s.slice(k + 2)));
      else li.append(el('div', 'num', s));
      ul.append(li);
    });
    body.append(ul);
  },
  fun(d, body) {
    const facts = d.facts || [];
    const fact = d.fact || facts[Math.floor(Math.random() * facts.length)] || '';
    body.append(icon('spark', 'spark'), el('div', 'fact', fact));
  },
};

export function createCards(stage, { blob, send = () => {}, strings: strs = {} } = {}) {
  const getS = typeof strs === 'function' ? strs : () => strs;
  const layer = el('div'); layer.id = 'cards';
  stage.append(layer);
  let node = null, cur = null, curData = null, hideTimer = null;
  const api = { get strings() { return getS() || {}; } };

  function pose(on, mini) {
    stage.classList.toggle('carding', on);
    stage.classList.toggle('mini-card', !!(on && mini));
    if (blob) blob.showing(!!(on && !mini));
  }
  function startTimer(seconds) {
    const t = node && node.querySelector('.timer');
    if (!t) return;
    clearTimeout(hideTimer);
    t.classList.remove('run', 'paused');
    if (!(seconds > 0)) { t.hidden = true; return; }
    t.hidden = false;
    void t.offsetWidth;   // restart the animation
    t.firstChild.style.animationDuration = `${seconds}s`;   // CSSOM, allowed by the CSP
    t.classList.add('run');
  }
  function removeNow() {
    if (!node) return;
    const old = node; node = null; cur = null; curData = null;
    old.classList.remove('in'); old.classList.add('out');
    setTimeout(() => old.remove(), 800);
    pose(false);
  }

  api.show = (d) => {
    if (!d || !BUILD[d.type]) return;
    if (node) { node.remove(); node = null; }
    cur = d.id; curData = d;
    node = el('section', `card type-${d.type}`);
    if (d.type === 'contact') node.classList.add('help');
    if (d.type === 'fun') node.classList.add('fun');
    node.setAttribute('role', 'region');
    node.setAttribute('aria-label', d.title || 'Card');
    const [ic, word] = EYEBROW[d.type];
    const eb = el('div', 'eyebrow'); eb.append(icon(d.type === 'contact' ? 'help' : ic), el('span', null, d.eyebrow || word));
    node.append(eb, el('h2', null, d.title || ''));
    if (d.subtitle && d.type !== 'qr') node.append(el('p', 'sub', d.subtitle));
    const body = el('div', 'body');
    BUILD[d.type](d, body, api);
    node.append(body);

    const foot = el('div', 'foot'), st = el('div', 'status');
    foot.append(st);
    const bar = el('div', 'timer'); bar.append(el('i'));
    node.append(foot, bar);
    layer.append(node);
    void node.offsetWidth;
    node.classList.add('in');
    pose(true, false);
    startTimer(d.auto_hide_s ?? 60);
  };

  api.state = (id, s, opts = {}) => {
    if (!node || id !== cur) return;
    const strings = api.strings;
    const st = node.querySelector('.status');
    const box = node.querySelector('.qrbox');
    st.replaceChildren(); st.className = 'status on';
    if (s === 'intro') { st.classList.remove('on'); return; }
    if (s === 'asking' || s === 'waiting') {
      st.classList.add(s);
      st.append(icon('ear'), el('span', null, opts.text || (s === 'asking' ? (curData.confirm || strings.card_confirm || 'Did you scan it?') : (strings.card_wait || 'Take your time…'))));
      st.append(el('span', 'spacer'));
      const yes = el('button', 'chip yes', strings.card_yes || 'Yes, got it');
      const no = el('button', 'chip', strings.card_no || 'Not yet');
      yes.type = no.type = 'button';
      yes.onclick = () => send({ type: 'card_reply', id: cur, answer: 'yes' });
      no.onclick = () => send({ type: 'card_reply', id: cur, answer: 'no' });
      if (s === 'asking') st.append(yes, no); else st.append(yes);
      if (s === 'waiting') startTimer(opts.seconds || 10);
      return;
    }
    if (s === 'yes') {
      if (box) box.classList.remove('pulse');
      st.classList.add('done'); st.append(icon('check'), el('span', null, opts.text || strings.card_done || 'Got it!'));
      node.querySelector('.timer').hidden = true;
      const old = node;
      old.classList.add('done');
      setTimeout(() => { if (node === old) removeNow(); }, 1600);
      return;
    }
    if (s === 'timeout') { removeNow(); }
  };

  api.mini = (id) => {
    if (!node || id !== cur) return;
    node.classList.add('mini');
    node.querySelector('.status').className = 'status';
    pose(true, true);
  };
  api.hide = (id) => { if (!id || id === cur) removeNow(); };
  Object.defineProperty(api, 'current', { get: () => cur });
  return api;
}
