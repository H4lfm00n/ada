# Kiosk UI spec v3: Ada, light theme, voice-first (M5a design half)

> Owner: designer. Status: **v3** for PM review, branch `task/des-ada-phase2` (2026-11-17).
> Source: `claude/PHASE2-SPEC.md` sections 0, 2, 3, 4. Supersedes v2 (`docs/ui-spec-v2-dark.md`).
> Clickable mockup: `ui/mockups/ada.html` (Three.js r128 from cdnjs; opens in any browser).
> Decisions DES43–DES50 in `docs/DECISIONS.md`. Content: `content/cards.yaml`,
> `content/idle_acts.yaml`, `content/voice.md`, `content/lines.yaml`.

## 1. Who is at the screen

People standing ~1 m from the mic and up to 2 m from the screen, in a loud room, often in
pairs. They **talk**; they don't type. Some are deaf or hard of hearing, so every spoken
word is captioned. Typing exists only as a hidden staff/debug input (`` ` `` key, not shown).

## 2. Layout (1920 × 1080 canvas; scale by `min(100vw/1920, 100vh/1080)`)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ [AI] Ada is an AI · listens only after "Hey Ada" or a tap · no audio recorded  ● Mic │ ① notice   ② mic indicator
│                                                                              │
│                              ╱╲╱╲╱╲                                          │
│                            wireframe blob  (r ≈ 260 px, center y ≈ 380)      │ ③ blob (hero)
│                              ╲╱╲╱╲╱                                          │
│                            LISTENING                                         │ ④ status word (some states)
│                                                                              │
│          Captions, 54 px, max 3 lines, words light up as she says them       │ ⑤ captions
│                                                                              │
│                  ( 🎙  Say "Hey Ada" or tap to talk )                         │ ⑥ talk button / status line
└──────────────────────────────────────────────────────────────────────────────┘
Card shown: blob shrinks to 62 % and moves to the upper left, leaning right; captions move
left-aligned under it; the card (660 px wide) slides in from the right (spec 4).
```

- ⑥ is a real button (whole pill is the tap target, ≥ 96 px tall) and the line that tells
  people how to talk. Tapping the blob also wakes Ada.
- Mini card: when the visitor asks something else while a card is up, the card shrinks to a
  300 px tile in the top-right corner and stays until its timer ends (spec 2.2).

## 3. The blob

Three.js wireframe icosphere (detail 5), `MeshBasicMaterial({wireframe:true})`, accent color,
55–85 % opacity, plus a faint inner core (detail 2, 12 %) for depth. Each vertex is pushed
along its normal by 3D noise (simplex in production; the mockup uses summed sines) plus a
breathing term. Parameters are eased toward per-state targets every frame (no hard cuts).

| State | Motion (targets) | Status word | Mic indicator |
|---|---|---|---|
| Idle | breathe 5 s cycle (±3 %), low noise, slow spin; idle acts every 45–120 s | — | off |
| Waking | 0.5 s: brighten, scale 1.08, rise ("look up"), faster spin | — | off→on |
| Listening | soft swell follows the **visitor's mic level** through the same envelope | LISTENING | **on, red dot** |
| Thinking | slow-but-steady spin (1.4 rad/s), mesh tightens (scale 0.94), high-freq shimmer | THINKING | off |
| Speaking | **gentle swell + one slow wave** follows Ada's TTS level through a soft envelope (≈0.25 s attack, 0.6 s release). Never per-word jitter. | — | off (half-duplex) / on (barge-in builds) |
| Showing a card | shrink to 0.62, move upper-left, lean toward card (tilt −0.12 rad) | — | per state |
| Confused / fallback | quick wobble-tilt (±0.15 rad, 0.8 s), then back to idle | — | off |
| Out of order | gray, still, collapsed (y-scale 0.55), "Ada is resting" message, mic off, audio muted | — | off |

`prefers-reduced-motion`: noise and breathing at 35 %, no spins; acts marked `skip` in
`idle_acts.yaml` don't run. Budget: 60 fps on the kiosk; one draw call per mesh; update
positions on the CPU (≈2.5k vertices) or move the displacement to a vertex shader if needed.

## 4. Captions

- 54 px Inter 500 (46 px when a card is showing), `#16181D` on `#F7F6F2` (contrast ≈ 16:1).
- Words appear dimmed (18 %) and light up as TTS reaches them (engineer: word timings from
  Kokoro, or estimate from sentence duration). Max 3 lines; older sentences scroll up and fade.
- Captions show Ada's words only. The visitor's transcript is **not** shown (privacy, DES41).
- Captions = exactly what is spoken, minus `<card .../>` tags (`content/voice.md` S12).

## 5. Cards

Catalog: `content/cards.yaml`. Types: `qr`, `directions`, `list`, `spotlight`, `checklist`,
`contact`, `fun`. All cards: white surface, 28 px radius, soft shadow, eyebrow (20 px caps,
accent), title 40 px, body 30 px, auto-hide progress bar at the bottom.

- **QR:** 380 px code generated locally from the catalog URL, black on white, quiet zone ≥ 4
  modules. Caption under it: "Point your phone camera at the code."
- **campus_help:** red 3 px border and red eyebrow; no auto-hide; no idle acts while shown.
- Enter: slide from the right, 0.7 s ease-out. Exit: slide back. Never covers the captions.

## 6. Check-in loop (spec 2.2, server-run)

`show_card` → Ada says `say_before` → after `confirm_after_s` (6) asks `confirm` → status
word LISTENING (no wake word needed) → yes: hide + `on_yes` · no/one sec: keep, wait
`retry_after_s` (10), ask once more · something else: mini card + normal answer · timeout or
new visitor: hide quietly. In the mockup: Cards → "QR + check-in", then "Visitor says".

## 7. Copy (UI strings)

| Where | Text |
|---|---|
| Notice | `AI` chip + "Ada is an AI and can make mistakes · Listens only after "Hey Ada" or a tap · No audio recorded" |
| Talk button (idle) | Say **"Hey Ada"** or tap to talk |
| Talk button (listening) | I'm listening… |
| Talk button (thinking/speaking, barge-in builds only) | Say **"stop"** to interrupt |
| Mic indicator | "Mic off" / "Listening" (red dot, pulsing) |
| Out of order | **Ada is resting** · Back soon. A lab member can help you in the meantime. |
| QR caption | Point your phone camera at the code. |

TODO(PM): kiosk sign wording (spec 1.4) is separate from the on-screen notice above.

## 8. Design tokens

```css
--bg:#F7F6F2; --surface:#FFFFFF; --ink:#16181D; --ink-2:#4A4F5A; --ink-3:#8A8F99;
--line:#E6E3DC; --accent:#7C3AED; --accent-soft:#F1EAFE; --danger:#B42318; --rest:#9AA0A8;
--font: Inter (400/500/600/700), system-ui fallback;
--t-caption:54px; --t-card-title:40px; --t-card-body:30px; --t-status:30px; --t-notice:20px;
--r-card:28px; --shadow:0 18px 50px rgba(22,24,29,.10); --ease:cubic-bezier(.2,.8,.2,1);
```
One accent only (`--accent`, purple, contrast ≈5.4:1 on `--bg`). Red is reserved for the mic
indicator and help card, and always paired with a word ("Listening", "Get help now").

## 9. Accessibility

Captions for every spoken word; status words + motion so no state relies on color;
tap target ≥ 96 px; reduced-motion variant; `aria-live` on captions and mic status; the
help card is readable without audio.

## 10. Events the UI needs from the server (additions to v2 section 9)

`state` {idle|waking|listening|thinking|speaking|confused|out_of_order}, `audio_level`
{source: mic|tts, 0–1, ~20 Hz}, `caption` {sentence, word_timings?}, `show_card` {id, data},
`card_mini` {id}, `hide_card` {id}, `idle_act` {id}, `wake` {source: word|tap}.
