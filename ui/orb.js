// The orb (docs/ui-spec.md section 3). A port of the mockup's frame() (ui/mockups/kiosk.html):
// one filled circle whose edge is a sum of slow sine waves, three soft lights drifting
// inside, a highlight, and a glow. Each state sets targets; the orb eases toward them over
// ~0.4 s, so state changes never jump. Original design, no assets.
//
// API (window.Orb):
//   Orb.state(name)    idle | ready | listening | thinking | replying | fallback | safety |
//                      reconnecting | ooo | lost
//   Orb.kick()         small pulse (keypress now; mic level at M6; the "wave" action)
//   Orb.shake()        one quick side-to-side shake (fallback)
//   Orb.talk(text)     swell with the words while a sentence is "spoken" (TTS level at M6)
//   Orb.reduced(bool)  reduced motion: static orb; palette and size still follow the state
'use strict';
(() => {
  const cv = document.getElementById('orb');
  const ctx = cv.getContext('2d');
  const CX = 960, CY = 330, BASE_R = 195;

  const PAL = {   // [core, light A, light B, light C]
    calm:   ['#0E1A2B', '#3EE6D2', '#5B8CFF', '#B48CFF'],
    listen: ['#0E1A2E', '#6FD3FF', '#5B8CFF', '#3EE6D2'],
    think:  ['#140F2C', '#B48CFF', '#6E7CFF', '#FF8FD8'],
    warn:   ['#2A1C0B', '#FFD166', '#FF9A5A', '#FFB4A2'],
    err:    ['#22121A', '#FF9A8B', '#A86BFF', '#5B8CFF'],
    off:    ['#15181D', '#5A6472', '#4A525E', '#3A414B'],
  };
  const TARGETS = {
    idle:         { pal: 'calm',   scale: 1.00, amp: .020, speed: .5,  swirl: .35, glow: .55, alpha: 1 },
    ready:        { pal: 'calm',   scale: 1.00, amp: .022, speed: .6,  swirl: .40, glow: .60, alpha: 1 },
    listening:    { pal: 'listen', scale: 1.06, amp: .030, speed: .9,  swirl: .55, glow: .80, alpha: 1 },
    thinking:     { pal: 'think',  scale: .92,  amp: .028, speed: 2.4, swirl: 1.6, glow: .75, alpha: 1, ripple: 1 },
    replying:     { pal: 'calm',   scale: 1.02, amp: .024, speed: 1.0, swirl: .60, glow: .80, alpha: 1 },
    fallback:     { pal: 'warn',   scale: .98,  amp: .030, speed: .8,  swirl: .50, glow: .70, alpha: 1 },
    safety:       { pal: 'calm',   scale: 1.00, amp: .010, speed: .25, swirl: .15, glow: .50, alpha: 1 },
    reconnecting: { pal: 'err',    scale: .85,  amp: .012, speed: .3,  swirl: .20, glow: .35, alpha: .75, pulse: 1 },
    ooo:          { pal: 'off',    scale: .78,  amp: 0,    speed: 0,   swirl: 0,   glow: .15, alpha: .55, still: 1 },
    lost:         { pal: 'off',    scale: .78,  amp: 0,    speed: 0,   swirl: 0,   glow: .15, alpha: .55, still: 1 },
  };
  const KEYS = ['scale', 'amp', 'speed', 'swirl', 'glow', 'alpha', 'ripple', 'pulse', 'still'];

  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const HEX = Object.fromEntries(Object.entries(PAL).map(([k, v]) => [k, v.map(hex)]));
  const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
  const ease = (a, b, k) => a + (b - a) * k;

  const cur = { scale: 1, amp: .02, speed: .5, swirl: .35, glow: .55, alpha: 1, ripple: 0, pulse: 0,
                still: 0, pal: HEX.calm.map(c => c.slice()) };
  let target = TARGETS.idle;
  let kick = 0, shake = 0, talkEnv = 0, talkUntil = 0, phase = 0, swirlPhase = 0;
  let rm = false;
  let last = performance.now();

  function frame(now) {
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    const k = rm ? 1 : 1 - Math.pow(.02, dt);       // eases over ~0.4 s (instant when reduced)
    for (const key of KEYS) cur[key] = ease(cur[key], target[key] ?? 0, k);
    const tp = HEX[target.pal];
    for (let i = 0; i < 4; i++) for (let j = 0; j < 3; j++) cur.pal[i][j] = ease(cur.pal[i][j], tp[i][j], k);

    // Speech envelope while a sentence is on its way out (M6: driven by the TTS audio level).
    const talking = now < talkUntil;
    const tEnv = talking ? .35 + .65 * Math.abs(Math.sin(now / 95) * Math.sin(now / 233 + 1.3)) : 0;
    talkEnv = ease(talkEnv, tEnv, 1 - Math.pow(.0005, dt));
    kick *= Math.pow(.015, dt); shake *= Math.pow(.03, dt);

    // Reduced motion and out of order: no breathing, drift, ripple, pulses or shake.
    const motion = rm ? 0 : 1 - cur.still;
    phase += dt * cur.speed * motion;
    swirlPhase += dt * cur.swirl * motion;

    const breathe = 1 + .025 * Math.sin(now / 1000 * 1.1) * motion;               // ~6 s cycle
    const pulse = 1 - cur.pulse * .08 * (.5 + .5 * Math.sin(now / 1000 * Math.PI)) * motion;  // 0.5 Hz
    const R = BASE_R * cur.scale * breathe * pulse * (1 + (.06 * kick + .10 * talkEnv) * motion);
    const amp = (cur.amp + .05 * kick + .06 * talkEnv) * motion;
    const sx = Math.sin(now / 38) * 18 * shake * motion;

    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.save(); ctx.globalAlpha = cur.alpha; ctx.translate(sx, 0);

    // outer glow
    const g = ctx.createRadialGradient(CX, CY, R * .6, CX, CY, R * 2.1);
    g.addColorStop(0, rgba(cur.pal[1], .22 * cur.glow));
    g.addColorStop(.5, rgba(cur.pal[2], .08 * cur.glow));
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, cv.width, cv.height);

    // the shape: a filled circle with a soft, moving edge
    ctx.beginPath();
    const N = 160;
    for (let i = 0; i <= N; i++) {
      const a = i / N * Math.PI * 2;
      let d = .50 * Math.sin(3 * a + phase * 1.3) + .30 * Math.sin(5 * a - phase * 1.7) + .20 * Math.sin(2 * a + phase * .9);
      d += cur.ripple * .9 * Math.sin(7 * a - phase * 3.2);   // thinking: a ripple travels around the edge
      const r = R * (1 + amp * d);
      if (i) ctx.lineTo(CX + r * Math.cos(a), CY + r * Math.sin(a)); else ctx.moveTo(CX + r, CY);
    }
    ctx.closePath();
    ctx.fillStyle = rgba(cur.pal[0], 1); ctx.fill();
    ctx.save(); ctx.clip();

    // three lights drifting inside
    ctx.globalCompositeOperation = 'lighter';
    for (const [c, size, sp, off] of [[cur.pal[1], .80, 1.0, 0], [cur.pal[2], .85, 1.3, 2.1], [cur.pal[3], .70, .8, 4.2]]) {
      const lx = CX + R * .34 * Math.cos(swirlPhase * sp + off);
      const ly = CY + R * .30 * Math.sin(swirlPhase * sp * 1.3 + off);
      const lg = ctx.createRadialGradient(lx, ly, 0, lx, ly, R * (size + .25 * talkEnv * motion));
      lg.addColorStop(0, rgba(c, .85 * cur.glow + .15)); lg.addColorStop(1, rgba(c, 0));
      ctx.fillStyle = lg; ctx.fillRect(CX - R * 1.3, CY - R * 1.3, R * 2.6, R * 2.6);
    }
    // soft highlight so it reads as a solid sphere
    const hl = ctx.createRadialGradient(CX - R * .35, CY - R * .45, 0, CX - R * .35, CY - R * .45, R * .9);
    hl.addColorStop(0, 'rgba(255,255,255,.16)'); hl.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = hl; ctx.fillRect(CX - R * 1.3, CY - R * 1.3, R * 2.6, R * 2.6);
    ctx.restore(); ctx.restore();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  window.Orb = {
    state(name) { target = TARGETS[name] || TARGETS.idle; },
    kick() { kick = 1; },
    shake() { shake = 1; },
    talk(text) {
      const words = String(text || '').split(/\s+/).length;
      talkUntil = performance.now() + Math.min(4500, 260 * words);
    },
    reduced(on) { rm = !!on; },
  };
})();
