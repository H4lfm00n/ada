# Kiosk UI spec v3.1: Ada, light theme, glass orb, screen mode now / voice next

> Owner: designer. Status: **v3.1** (2026-11-18, branch `task/des-mvp-fixes`). v3.1: the
> character is the **glass orb** ENG built (D64, D66), not a wireframe blob; the typed MVP
> runs in **screen mode** (PM 2026-11-18) with its own copy; no robot-arm idle act.
> Source: `claude/PHASE2-SPEC.md` sections 0, 2, 3, 4. Supersedes v2 (`docs/ui-spec-v2-dark.md`).
> Clickable mockup: `ui/mockups/ada.html`, built on the real `ui/blob.js` and `ui/cards.js`
> (no network; serve the repo root, e.g. `python3 -m http.server`, and open `/ui/mockups/ada.html`).
> Decisions DES43–DES50, DES52–DES57 in `docs/DECISIONS.md`. Content: `content/cards.yaml`,
> `content/idle_acts.yaml`, `content/voice.md`, `content/lines.yaml`.

## 1. Who is at the screen

People standing ~1 m from the mic and up to 2 m from the screen, in a loud room, often in
pairs. Some are deaf or hard of hearing, so everything Ada says is on screen.

Two output modes (config `output_mode`, DES54):

| | **Screen mode: the typed MVP (now)** | **Spoken mode (from M6a)** |
|---|---|---|
| Visitor | taps the talk button, types in the box | says "Hey Ada" or taps, then talks |
| Ada | captions only | voice + captions |
| Mic indicator | hidden (there is no mic) | shown |
| Mic-shaped icons (talk pill, card "Did you get it?" row) | chat bubble / none | mic |
| Status word while waiting for the visitor | none (the typing box is the cue) | LISTENING |
| Copy | plain keys in `content/ui_strings.yaml` | `*_spoken` keys |

In spoken mode typing is the hidden staff/debug input (`` ` `` key).

## 2. Layout (1920 × 1080 canvas; scale by `min(100vw/1920, 100vh/1080)`)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ [AI] Ada is an AI · chats saved as text on this computer · no audio or video  │ ① notice (screen mode)  ② mic: spoken mode only
│                                                                              │
│                              ╭──────╮                                        │
│                            glass orb  (r ≈ 260 px, center y ≈ 380)           │ ③ orb (hero)
│                              ╰──────╯                                        │
│                            LISTENING                                         │ ④ status word (some states)
│                                                                              │
│          Captions, 54 px, max 3 lines, words light up as she says them       │ ⑤ captions
│                                                                              │
│                  (  Tap here to ask Ada a question  )                         │ ⑥ talk button (screen) → typing box
└──────────────────────────────────────────────────────────────────────────────┘
Card shown: blob shrinks to 62 % and moves to the upper left, leaning right; captions move
left-aligned under it; the card (660 px wide) slides in from the right (spec 4).
```

- ⑥ is a real button (whole pill is the tap target, ≥ 96 px tall) and the line that tells
  people how to start. Tapping the orb also wakes Ada. Icon: a chat bubble in screen mode,
  a mic in spoken mode (a mic icon would promise voice that isn't there). In screen mode a tap turns the pill
  into the typing box (1100 px, same height, accent border); in spoken mode it starts listening.
- Mini card: when the visitor asks something else while a card is up, the card shrinks to a
  300 px tile in the top-right corner and stays until its timer ends (spec 2.2).

## 3. The orb (D64, D66: supersedes the wireframe blob)

Ada is a **violet glass orb with light flowing inside it**. Built in `ui/blob.js` (ENG); this
section is the design intent the engineer's build already matches.

- **Body:** a finely subdivided sphere, smoothly shaded, deformed in the vertex shader by slow,
  low-frequency simplex noise plus a breathing term. Silhouette is round (noise amplitude ≈
  0.026); never spiky, never a visible mesh.
- **Glass:** deep violet body (`#2E1065` → `#6D28D9`), a bright lavender rim and one crisp
  highlight.
- **Inner light:** three broad ribbons, pink `#F472B6`, sky `#60A5FA`, lavender `#C4B5FD`,
  winding through the orb at two depths (parallax), warped by slow noise.
- **Halo:** a soft colored glow behind the orb, hue drifting slowly.
- **Color rule:** these three hues live **only inside the orb**. All UI chrome keeps the one
  accent (section 8). PM approved the orb colors, 2026-11-18.
- **Smoothness rules (why the wireframe was dropped):** every phase is integrated, never
  `time × rate`; look parameters ease with ≈ 0.45 s time constant; audio drives one soft swell
  (≈ 0.25 s attack, 0.6 s release), never per word; no flicker, no strobing.

| State | Orb (energy of the light · flow speed · halo · shape) | Status word | Mic indicator (spoken mode) |
|---|---|---|---|
| Idle | calm light, slow flow, soft halo; breathing 5 s (±3 %); idle acts every 45–120 s | — | off |
| Waking | 0.5 s: brighter light, halo up, scale 1.05, rises ("looks up") | — | off→on |
| Listening (spoken) / Typing (screen) | brighter, slight swell with the mic level (spoken); calm steady glow while typing (screen) | LISTENING (spoken only) | on, red dot |
| Thinking | light flows fastest, slight shrink (0.95), a slow sweep of brightness | THINKING | off |
| Speaking / showing a reply | gentle swell + one slow wave with Ada's audio (spoken) or a steady gentle swell while captions appear (screen) | — | off (half-duplex) |
| Showing a card | shrink to 0.62, move upper-left, lean toward the card | — | per state |
| Confused / fallback | one gentle 1.2 s wobble-tilt, then back | — | off |
| Out of order | gray glass, no inner light, no halo, collapsed and still; "Ada is resting" | — | off |
| Connection lost | gray glass, faint light | CONNECTION LOST | off |

`prefers-reduced-motion`: motion at 35 %, no spins; acts marked `skip` in `idle_acts.yaml`
don't run. Budget: 60 fps on the kiosk display (check with `/ui/?debug=1`).

Idle acts (`content/idle_acts.yaml` v2): 19 acts besides breathing. `daydream_arm` is
**removed** (PM, no robot-arm teasing); `wireframe_wave` and `count_vertices` keep their
IDs but now read as a light wave and bands of light brightening.

## 4. Captions

- 54 px Inter 500 (46 px when a card is showing), `#16181D` on `#F7F6F2` (contrast ≈ 16:1).
- Words appear dimmed (18 %) and light up as TTS reaches them (engineer: word timings from
  Kokoro, or estimate from sentence duration). Max 3 lines; older sentences scroll up and fade.
- Captions show Ada's words only. The visitor's transcript is **not** shown (privacy, DES41).
- Captions = exactly what is spoken, minus `<card .../>` tags (`content/voice.md` S12).
- Screen mode: the same captions; words can appear all at once per sentence or at an estimated
  reading pace (no TTS timings). Room codes, times and public emails appear in written form
  ("LIB 260", "ai@sfsu.edu") because there is no voice to say them.

## 5. Cards

Catalog: `content/cards.yaml`. Types: `qr`, `directions`, `list`, `spotlight`, `checklist`,
`contact`, `fun`. All cards: white surface, 28 px radius, soft shadow, eyebrow (20 px caps,
accent), title 40 px, body 30 px, auto-hide progress bar at the bottom.

- **QR:** 380 px code generated locally from the catalog URL, black on white, quiet zone ≥ 4
  modules. Caption under it: the card's `qr_caption`, else "Point your phone camera at the code."
- **Placeholder QR cards (MVP, DES53):** both QR cards open SFSU's AI for Students page for now.
  The coming-soon note is in the card itself: title "Discord invite: coming soon" /
  "Proposal form: coming soon", and `qr_caption` "For now, scan for SFSU's AI for Students page."
  No extra UI needed.
- **campus_help:** red 3 px border and red eyebrow; no auto-hide; no idle acts while shown.
- Enter: slide from the right, 0.7 s ease-out. Exit: slide back. Never covers the captions.

## 6. Check-in loop (spec 2.2, server-run)

`show_card` → Ada says `say_before` → after `confirm_after_s` (6) asks `confirm` → status
word LISTENING (no wake word needed) → yes: hide + `on_yes` · no/one sec: keep, wait
`retry_after_s` (10), ask once more · something else: mini card + normal answer · timeout or
new visitor: hide quietly. In the mockup: Cards → "QR + check-in", then "Visitor says".

## 7. Copy (UI strings)

Source of truth: `content/ui_strings.yaml` v3. Plain keys = screen mode (ships now);
`*_spoken` keys replace them in spoken mode.

| Where | Screen mode (now) | Spoken mode (M6a+) |
|---|---|---|
| Notice | `AI` chip + "Ada is an AI and can make mistakes · Chats are saved as text on this computer · No audio or video" | `AI` chip + "Ada is an AI and can make mistakes · Listens only after "Hey Ada" or a tap · No audio recorded" |
| Talk button (idle) | **Tap here** to ask Ada a question | Say **"Hey Ada"** or tap to talk |
| Talk button (after tap) | Type your question below (then the typing box) | I'm listening… |
| Typing box placeholder | Type a question, then press Enter · then "Ask me something else" | (staff only) |
| Talk button (thinking/speaking, barge-in builds only) | — | Say **"stop"** to interrupt |
| Mic indicator | hidden | "Mic off" / "Listening" (red dot, pulsing) |
| Out of order | **Ada is resting** · Back soon. A lab member can help you in the meantime. | same |
| Still there? | Still there? I'll reset for the next visitor in 30 seconds. | same |
| QR caption | card `qr_caption`, else "Point your phone camera at the code." | same |

Why the screen notice changed: in the typed MVP there is no mic, so "listens only after
Hey Ada" would be untrue; the honest privacy line is what is actually kept (text, on this
computer) and what isn't (audio, video). It matches safety rule 9 in the prompt.

TODO(PM): kiosk sign wording (spec 1.4) is separate from the on-screen notice above.

## 8. Design tokens

```css
--bg:#F7F6F2; --surface:#FFFFFF; --ink:#16181D; --ink-2:#4A4F5A; --ink-3:#8A8F99;
--line:#E6E3DC; --accent:#7C3AED; --accent-soft:#F1EAFE; --danger:#B42318; --rest:#9AA0A8;
--font: Inter (400/500/600/700), system-ui fallback;
--t-caption:54px; --t-card-title:40px; --t-card-body:30px; --t-status:30px; --t-notice:20px;
--r-card:28px; --shadow:0 18px 50px rgba(22,24,29,.10); --ease:cubic-bezier(.2,.8,.2,1);
```
One accent only in the UI chrome (`--accent`, purple, contrast ≈5.4:1 on `--bg`); the orb's
inner pink/sky/lavender light is the single exception (section 3). Red is reserved for the mic
indicator and help card, and always paired with a word ("Listening", "Get help now").

## 9. Accessibility

Captions for every spoken word; status words + motion so no state relies on color;
tap target ≥ 96 px; reduced-motion variant; `aria-live` on captions and mic status; the
help card is readable without audio.

## 10. Events the UI needs from the server (additions to v2 section 9)

`state` {idle|waking|listening|thinking|speaking|confused|out_of_order}, `audio_level`
{source: mic|tts, 0–1, ~20 Hz}, `caption` {sentence, word_timings?}, `show_card` {id, data},
`card_mini` {id}, `hide_card` {id}, `idle_act` {id}, `wake` {source: word|tap}.
