// Ada's wireframe blob (docs/ui-spec.md v3 section 3; PHASE2-SPEC 3.3).
//
// A Three.js wireframe icosphere (detail 5) plus a faint inner core. Each frame every vertex
// is pushed along its normal by 3D simplex noise plus a breathing term, and by the audio
// swell. All look parameters ease toward per-state targets (no hard cuts). Idle acts add a
// shape morph, a displacement band, or a highlight on top, then hand back to the state.
// Original design, no assets. Positions are updated on the CPU (~2.2k vertices).
//
// API (createBlob(canvas) returns):
//   state(name)        idle | waking | listening | thinking | speaking | confused | out | lost
//   showing(bool)      card pose: shrink, move upper-left, lean toward the card (M5b hook)
//   level(v, source)   audio loudness 0-1 (mic while listening, tts while speaking). Smoothed
//                      by a soft envelope (~0.25 s attack, ~0.6 s release, DES51); never maps
//                      raw RMS straight to displacement.
//   act(id, seconds)   play an idle act (ids in ACTS); returns false if unknown
//   stopAct()          stop the act now (any interaction); the blob eases back
//   reduced(bool)      prefers-reduced-motion: noise and breathing at 35 %, no spins
//   fps(), cpuMs()     frames per second; JS + draw-submit ms per frame (debug panel)
import * as THREE from './vendor/three.module.min.js';

// ---------------------------------------------------------------- simplex noise (3D)
// Stefan Gustavson's public-domain simplex noise, compacted. Output roughly -1..1.
const GRAD = new Float32Array([1,1,0,-1,1,0,1,-1,0,-1,-1,0,1,0,1,-1,0,1,1,0,-1,-1,0,-1,0,1,1,0,-1,1,0,1,-1,0,-1,-1]);
const PERM = new Uint8Array(512);
(() => {
  const p = new Uint8Array(256);
  for (let i = 0; i < 256; i++) p[i] = i;
  let s = 1337;   // fixed seed: the blob looks the same on every reload
  for (let i = 255; i > 0; i--) { s = (s * 16807) % 2147483647; const j = s % (i + 1); [p[i], p[j]] = [p[j], p[i]]; }
  for (let i = 0; i < 512; i++) PERM[i] = p[i & 255];
})();
const F3 = 1 / 3, G3 = 1 / 6;
function simplex3(x, y, z) {
  const s = (x + y + z) * F3, i = Math.floor(x + s), j = Math.floor(y + s), k = Math.floor(z + s);
  const t = (i + j + k) * G3, x0 = x - i + t, y0 = y - j + t, z0 = z - k + t;
  let i1, j1, k1, i2, j2, k2;
  if (x0 >= y0) {
    if (y0 >= z0) { i1 = 1; j1 = 0; k1 = 0; i2 = 1; j2 = 1; k2 = 0; }
    else if (x0 >= z0) { i1 = 1; j1 = 0; k1 = 0; i2 = 1; j2 = 0; k2 = 1; }
    else { i1 = 0; j1 = 0; k1 = 1; i2 = 1; j2 = 0; k2 = 1; }
  } else if (y0 < z0) { i1 = 0; j1 = 0; k1 = 1; i2 = 0; j2 = 1; k2 = 1; }
  else if (x0 < z0) { i1 = 0; j1 = 1; k1 = 0; i2 = 0; j2 = 1; k2 = 1; }
  else { i1 = 0; j1 = 1; k1 = 0; i2 = 1; j2 = 1; k2 = 0; }
  const ii = i & 255, jj = j & 255, kk = k & 255;
  let n = 0;
  const corner = (dx, dy, dz, g) => {
    let tt = 0.6 - dx * dx - dy * dy - dz * dz;
    if (tt < 0) return 0;
    g = (g % 12) * 3; tt *= tt;
    return tt * tt * (GRAD[g] * dx + GRAD[g + 1] * dy + GRAD[g + 2] * dz);
  };
  n += corner(x0, y0, z0, PERM[ii + PERM[jj + PERM[kk]]]);
  n += corner(x0 - i1 + G3, y0 - j1 + G3, z0 - k1 + G3, PERM[ii + i1 + PERM[jj + j1 + PERM[kk + k1]]]);
  n += corner(x0 - i2 + 2 * G3, y0 - j2 + 2 * G3, z0 - k2 + 2 * G3, PERM[ii + i2 + PERM[jj + j2 + PERM[kk + k2]]]);
  n += corner(x0 - 1 + 3 * G3, y0 - 1 + 3 * G3, z0 - 1 + 3 * G3, PERM[ii + 1 + PERM[jj + 1 + PERM[kk + 1]]]);
  return 32 * n;
}

// ---------------------------------------------------------------- look parameters
// BRIGHT = the waking flash; DEEP = act highlights (on a light screen, emphasis is darker)
const ACCENT = 0x7C3AED, BRIGHT = 0xB794F6, DEEP = 0x4C1D95, REST = 0x9AA0A8;
// Units: 1 world unit = 260 design px (spec: r ~ 260 px); y = 0.615 puts the centre at 380 px.
const BASE = { amp: .06, freq: 1, speed: .6, spin: .08, scale: 1, sx: 1, sy: 1, x: 0, y: .615, tilt: 0,
  op: .62, bright: 0, gray: 0, shimmer: 0, breath: 1 };
const STATES = {
  idle:      {},
  waking:    { amp: .09, speed: 1.4, spin: .4, scale: 1.08, y: .76, op: .95, bright: .35 },
  listening: { amp: .05, speed: .9, spin: .12, scale: 1.04, y: .66, op: .78, bright: .1 },
  thinking:  { amp: .025, freq: 2.4, speed: 2.2, spin: 1.4, scale: .94, op: .72, shimmer: 1 },
  speaking:  { amp: .04, speed: .7, spin: .12, op: .75 },
  confused:  { amp: .08, speed: 1.2, spin: .05, scale: .98, op: .62 },
  out:       { amp: 0, speed: 0, spin: 0, scale: .82, sy: .55, y: .32, op: .45, gray: 1, breath: 0 },
  lost:      { amp: .01, speed: .2, spin: 0, scale: .85, sy: .8, y: .45, op: .4, gray: .85, breath: .3 },
};
const SHOWING = { scale: .62, x: -1.87, y: .69, tilt: -.12 };   // card pose (spec 3.3)

const clamp01 = v => Math.max(0, Math.min(1, v));
const smooth = (a, b, v) => { const t = clamp01((v - a) / (b - a)); return t * t * (3 - 2 * t); };
// in (0..a) -> hold -> out (b..1): a 0..1..0 envelope over an act's progress u
const envelope = (u, a, b) => (u < a ? smooth(0, a, u) : u > b ? 1 - smooth(b, 1, u) : 1);

// ---------------------------------------------------------------- idle acts
// Each act may return per-frame: { p: {param overrides}, shape(dir,local)->radius multiplier
// blend {m, r}, band(dir, local)->extra radius, glow(dir, local)->0..1, warp(pos) }.
// `u` = progress 0..1, `t` = seconds since the act started, `g` = 1 or 0.35 (reduced, gentle).
const ACTS = {
  daydream_cube: (u, t, g) => {
    const m = envelope(u, .25, .6) * .95 * g;
    return { p: { amp: .12 - .1 * m, spin: .08 + .25 * m }, shape: d => {
      const c = .82 / Math.max(Math.abs(d.x), Math.abs(d.y), Math.abs(d.z));
      return { m, r: c };
    } };
  },
  daydream_star: (u, t, g) => {
    const m = envelope(u, .25, .6) * g;
    return { p: { amp: .03, spin: .02 }, shape: (d, L) => {
      const a = Math.atan2(L.y, L.x);
      const sr = .72 + .62 * Math.pow(Math.abs(Math.cos(2.5 * a)), 5);
      return { m, r: 1 + (sr - 1) * (1 - Math.abs(L.z) * .7) };
    } };
  },
  daydream_arm: (u, t, g) => {   // stretch into a two-segment arm, wave once, melt back
    const m = envelope(u, .2, .7) * g;
    const wave = Math.sin(clamp01((u - .3) / .4) * Math.PI * 2) * .7;   // one wave
    return { p: { spin: 0, amp: .03, y: .615 - .22 * m }, arm: { m, bend: 1.0 + wave } };
  },
  juggling: (u, t) => {   // the third ball drops on loop 3; the blob squashes and tilts
    const oops = t > 5.4 && t < 6.6;
    return { balls: { t, drop: true }, p: oops ? { tilt: .18, sy: .92 } : {} };
  },
  juggling_success: (u, t) => ({ balls: { t, drop: false }, p: u > .85 ? { y: .615 + .12 * Math.sin((u - .85) / .15 * Math.PI) } : {} }),
  dozing: (u, t, g) => ({ p: { sy: 1 - .1 * g, y: .52, speed: .25, amp: .035, op: .42, breath: .9 }, zzz: true, breathPeriod: 8 }),
  hello_wave: (u, t, g) => {
    const c = u * 3.2;   // ripple travels from the front centre outward
    return { p: { tilt: .06 * Math.sin(u * Math.PI) * g }, band: (d, L, F) => {
      const ang = Math.acos(Math.max(-1, Math.min(1, F)));
      const x = (ang - c) / .35;
      return .1 * g * Math.exp(-x * x) * (1 - u);
    } };
  },
  try_asking_proposal: () => ({}),
  try_asking_ai: () => ({}),
  try_asking_where: () => ({}),
  stretch: (u, t, g) => {
    const a = envelope(u, .2, .45) * (u < .5 ? 1 : 0), b = u >= .5 ? envelope((u - .5) * 2, .3, .6) : 0;
    return { p: { sy: 1 + (.25 * a - .18 * b) * g, sx: 1 + (-.13 * a + .18 * b) * g } };
  },
  look_around: (u, t, g) => {
    const az = u < .33 ? -smooth(0, .33, u) : u < .66 ? -1 + 2 * smooth(.33, .66, u) : 1 - smooth(.66, 1, u);
    return { gaze: { x: Math.sin(az * .9), y: .1, z: Math.cos(az * .9) }, glowAmt: envelope(u, .1, .9) };
  },
  shy_shrink: (u, t, g) => {
    const m = envelope(u, .25, .55) * g;
    return { p: { scale: 1 - .14 * m, y: .615 - .18 * m } };
  },
  wireframe_wave: (u, t, g) => {
    const c = 1.3 - 2.6 * u;
    return { band: (d, L, F, U) => { const x = (U - c) / .2; return .13 * g * Math.exp(-x * x); } };
  },
  spin_on_axis: (u) => {   // one quick full turn with a little overshoot
    const s = 1.70158, v = u - 1;
    return { extraSpin: 2 * Math.PI * (1 + (s + 1) * v * v * v + s * v * v), p: { spin: 0 } };
  },
  fans_spinning: (u, t, g) => {
    const m = envelope(u, .3, .6) * g;
    return { p: { amp: .06 + .08 * m, freq: 1 + 1.6 * m, speed: .6 + 2.6 * m } };
  },
  count_vertices: (u) => {
    const c = 1.1 - 2.4 * clamp01(u / .8);
    const fade = u > .8 ? 1 - smooth(.8, 1, u) : 1;
    return { glow: (d, L, F, U) => (U > c ? fade : 0) * (U > c - .12 ? 1 : 0) };
  },
};
export const ACT_IDS = Object.keys(ACTS);

// ---------------------------------------------------------------- the blob
export function createBlob(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setClearColor(0x000000, 0);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 16 / 9, .1, 100);
  camera.position.set(0, 0, 6.59);

  const geo = new THREE.IcosahedronGeometry(1, 5);
  const base = geo.attributes.position.array.slice();
  const nV = base.length / 3;
  const colors = new Float32Array(nV * 3);
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const mat = new THREE.MeshBasicMaterial({ vertexColors: true, wireframe: true, transparent: true, opacity: .62, depthWrite: false });
  const mesh = new THREE.Mesh(geo, mat);
  const coreMat = new THREE.MeshBasicMaterial({ color: ACCENT, wireframe: true, transparent: true, opacity: .12, depthWrite: false });
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(.62, 2), coreMat);
  const group = new THREE.Group();
  group.add(mesh, core);
  scene.add(group);
  const ballMat = () => new THREE.MeshBasicMaterial({ color: ACCENT, wireframe: true, transparent: true, opacity: 0, depthWrite: false });
  const balls = [0, 1, 2].map(() => { const b = new THREE.Mesh(new THREE.IcosahedronGeometry(.17, 1), ballMat()); scene.add(b); return b; });

  const P = { ...BASE }, T = { ...BASE };
  let stateName = 'idle', showingOn = false, reducedOn = false;
  let level = 0, levelTarget = 0, swell = 0, levelAt = 0;
  let act = null;   // { id, fn, t0, dur }
  let t = 0, last = performance.now(), rotY = 0, frames = 0, fpsVal = 0, fpsAt = performance.now(), cpuMs = 0;
  const cBase = new THREE.Color(), cBright = new THREE.Color(BRIGHT), cDeep = new THREE.Color(DEEP), cTmp = new THREE.Color();
  const qInv = new THREE.Quaternion(), vUp = new THREE.Vector3(), vFront = new THREE.Vector3();
  const vRight = new THREE.Vector3(), vGaze = new THREE.Vector3();
  const D = { x: 0, y: 0, z: 0 }, L = { x: 0, y: 0, z: 0 };

  function retarget() {
    Object.assign(T, BASE, STATES[stateName] || {});
    if (showingOn && stateName !== 'out' && stateName !== 'lost') Object.assign(T, SHOWING);
  }

  // confused: a quick wobble-tilt, ±0.15 rad over 0.8 s
  let wobbleT0 = -1;

  function frame(now) {
    const c0 = performance.now();
    const dt = Math.min(.05, (now - last) / 1000); last = now; t += dt;
    frames++; if (now - fpsAt >= 1000) { fpsVal = frames * 1000 / (now - fpsAt); frames = 0; fpsAt = now; }
    const g = reducedOn ? .35 : 1;

    // act overlay (computed first: it can override targets for this frame)
    let A = null;
    if (act) {
      const at = t - act.t0, u = clamp01(at / act.dur);
      if (u >= 1) stopAct(); else A = act.fn(u, at, g) || {};
    }
    const target = A && A.p ? { ...T, ...A.p } : T;
    const k = 1 - Math.pow(.02, dt);
    for (const key in P) P[key] += (target[key] - P[key]) * k;

    // audio envelope: soft attack/release, then a slower swell (DES51)
    if (now - levelAt > 400) levelTarget = 0;   // no fresh level for 0.4 s: let it fall
    const atk = levelTarget > level ? .02 : .2;
    level += (levelTarget - level) * (1 - Math.pow(atk, dt));
    swell += (level - swell) * (1 - Math.pow(.15, dt));

    // wobble (confused)
    let wob = 0;
    if (wobbleT0 >= 0) {
      const w = t - wobbleT0;
      if (w > .8) wobbleT0 = -1; else wob = .15 * Math.sin(w / .8 * Math.PI * 3) * (1 - w / .8) * g;
    }

    // group transform first: acts that work in screen space need the inverse rotation
    rotY += P.spin * dt * (reducedOn ? 0 : 1);
    const extra = A && A.extraSpin ? A.extraSpin * (reducedOn ? 0 : 1) : 0;
    group.rotation.set(.25 + Math.sin(t * .4) * .05 * g, rotY + extra, P.tilt + wob);
    group.position.set(P.x, P.y, 0);
    group.scale.set(P.scale * P.sx, P.scale * P.sy, P.scale);
    group.updateMatrixWorld();
    qInv.copy(group.quaternion).invert();
    vUp.set(0, 1, 0).applyQuaternion(qInv);
    vFront.set(0, 0, 1).applyQuaternion(qInv);
    vRight.set(1, 0, 0).applyQuaternion(qInv);
    if (A && A.gaze) vGaze.set(A.gaze.x, A.gaze.y, A.gaze.z).normalize().applyQuaternion(qInv);

    const period = A && A.breathPeriod ? A.breathPeriod : 5;
    const breath = Math.sin(t * 2 * Math.PI / period) * .03 * g * P.breath;
    const amp = P.amp * g, fq = P.freq * 1.6, ts = t * P.speed;
    const shimmer = P.shimmer * g;
    const pos = geo.attributes.position.array;
    cBase.set(ACCENT).lerp(cTmp.set(REST), P.gray).lerp(cBright, P.bright * (1 - P.gray));
    for (let v = 0, i = 0; v < nV; v++, i += 3) {
      const bx = base[i], by = base[i + 1], bz = base[i + 2];
      D.x = bx; D.y = by; D.z = bz;
      const F = bx * vFront.x + by * vFront.y + bz * vFront.z;   // facing the room
      const U = bx * vUp.x + by * vUp.y + bz * vUp.z;            // screen-up
      // the direction in screen space (right, up, toward the room), for silhouettes
      L.x = bx * vRight.x + by * vRight.y + bz * vRight.z; L.y = U; L.z = F;
      let r = 1 + breath + amp * simplex3(bx * fq, by * fq, bz * fq + ts)
        + swell * .06 * g + swell * .05 * g * Math.sin(U * 2.2 - t * 3.2)     // swell + one slow wave
        + (shimmer ? shimmer * .012 * simplex3(bx * 6, by * 6, bz * 6 + t * 9) : 0);
      // highlight (look around, counting): blend toward the deep color
      let h = 0;
      if (A) {
        if (A.shape) { const s = A.shape(D, L); r = r * (1 - s.m) + s.r * r * s.m; }
        if (A.band) r += A.band(D, L, F, U);
        if (A.glow) h = A.glow(D, L, F, U);
        else if (A.gaze) {   // the "gaze" region bulges a little too, so it reads as looking
          h = A.glowAmt * smooth(.5, .9, bx * vGaze.x + by * vGaze.y + bz * vGaze.z);
          r += .09 * g * h;
        }
      }
      let px = bx * r, py = by * r, pz = bz * r;
      if (A && A.arm) {   // object y is ~screen up (the blob only spins around y)
        // upper arm stays vertical; above the "elbow" (U > .15) the forearm leans sideways
        const m = A.arm.m, bend = m * Math.max(0, U - .15) * A.arm.bend;
        px = px * (1 - .5 * m) + vRight.x * bend;
        py = py * (1 + .38 * m) + vRight.y * bend;
        pz = pz * (1 - .5 * m) + vRight.z * bend;
      }
      pos[i] = px; pos[i + 1] = py; pos[i + 2] = pz;
      const c = h > 0 ? cTmp.copy(cBase).lerp(cDeep, h * .85) : cBase;
      colors[i] = c.r; colors[i + 1] = c.g; colors[i + 2] = c.b;
    }
    geo.attributes.position.needsUpdate = true;
    geo.attributes.color.needsUpdate = true;
    coreMat.color.copy(cBase);
    mat.opacity = P.op + (shimmer ? Math.sin(t * 9) * .05 * shimmer : 0);
    coreMat.opacity = P.op * .2;

    // juggling balls
    const B = A && A.balls;
    balls.forEach((b, i) => {
      if (!B) { b.material.opacity = 0; return; }
      const jt = B.t, a = jt * 2.4 + i * 2.094, R = 1.55 * P.scale;
      let x = P.x + Math.cos(a) * R, y = P.y + Math.sin(a) * R * .55 + .2, op = .75;
      if (B.drop && i === 2 && jt > 5.2) {   // third loop: one ball drops, rolls back in at the end
        const ft = jt - 5.2, a0 = 5.2 * 2.4 + i * 2.094;
        x = P.x + Math.cos(a0) * R + ft * .4; y = P.y + Math.sin(a0) * R * .55 - ft * ft * 4.5; op = Math.max(0, .75 - ft * .5);
        if (jt > 7.6) { const back = clamp01((jt - 7.6) / 1); x = P.x + 2.4 - back * 1.2; y = P.y - 1.15; op = .75 * back; }
      }
      b.position.set(x, y, .6); b.material.opacity = op; b.rotation.y += dt * 2; b.material.color.copy(cBase);
    });

    renderer.render(scene, camera);
    cpuMs += (performance.now() - c0 - cpuMs) * .05;   // JS + draw submit per frame (EMA)
    requestAnimationFrame(frame);
  }

  function resize(width, height, pixelRatio) {
    renderer.setPixelRatio(Math.min(2, pixelRatio || 1));
    renderer.setSize(width, height, false);
    camera.aspect = width / height; camera.updateProjectionMatrix();
  }
  resize(1920, 1080, 1);
  requestAnimationFrame(frame);

  function stopAct() { act = null; retarget(); }

  return {
    state(name) {
      if (!STATES[name]) name = 'idle';
      stateName = name;
      if (name === 'confused') wobbleT0 = t;
      retarget();
    },
    get current() { return stateName; },
    showing(on) { showingOn = !!on; retarget(); },
    level(v, _source) { levelTarget = clamp01(+v || 0); levelAt = performance.now(); },
    act(id, seconds) {
      const fn = ACTS[id];
      if (!fn || stateName === 'out' || stateName === 'lost') return false;
      act = { id, fn, t0: t, dur: Math.max(.5, +seconds || 5) };
      return true;
    },
    stopAct,
    get acting() { return act ? act.id : null; },
    reduced(on) { reducedOn = !!on; },
    resize,
    fps: () => Math.round(fpsVal),
    cpuMs: () => Math.round(cpuMs * 10) / 10,
  };
}
