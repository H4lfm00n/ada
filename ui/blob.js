// Ada's blob (docs/ui-spec.md v3 section 3; PHASE2-SPEC 3.3), v2: a smooth, softly shaded
// solid (PM, 2026-11-17: "very smooth and easy looking, not a wireframe", D64).
//
// A finely subdivided sphere deformed in the vertex shader by slow, low-frequency 3D simplex
// noise plus a breathing term and the audio swell; normals are re-derived in the shader so
// the shading stays smooth. It reads as a violet glass orb: a bright rim and a crisp highlight
// outside, and soft ribbons of pink, sky and lavender light flowing inside at two depths,
// with a colored halo behind it (PM, D66). State sets how bright and how fast the light flows.
//
// Smoothness rules (the v1 wireframe twitched):
//   - Every phase is integrated (phase += rate * dt), never `time * rate`, so changing a rate
//     (idle -> thinking) can't make the surface jump.
//   - All look parameters ease toward their state targets with a ~0.45 s time constant.
//   - Audio drives one soft swell (attack ~0.25 s, release ~0.6 s, DES51), never per word.
//   - No flicker, no strobing opacity, no high-frequency noise.
//
// API (createBlob(canvas) returns):
//   state(name)        idle | waking | listening | thinking | speaking | confused | out | lost
//   showing(bool)      card pose: shrink, move upper-left, lean toward the card (M5b hook)
//   level(v, source)   audio loudness 0-1 (mic while listening, tts while speaking)
//   talking(bool)      stand-in while Ada "speaks" without TTS: a slow, steady gentle swell
//   act(id, seconds)   play an idle act (ids in ACTS); returns false if unknown
//   stopAct()          stop the act now (any interaction); the blob eases back
//   reduced(bool)      prefers-reduced-motion: motion at 35 %, no spins
//   fps(), cpuMs()     frames per second; JS + draw-submit ms per frame (debug panel)
import * as THREE from './vendor/three.module.min.js';

// ---------------------------------------------------------------- shaders
// 3D simplex noise: Ian McEwan, Ashima Arts (MIT License; github.com/ashima/webgl-noise).
const NOISE = /* glsl */`
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+10.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.5-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 105.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

const VERT = /* glsl */`
uniform float uNoiseT, uAmp, uFreq, uBreath, uSwell, uSwellT, uG;
uniform float uCube, uStar, uHeart, uArmM, uArmBend, uRippleC, uRippleA, uWaveC, uWaveA;
uniform float uGazeAmt, uGazeLo, uCountC, uCountFade, uSweep, uSweepT;
uniform vec3 uRight, uUp, uFront, uGaze;
varying vec3 vN; varying vec3 vV; varying vec3 vD; varying float vGlow;
${NOISE}
vec3 shapeAt(vec3 d, out float h){
  float U=dot(d,uUp), F=dot(d,uFront), X=dot(d,uRight);
  float r=1.0+uBreath+uAmp*uG*snoise(d*uFreq+vec3(0.0,0.0,uNoiseT))
         +uSwell*uG*(0.055+0.035*sin(U*2.0-uSwellT));
  if(uCube>0.001){ float c=0.82/max(max(abs(d.x),abs(d.y)),abs(d.z)); r=mix(r,r*c,uCube); }
  if(uStar>0.001){ float a=atan(U,X); float sr=0.74+0.58*pow(abs(cos(2.5*a)),4.0);
                   float core=smoothstep(0.08,0.5,length(vec2(X,U)));   // no pinch at the centre
                   r=mix(r,r*(1.0+(sr-1.0)*(1.0-abs(F)*0.7)*core),uStar); }
  if(uHeart>0.001){ float a=atan(U,X);                       // two lobes, a cleft on top, a point below
                    float hr=0.84+0.3*exp(-pow((a-2.17)/0.48,2.0))+0.3*exp(-pow((a-0.97)/0.48,2.0))
                             -0.3*exp(-pow((a-1.5708)/0.2,2.0))+0.48*exp(-abs(a+1.5708)/0.3);
                    float core=smoothstep(0.08,0.5,length(vec2(X,U)));
                    r=mix(r,r*mix(1.0,hr*0.92,(1.0-abs(F)*0.6)*core),uHeart); }
  if(uRippleA>0.0){ float ang=acos(clamp(F,-1.0,1.0)); float x=(ang-uRippleC)/0.45; r+=uRippleA*exp(-x*x); }
  if(uWaveA>0.0){ float x=(U-uWaveC)/0.28; r+=uWaveA*exp(-x*x); }
  h=0.0;
  if(uGazeAmt>0.0){ h=uGazeAmt*smoothstep(uGazeLo,mix(0.92,0.99,(uGazeLo-0.45)/0.5),dot(d,uGaze)); r+=0.06*uG*h; }
  if(uCountFade>0.0){ h=max(h,uCountFade*smoothstep(uCountC-0.3,uCountC+0.3,U)); }
  if(uSweep>0.0){ float ang=atan(F,X); h=max(h,uSweep*0.45*pow(0.5+0.5*cos(ang-uSweepT),5.0)); }
  vec3 p=d*r;
  if(uArmM>0.001){ float e=U-0.15; float bend=uArmM*0.5*(e+sqrt(e*e+0.03))*uArmBend;   // soft elbow
    p.x*=1.0-0.5*uArmM; p.z*=1.0-0.5*uArmM; p.y*=1.0+0.38*uArmM; p+=uRight*bend; }
  return p;
}
void main(){
  vec3 d=normalize(position);
  float h, hh;
  vec3 p=shapeAt(d,h);
  vec3 t1=normalize(cross(d,abs(d.y)<0.99?vec3(0.0,1.0,0.0):vec3(1.0,0.0,0.0)));
  vec3 t2=cross(d,t1);
  vec3 p1=shapeAt(normalize(d+t1*0.012),hh), p2=shapeAt(normalize(d+t2*0.012),hh);
  vec3 n=normalize(cross(p1-p,p2-p)); if(dot(n,d)<0.0) n=-n;
  vN=normalize(normalMatrix*n);
  vec4 mv=modelViewMatrix*vec4(p,1.0);
  vV=-mv.xyz; vGlow=h; vD=d;
  gl_Position=projectionMatrix*mv;
}`;

const FRAG = /* glsl */`
uniform vec3 uDeep, uMid, uRim, uC1, uC2, uC3;
uniform float uGray, uBright, uOpacity, uFlowT, uEnergy, uSwellF;
varying vec3 vN; varying vec3 vV; varying vec3 vD; varying float vGlow;
${NOISE}
// Three soft ribbons of light winding through the orb, warped by slow noise so they flow.
vec3 ribbons(vec3 q, float t){
  float n1=snoise(q*0.75+vec3(0.0,t*0.22,t*0.14));
  float n2=snoise(q*1.05+vec3(t*0.17,0.0,-t*0.2)+n1*0.4);
  float b1=abs(sin(q.y*1.5+n1*0.9+t*0.55));
  float b2=abs(sin(dot(q,normalize(vec3(0.8,0.3,0.5)))*1.7+n2*0.85-t*0.45));
  float b3=abs(sin(dot(q,normalize(vec3(-0.4,0.7,0.6)))*1.3+(n1+n2)*0.6+t*0.35));
  return uC1*pow(1.0-b1,4.0)+uC2*pow(1.0-b2,4.0)+uC3*0.8*pow(1.0-b3,3.5);
}
void main(){
  vec3 n=normalize(vN), v=normalize(vV);
  float facing=max(dot(n,v),0.0);
  vec3 L=normalize(vec3(-0.45,0.75,0.55));
  float diff=smoothstep(0.0,1.0,dot(n,L)*0.5+0.5);
  vec3 col=mix(uDeep,uMid,diff*0.55+(1.0-facing)*0.25);      // deep violet glass, lighter toward the edge
  float e=uEnergy*(1.0+uSwellF*0.9);
  // two layers at different depths: parallax makes it read as light inside a volume
  vec3 inner=ribbons(vD,uFlowT)+0.4*ribbons(vD*0.7+vec3(0.31,-0.22,0.13),uFlowT*0.8+3.1);
  col+=inner*e*0.8*(0.3+0.7*smoothstep(0.0,0.85,facing));
  col+=uC3*0.28*e*pow(facing,3.0);                           // soft inner core
  float rim=pow(1.0-facing,2.6);
  col=mix(col,uRim,rim*0.75);                                // bright glassy edge
  vec3 R=reflect(-L,n); float sp=max(dot(R,v),0.0);
  col+=pow(sp,70.0)*0.75+pow(sp,8.0)*0.07;                   // crisp highlight + sheen
  col=mix(col,uRim,vGlow*0.45);
  col=mix(col,uRim,uBright*0.25);
  vec3 gray=mix(vec3(0.42,0.44,0.48),vec3(0.80,0.81,0.84),diff); gray=mix(gray,vec3(0.94),rim*0.6);
  gray+=pow(sp,70.0)*0.5;
  col=mix(col,gray,uGray);
  gl_FragColor=vec4(clamp(col,0.0,1.0),uOpacity);
}`;

// The halo: a soft colored glow behind the orb, slowly shifting hue.
const HALO_VERT = /* glsl */`varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`;
const HALO_FRAG = /* glsl */`
uniform vec3 uC1, uC2, uC3; uniform float uHalo, uFlowT; varying vec2 vUv;
void main(){
  vec2 p=vUv*2.0-1.0; float r=length(p), a=atan(p.y,p.x);
  vec3 c=mix(uC1,uC2,0.5+0.5*sin(a+uFlowT*0.4));
  c=mix(c,uC3,0.25+0.25*sin(a*2.0-uFlowT*0.3));
  float x=max(r-0.52,0.0)/0.2;
  gl_FragColor=vec4(c,uHalo*0.38*exp(-x*x)*smoothstep(1.0,0.75,r));
}`;

// Soft round dots (sparkles, doodle nodes) and fading lines (doodle edges). Normal blending:
// the page is light, so additive light would vanish off the orb.
const DOT_VERT = /* glsl */`
attribute float aAlpha; attribute vec3 aColor; uniform float uSize, uPR;
varying float vA; varying vec3 vC;
void main(){ vA=aAlpha; vC=aColor; vec4 mv=modelViewMatrix*vec4(position,1.0);
  gl_PointSize=uSize*uPR*6.5/max(-mv.z,0.1); gl_Position=projectionMatrix*mv; }`;
const DOT_FRAG = /* glsl */`
varying float vA; varying vec3 vC;
void main(){ float d=length(gl_PointCoord-vec2(0.5)); float a=smoothstep(0.5,0.12,d)*vA;
  if(a<0.01) discard; gl_FragColor=vec4(mix(vC,vec3(1.0),smoothstep(0.25,0.0,d)*0.55),a); }`;
const LINE_VERT = /* glsl */`
attribute float aAlpha; varying float vA;
void main(){ vA=aAlpha; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`;
const LINE_FRAG = /* glsl */`uniform vec3 uC; varying float vA; void main(){ gl_FragColor=vec4(uC,vA); }`;

const rgb = hex => new THREE.Vector3(((hex >> 16) & 255) / 255, ((hex >> 8) & 255) / 255, (hex & 255) / 255);
// Ada's palette: violet glass around --accent #7C3AED, with pink, sky and lavender light inside.
const DEEP = 0x2E1065, MID = 0x6D28D9, RIM = 0xEDE9FE, C1 = 0xF472B6, C2 = 0x60A5FA, C3 = 0xC4B5FD;

// ---------------------------------------------------------------- look parameters
// Units: 1 world unit = 260 design px (spec: r ~ 260 px); y = 0.615 puts the centre at 380 px.
const BASE = { amp: .026, freq: .85, speed: .14, spin: .05, scale: 1, sx: 1, sy: 1, x: 0, y: .615, tilt: 0,
  op: 1, bright: 0, gray: 0, sweep: 0, breath: 1, energy: .75, flow: .3, halo: .6 };
const STATES = {
  idle:      {},
  waking:    { amp: .04, speed: .3, scale: 1.05, y: .7, bright: .5, energy: 1.15, flow: .6, halo: .95 },
  listening: { amp: .036, speed: .25, scale: 1.03, y: .65, bright: .15, energy: .95, flow: .45, halo: .85 },
  thinking:  { amp: .026, freq: 1.15, speed: .4, scale: .95, sweep: 1, energy: 1, flow: .95, halo: .9 },
  speaking:  { amp: .03, speed: .2, energy: .9, flow: .45, halo: .8 },
  confused:  { amp: .045, speed: .25, scale: .98, energy: .7, flow: .35 },
  out:       { amp: .012, speed: .05, spin: 0, scale: .82, sy: .6, y: .32, gray: 1, breath: .2, op: .92,
               energy: 0, flow: .03, halo: 0 },
  lost:      { amp: .02, speed: .08, spin: 0, scale: .86, sy: .82, y: .45, gray: .85, breath: .4, op: .88,
               energy: .1, flow: .05, halo: 0 },
};
const SHOWING = { scale: .62, x: -1.87, y: .69, tilt: -.12 };   // card pose (spec 3.3)

const clamp01 = v => Math.max(0, Math.min(1, v));
const smooth = (a, b, v) => { const t = clamp01((v - a) / (b - a)); return t * t * (3 - 2 * t); };
// in (0..a) -> hold -> out (b..1): a 0..1..0 envelope over an act's progress u
const envelope = (u, a, b) => (u < a ? smooth(0, a, u) : u > b ? 1 - smooth(b, 1, u) : 1);
const ease = (dt, tau) => 1 - Math.exp(-dt / tau);

// ---------------------------------------------------------------- idle acts
// Each act returns per-frame overrides: { p: {look params}, cube, star, arm: {m, bend},
// ripple: {c, a}, wave: {c, a}, gaze: {x,y,z}, glowAmt, count: {c, fade}, balls, extraSpin,
// breathPeriod }. `u` = progress 0..1, `t` = seconds since start, `g` = 1 or 0.35 (reduced).
const ACTS = {
  daydream_cube: (u, t, g) => ({ cube: envelope(u, .3, .6) * .9 * g, p: { amp: .02 } }),
  daydream_star: (u, t, g) => ({ star: envelope(u, .3, .6) * g, p: { amp: .02, spin: 0 } }),
  juggling: (u, t) => {   // the third ball drops on loop 3; the blob squashes and tilts
    const oops = envelope(clamp01((t - 5.3) / 1.6), .3, .6);
    return { balls: { t, drop: true }, p: { tilt: .14 * oops, sy: 1 - .07 * oops } };
  },
  juggling_success: (u, t) => ({ balls: { t, drop: false },
    p: { y: .615 + .1 * Math.sin(smooth(.82, 1, u) * Math.PI) } }),
  dozing: (u, t, g) => ({ p: { sy: 1 - .08 * g, y: .53, speed: .08, amp: .03, bright: 0, breath: .9 },
    breathPeriod: 8 }),
  hello_wave: (u, t, g) => ({ ripple: { c: smooth(0, 1, u) * 3.2, a: .07 * g * (1 - u) },
    p: { tilt: .05 * Math.sin(u * Math.PI) * g } }),
  fun_fact: (u) => ({ extraSpin: Math.PI * smooth(0, .6, u), p: { spin: 0 } }),   // slow half-turn
  try_asking_proposal: () => ({}),
  try_asking_ai: () => ({}),
  try_asking_where: () => ({}),
  stretch: (u, t, g) => {
    const a = u < .5 ? envelope(u * 2, .35, .65) : 0, b = u >= .5 ? envelope((u - .5) * 2, .35, .65) : 0;
    return { p: { sy: 1 + (.18 * a - .12 * b) * g, sx: 1 + (-.09 * a + .12 * b) * g } };
  },
  look_around: (u, t, g) => {
    const az = u < .33 ? -smooth(0, .33, u) : u < .66 ? -1 + 2 * smooth(.33, .66, u) : 1 - smooth(.66, 1, u);
    return { gaze: { x: Math.sin(az * .8), y: .12, z: Math.cos(az * .8) }, glowAmt: envelope(u, .15, .85),
      p: { tilt: -.06 * az * g } };
  },
  shy_shrink: (u, t, g) => {
    const m = envelope(u, .3, .6) * g;
    return { p: { scale: 1 - .1 * m, y: .615 - .14 * m } };
  },
  wireframe_wave: (u, t, g) => ({ wave: { c: 1.3 - 2.6 * smooth(0, 1, u), a: .06 * g } }),
  spin_on_axis: (u) => ({ extraSpin: 2 * Math.PI * smooth(0, 1, u), p: { spin: 0 } }),
  fans_spinning: (u, t, g) => {
    const m = envelope(u, .35, .6) * g;
    return { p: { amp: .045 + .035 * m, freq: .9 + .4 * m, speed: .16 + .35 * m } };
  },
  // ---- idle life v2 (PM 2026-11-18): Ada gets bored ----
  // dots appear inside her, connect into a tiny 3-4-3 network, a signal runs through, fade
  neural_doodle: (u) => ({ doodle: u, p: { energy: .35, flow: .12, spin: 0, amp: .016 } }),
  // the page draws the bar (kiosk.js); the orb works hard, stalls at 99 %, slumps
  loading_bar: (u, t, g) => {
    const slump = smooth(.66, .76, u) * (1 - smooth(.9, 1, u));
    return { p: { sweep: u < .66 ? .9 : 0, flow: u < .66 ? .9 : .1, energy: .75 - .4 * slump, bright: 0 },
      squash: { x: 1 + .03 * slump * g, y: 1 - .07 * slump * g }, lift: -.07 * slump, rock: .08 * slump * g };
  },
  // two hitching breaths in, a big squash, a burst of sparkles, a springy wobble back
  sneeze: (u, t, g, o) => {
    const tb = .55 * o.dur, k = t - tb;
    if (k < 0) {
      const inh = Math.max(.55 * envelope(clamp01((u - .06) / .22), .45, .7), smooth(.3, .54, u));
      return { squash: { x: 1 - .05 * inh * g, y: 1 + .12 * inh * g }, lift: .06 * inh, mult: 1 + .03 * inh,
        p: { bright: .35 * inh }, rock: -.06 * inh * g };
    }
    const w = Math.exp(-k * 3.2) * Math.cos(k * 15);
    return { squash: { x: 1 + .2 * w * g, y: 1 - .22 * w * g }, lift: -.05 * Math.max(w, 0),
      sparks: { t: k, n: 1 }, p: { bright: .2 * Math.exp(-k * 2) } };
  },
  // a bright spot runs around her surface faster and faster; she tries to follow, then gets dizzy
  chase_glow: (u, t, g) => {
    const ang = 2 * Math.PI * (.12 * t + .055 * t * t), run = 1 - smooth(.8, .88, u);
    const dizzy = smooth(.8, .88, u) * (1 - smooth(.96, 1, u));
    return { gaze: { x: Math.sin(ang) * .8, y: Math.cos(ang) * .55 + .05, z: .7 }, glowAmt: 1.4 * envelope(u, .06, .82), gazeLo: .84,
      extraSpin: .3 * Math.sin(ang) * run * g, rock: (.07 * Math.sin(ang) * run + .09 * dizzy * Math.sin(t * 9)) * g,
      p: { speed: .3, spin: 0 } };
  },
  // shrinks to a dot, hides, then pops back with a bounce and a ripple
  peekaboo: (u, t, g) => {
    const hide = smooth(0, .22, u) * (1 - smooth(.6, .64, u));
    const pop = u >= .62 ? Math.exp(-(u - .62) * 12) * Math.sin((u - .62) * 28) : 0;
    return { mult: 1 - .93 * hide + .2 * pop * g, p: { bright: u > .6 ? .5 * (1 - smooth(.6, .9, u)) : 0 },
      ripple: { c: smooth(.62, .92, u) * 3.2, a: u > .62 ? .06 * g * (1 - smooth(.62, 1, u)) : 0 } };
  },
  // morphs into a shape (o.shape: star | cube | heart); the page captions "Guess what I am!"
  what_am_i: (u, t, g, o) => {
    const m = (o.revealed ? smooth(0, 1.2, t) * (1 - smooth(o.dur - .6, o.dur, t)) : envelope(u, .14, .88)) * g;
    const sh = o.shape || 'star';
    return { star: sh === 'star' ? m : 0, cube: sh === 'cube' ? .9 * m : 0, heart: sh === 'heart' ? m : 0,
      p: { amp: .014, spin: 0, bright: o.revealed ? .3 : 0 }, rock: o.revealed ? .05 * Math.sin(t * 6) * g : 0 };
  },
  // a long bored sigh: breathe in, deflate and droop, slowly recover
  sigh: (u, t, g) => {
    const a = u < .4 ? smooth(0, .32, u) : 1 - smooth(.36, .5, u), b = smooth(.4, .6, u) * (1 - smooth(.84, 1, u));
    return { mult: 1 + .05 * a * g, squash: { x: 1 + .03 * b * g, y: 1 - .1 * b * g }, lift: .03 * a - .07 * b,
      rock: .04 * b * g, p: { energy: .75 - .35 * b, flow: .3 - .15 * b } };
  },
  // goodbye (not an idle act: the page plays it when a conversation ends): a little bow,
  // a side-to-side wave, a hop and a few sparkles
  goodbye_wave: (u, t, g, o) => {
    const bow = envelope(clamp01(u / .32), .4, .6);
    const wave = u > .28 && u < .82 ? Math.sin((u - .28) / .54 * Math.PI * 4) * (1 - smooth(.74, .82, u)) : 0;
    const hop = u > .8 ? Math.sin(smooth(.8, .98, u) * Math.PI) : 0;
    return { squash: { x: 1 + .05 * bow * g, y: 1 - .09 * bow * g }, lift: -.06 * bow + .14 * hop * g,
      rock: .13 * wave * g, ripple: { c: smooth(.28, .9, u) * 3.2, a: u > .28 ? .05 * g * (1 - smooth(.28, .9, u)) : 0 },
      sparks: u > .86 ? { t: t - .86 * o.dur, n: .45 } : null, p: { bright: .25 * hop } };
  },
  count_vertices: (u) => ({ count: { c: 1.15 - 2.4 * smooth(0, .8, u), fade: u > .8 ? 1 - smooth(.8, 1, u) : 1 } }),
};
export const ACT_IDS = Object.keys(ACTS);

// ---------------------------------------------------------------- the blob
export function createBlob(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setClearColor(0x000000, 0);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 16 / 9, .1, 100);
  camera.position.set(0, 0, 6.59);

  const uniforms = {
    uNoiseT: { value: 0 }, uAmp: { value: BASE.amp }, uFreq: { value: BASE.freq }, uBreath: { value: 0 },
    uSwell: { value: 0 }, uSwellT: { value: 0 }, uG: { value: 1 },
    uCube: { value: 0 }, uStar: { value: 0 }, uHeart: { value: 0 }, uArmM: { value: 0 }, uArmBend: { value: 1 },
    uRippleC: { value: 0 }, uRippleA: { value: 0 }, uWaveC: { value: 0 }, uWaveA: { value: 0 },
    uGazeAmt: { value: 0 }, uGazeLo: { value: .45 }, uCountC: { value: 0 }, uCountFade: { value: 0 }, uSweep: { value: 0 }, uSweepT: { value: 0 },
    uRight: { value: new THREE.Vector3(1, 0, 0) }, uUp: { value: new THREE.Vector3(0, 1, 0) },
    uFront: { value: new THREE.Vector3(0, 0, 1) }, uGaze: { value: new THREE.Vector3(0, 0, 1) },
    uDeep: { value: rgb(DEEP) }, uMid: { value: rgb(MID) }, uRim: { value: rgb(RIM) },
    uC1: { value: rgb(C1) }, uC2: { value: rgb(C2) }, uC3: { value: rgb(C3) },
    uGray: { value: 0 }, uBright: { value: 0 }, uOpacity: { value: 1 },
    uFlowT: { value: 0 }, uEnergy: { value: BASE.energy }, uSwellF: { value: 0 },
  };
  const mat = new THREE.ShaderMaterial({ uniforms, vertexShader: VERT, fragmentShader: FRAG, transparent: true });
  const mesh = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 40), mat);
  const group = new THREE.Group();
  group.add(mesh);
  const haloU = { uC1: uniforms.uC1, uC2: uniforms.uC2, uC3: uniforms.uC3, uHalo: { value: 0 }, uFlowT: { value: 0 } };
  const halo = new THREE.Mesh(new THREE.PlaneGeometry(4, 4),
    new THREE.ShaderMaterial({ uniforms: haloU, vertexShader: HALO_VERT, fragmentShader: HALO_FRAG,
      transparent: true, depthWrite: false }));
  halo.renderOrder = -1;
  scene.add(halo, group);
  // juggling balls: the same look, no deformation
  const balls = [0, 1, 2].map(() => {
    const m = mat.clone();
    m.uniforms.uAmp.value = 0; m.uniforms.uOpacity.value = 0; m.uniforms.uEnergy.value = .8;
    const b = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 8), m);   // the shader normalizes,
    b.scale.setScalar(.17);                                              // so size comes from scale
    b.visible = false; scene.add(b); return b;
  });

  // sparkles (sneeze, goodbye) and the neural-net doodle: dots + lines in front of the orb
  const PR = { value: 1 };
  const dotMaterial = size => new THREE.ShaderMaterial({ uniforms: { uSize: { value: size }, uPR: PR },
    vertexShader: DOT_VERT, fragmentShader: DOT_FRAG, transparent: true, depthWrite: false, depthTest: false });
  function points(n, size, colors) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    geo.setAttribute('aAlpha', new THREE.BufferAttribute(new Float32Array(n), 1));
    const col = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) { const c = colors[i % colors.length]; col.set([c.x, c.y, c.z], i * 3); }
    geo.setAttribute('aColor', new THREE.BufferAttribute(col, 3));
    const o = new THREE.Points(geo, dotMaterial(size)); o.renderOrder = 2; o.frustumCulled = false; o.visible = false;
    scene.add(o); return o;
  }
  let seed = 7; const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
  const SPARK_N = 44;
  const sparks = points(SPARK_N, 34, [rgb(C1), rgb(C2), rgb(0x7C3AED), rgb(C3), rgb(0xF59E0B)]);
  const sparkDir = Array.from({ length: SPARK_N }, () => {
    const a = rnd() * Math.PI * 2, sp = .9 + rnd() * 1.6;
    return { x: Math.cos(a) * sp, y: Math.sin(a) * sp * .85 + .35, z: (rnd() - .5) * .6, r: .6 + rnd() * .5 };
  });
  const LAYERS = [3, 4, 3];
  const NODES = [];
  LAYERS.forEach((n, L) => { for (let i = 0; i < n; i++) NODES.push({ L,
    x: (L - 1) * .5 + (rnd() - .5) * .06, y: (i - (n - 1) / 2) * .3 + (rnd() - .5) * .05 }); });
  const EDGES = [];
  NODES.forEach((a, i) => NODES.forEach((b, j) => { if (b.L === a.L + 1) EDGES.push([i, j, a.L]); }));
  const nodePts = points(NODES.length, 40, [rgb(RIM)]);
  const edgeGeo = new THREE.BufferGeometry();
  edgeGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(EDGES.length * 6), 3));
  edgeGeo.setAttribute('aAlpha', new THREE.BufferAttribute(new Float32Array(EDGES.length * 2), 1));
  const edges = new THREE.LineSegments(edgeGeo, new THREE.ShaderMaterial({ uniforms: { uC: { value: rgb(RIM) } },
    vertexShader: LINE_VERT, fragmentShader: LINE_FRAG, transparent: true, depthWrite: false, depthTest: false }));
  edges.renderOrder = 2; edges.frustumCulled = false; edges.visible = false; scene.add(edges);

  const P = { ...BASE }, T = { ...BASE };
  let stateName = 'idle', showingOn = false, reducedOn = false, talkingOn = false;
  let level = 0, levelTarget = 0, swell = 0, levelAt = 0;
  let act = null;   // { id, fn, t0, dur }
  let t = 0, last = performance.now(), frames = 0, fpsVal = 0, fpsAt = performance.now(), cpuMs = 0;
  let lastExtra = 0, rotY = 0, noiseT = 0, breathPh = 0, swellT = 0, sweepT = 0, flowT = 0, wobbleT0 = -1;
  const AV = { mult: 1, sqx: 1, sqy: 1 };   // smoothed act values, so acts fade in and out instead of popping
  const qInv = new THREE.Quaternion();

  function retarget() {
    Object.assign(T, BASE, STATES[stateName] || {});
    if (showingOn && stateName !== 'out' && stateName !== 'lost') Object.assign(T, SHOWING);
  }

  function frame(now) {
    const c0 = performance.now();
    const dt = Math.min(.05, Math.max(0, (now - last) / 1000)); last = now; t += dt;
    frames++; if (now - fpsAt >= 1000) { fpsVal = frames * 1000 / (now - fpsAt); frames = 0; fpsAt = now; }
    const g = reducedOn ? .35 : 1;

    let A = null;
    if (act) {
      const at = t - act.t0, u = clamp01(at / act.dur);
      if (u >= 1) stopAct(); else A = act.fn(u, at, g, act.o) || {};
    }
    const target = A && A.p ? { ...T, ...A.p } : T;
    const k = ease(dt, .45);
    for (const key in P) P[key] += (target[key] - P[key]) * k;

    // audio: soft attack/release, then a slower swell (DES51). Without TTS, a steady gentle swell.
    if (now - levelAt > 400) levelTarget = talkingOn ? .32 + .06 * Math.sin(swellT * .9) : 0;
    level += (levelTarget - level) * ease(dt, levelTarget > level ? .25 : .6);
    swell += (level - swell) * ease(dt, .35);

    // integrated phases: changing a rate never makes the surface jump
    noiseT += P.speed * dt * (reducedOn ? .5 : 1);
    breathPh += dt * 2 * Math.PI / (A && A.breathPeriod ? A.breathPeriod : 5);
    swellT += dt * 1.4;
    sweepT += dt * 1.1;
    flowT += P.flow * dt * (reducedOn ? .4 : 1);
    rotY += P.spin * dt * (reducedOn ? 0 : 1);

    // confused: one gentle wobble-tilt (±0.09 rad over 1.2 s)
    let wob = 0;
    if (wobbleT0 >= 0) {
      const w = t - wobbleT0;
      if (w > 1.2) wobbleT0 = -1; else wob = .09 * Math.sin(w / 1.2 * Math.PI * 2) * Math.sin(w / 1.2 * Math.PI) * g;
    }

    const extra = A && A.extraSpin ? A.extraSpin * (reducedOn ? 0 : 1) : 0;
    lastExtra = extra;
    group.rotation.set(.2, rotY + extra, P.tilt + wob);
    group.position.set(P.x, P.y, 0);
    group.scale.set(P.scale * P.sx, P.scale * P.sy, P.scale);
    group.updateMatrixWorld();
    qInv.copy(group.quaternion).invert();

    // act values, smoothed
    const want = {
      cube: A && A.cube || 0, star: A && A.star || 0, heart: A && A.heart || 0,
      mult: A && A.mult != null ? A.mult : 1, rock: A && A.rock || 0, lift: A && A.lift || 0, sqx: A && A.squash ? A.squash.x : 1, sqy: A && A.squash ? A.squash.y : 1, armM: A && A.arm ? A.arm.m : 0,
      rippleA: A && A.ripple ? A.ripple.a : 0, waveA: A && A.wave ? A.wave.a : 0,
      gazeAmt: A && A.gaze ? A.glowAmt : 0, countFade: A && A.count ? A.count.fade : 0,
    };
    const ka = ease(dt, .2);
    const kf = ease(dt, .07);   // squash / pop: fast, still smooth
    for (const key in want) {
      const kk = key === 'mult' || key === 'sqx' || key === 'sqy' || key === 'rock' || key === 'lift' ? kf : ka;
      AV[key] = (AV[key] ?? 0) + (want[key] - (AV[key] ?? 0)) * kk;
    }
    group.scale.set(P.scale * P.sx * AV.sqx * AV.mult, P.scale * P.sy * AV.sqy * AV.mult, P.scale * AV.mult);
    group.rotation.z = P.tilt + wob + AV.rock;
    group.position.y = P.y + AV.lift;
    group.updateMatrixWorld();

    const U = uniforms;
    U.uNoiseT.value = noiseT; U.uAmp.value = P.amp; U.uFreq.value = P.freq;
    U.uBreath.value = Math.sin(breathPh) * .025 * g * P.breath;
    U.uSwell.value = swell; U.uSwellT.value = swellT; U.uG.value = g;
    U.uCube.value = AV.cube; U.uStar.value = AV.star; U.uHeart.value = AV.heart; U.uArmM.value = AV.armM;
    if (A && A.arm) U.uArmBend.value = A.arm.bend;
    if (A && A.ripple) U.uRippleC.value = A.ripple.c;
    U.uRippleA.value = AV.rippleA;
    if (A && A.wave) U.uWaveC.value = A.wave.c;
    U.uWaveA.value = AV.waveA;
    if (A && A.gaze) U.uGaze.value.set(A.gaze.x, A.gaze.y, A.gaze.z).normalize().applyQuaternion(qInv);
    U.uGazeAmt.value = AV.gazeAmt; U.uGazeLo.value = A && A.gazeLo ? A.gazeLo : .45;
    if (A && A.count) U.uCountC.value = A.count.c;
    U.uCountFade.value = AV.countFade;
    U.uSweep.value = P.sweep * g; U.uSweepT.value = sweepT;
    U.uRight.value.set(1, 0, 0).applyQuaternion(qInv);
    U.uUp.value.set(0, 1, 0).applyQuaternion(qInv);
    U.uFront.value.set(0, 0, 1).applyQuaternion(qInv);
    U.uGray.value = P.gray; U.uBright.value = P.bright * (1 - P.gray); U.uOpacity.value = P.op;
    U.uFlowT.value = flowT; U.uEnergy.value = P.energy; U.uSwellF.value = swell;
    halo.position.set(P.x, P.y + AV.lift, -.9);
    halo.scale.setScalar(P.scale * AV.mult * (1 + .04 * swell));
    haloU.uHalo.value = P.halo * (1 + .5 * swell); haloU.uFlowT.value = flowT;

    // juggling balls
    const B = A && A.balls;
    balls.forEach((b, i) => {
      const bu = b.material.uniforms;
      if (!B) { bu.uOpacity.value += (0 - bu.uOpacity.value) * ease(dt, .15); b.visible = bu.uOpacity.value > .01; return; }
      const jt = B.t, a = jt * 2.0 + i * 2.094, R = 1.55 * P.scale;
      let x = P.x + Math.cos(a) * R, y = P.y + Math.sin(a) * R * .5 + .15, op = 1;
      if (B.drop && i === 2 && jt > 6.3) {   // loop 3: one ball drops, then rolls back in
        const ft = jt - 6.3, a0 = 6.3 * 2.0 + i * 2.094;
        x = P.x + Math.cos(a0) * R + ft * .35; y = P.y + Math.sin(a0) * R * .5 + .15 - ft * ft * 3.2;
        op = clamp01(1 - ft * .7);
        if (jt > 7.8) { const back = smooth(7.8, 8.8, jt); x = P.x + 2.2 - back * 1.0; y = P.y - 1.1; op = back; }
      }
      if (jt < .6) op *= smooth(0, .6, jt);
      b.position.set(x, y, .6); b.visible = true; b.rotation.y += dt;
      bu.uOpacity.value = op; bu.uGray.value = P.gray; bu.uFlowT.value = flowT + i * 2.0;
    });

    // sparkles: burst from the orb's centre, drift down, fade (A.sparks.t = s since burst)
    const SP = A && A.sparks;
    if (SP && SP.t >= 0 && SP.t < 1.6) {
      const pos = sparks.geometry.attributes.position.array, al = sparks.geometry.attributes.aAlpha.array;
      const sc = P.scale * AV.mult, k = SP.t, n = Math.round(SPARK_N * (SP.n || 1));
      for (let i = 0; i < SPARK_N; i++) {
        const d = sparkDir[i], R = sc * d.r + k * 1.4;
        pos[i * 3] = P.x + d.x * R * .9; pos[i * 3 + 1] = P.y + AV.lift + d.y * R * .9 - k * k * .9; pos[i * 3 + 2] = .9 + d.z;
        al[i] = i < n ? clamp01(1 - k / 1.5) * smooth(0, .08, k) * g : 0;
      }
      sparks.geometry.attributes.position.needsUpdate = true; sparks.geometry.attributes.aAlpha.needsUpdate = true;
      sparks.visible = true;
    } else sparks.visible = false;

    // neural-net doodle (A.doodle = act progress u)
    const DU = A && A.doodle;
    if (DU != null) {
      const sc = P.scale * AV.mult, fade = 1 - smooth(.86, 1, DU);
      const np = nodePts.geometry.attributes.position.array, na = nodePts.geometry.attributes.aAlpha.array;
      const world = (n) => [P.x + n.x * sc, P.y + AV.lift + n.y * sc, 1.2 * sc];
      NODES.forEach((n, i) => {
        const w = world(n); np.set(w, i * 3);
        const on = smooth(.05 + n.L * .09, .13 + n.L * .09, DU);
        const pulse = Math.exp(-Math.pow((DU - (.66 + n.L * .06)) / .025, 2));
        na[i] = (on * .8 + .2 * pulse * on) * fade;
      });
      const ep = edgeGeo.attributes.position.array, ea = edgeGeo.attributes.aAlpha.array;
      EDGES.forEach(([i, j, L], e) => {
        const a = world(NODES[i]), b = world(NODES[j]), gr = smooth(.32 + L * .12, .5 + L * .12, DU);
        ep.set(a, e * 6); ep.set([a[0] + (b[0] - a[0]) * gr, a[1] + (b[1] - a[1]) * gr, a[2]], e * 6 + 3);
        const pulse = Math.exp(-Math.pow((DU - (.69 + L * .06)) / .03, 2));
        ea[e * 2] = ea[e * 2 + 1] = (.5 + .45 * pulse) * Math.min(1, gr * 4) * fade;
      });
      nodePts.geometry.attributes.position.needsUpdate = true; nodePts.geometry.attributes.aAlpha.needsUpdate = true;
      edgeGeo.attributes.position.needsUpdate = true; edgeGeo.attributes.aAlpha.needsUpdate = true;
      nodePts.visible = edges.visible = true;
    } else nodePts.visible = edges.visible = false;

    renderer.render(scene, camera);
    cpuMs += (performance.now() - c0 - cpuMs) * .05;   // JS + draw submit per frame (EMA)
    requestAnimationFrame(frame);
  }

  function resize(width, height, pixelRatio) {
    renderer.setPixelRatio(Math.min(2, pixelRatio || 1));
    PR.value = renderer.getPixelRatio() * width / 1920;
    renderer.setSize(width, height, false);
    camera.aspect = width / height; camera.updateProjectionMatrix();
  }
  resize(1920, 1080, 1);
  requestAnimationFrame(frame);

  // An act's extra turn is kept when it ends (a half-turn must not snap back).
  function stopAct() { act = null; rotY += lastExtra; lastExtra = 0; retarget(); }

  return {
    state(name) {
      if (!STATES[name]) name = 'idle';
      if (name === 'confused' && stateName !== 'confused') wobbleT0 = t;
      stateName = name;
      retarget();
    },
    get current() { return stateName; },
    showing(on) { showingOn = !!on; retarget(); },
    level(v, _source) { levelTarget = clamp01(+v || 0); levelAt = performance.now(); },
    talking(on) { talkingOn = !!on; },
    act(id, seconds, opts = {}) {
      const fn = ACTS[id];
      if (!fn || stateName === 'out' || stateName === 'lost') return false;
      const dur = Math.max(.5, +seconds || 5);
      act = { id, fn, t0: t, dur, o: { ...opts, dur } };
      return true;
    },
    // what_am_i: show the answer and make sure the shape stays up `hold` more seconds
    reveal(hold = 2.6) {
      if (!act) return;
      act.o.revealed = true;
      act.dur = Math.max(act.dur, (t - act.t0) + hold);
      act.o.dur = act.dur;
    },
    stopAct,
    get acting() { return act ? act.id : null; },
    reduced(on) { reducedOn = !!on; },
    resize,
    fps: () => Math.round(fpsVal),
    cpuMs: () => Math.round(cpuMs * 10) / 10,
  };
}
