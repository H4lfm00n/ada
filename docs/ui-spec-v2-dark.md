# Kiosk UI spec v2 (SUPERSEDED by docs/ui-spec.md v3, 2026-11-17; kept for history)

> Owner: designer. Status: **v2** for PM review, branch `task/des-kiosk-ui`.
> v2 (2026-11-16, PM feedback): the face is replaced by a single animated orb, and the
> transcript and chat bubbles are gone. The screen shows only what the bot says, as
> captions at the bottom. Decisions: DES32–DES39 (v1), DES40–DES42 (v2) in `docs/DECISIONS.md`.
> Clickable mockup: `ui/mockups/kiosk.html` (open in any browser, no server needed).
> The bot's name is `{{bot_name}}` everywhere; the UI gets it from the server (`hello`).

---

## 1. Who is at the screen

A big screen in the AI Commons (LIB 260), read from about **2 m away**, often by people
standing or walking past. Visitors type on a keyboard for now; voice comes later, so every
state has to work for typing **and** speaking. The screen is public, so it shows as little
as possible: the orb, what the bot says, and one line of typing.

---

## 2. Layout

Designed on a **1920 × 1080 landscape canvas**; scale everything by `100vh / 1080`.
There are only five elements, all centered on one vertical axis:

```
┌──────────────────────────────────────────────────────────────┐
│     AI · {{bot_name}} can make mistakes · Text log only …    │  ① notice (always)
│                                                              │
│                          ◉◉◉◉◉                               │
│                        ◉◉◉◉◉◉◉◉◉                             │  ② orb (r ≈ 195 px)
│                          ◉◉◉◉◉                               │
│                        THINKING                              │  ③ status word (only some states)
│                                                              │
│        You show it examples, and it slowly adjusts           │  ④ captions (bot only)
│        those connections until it gets good at a task.       │
│                                                              │
│              ───── Type a question and press Enter ─────     │  ⑤ input line
└──────────────────────────────────────────────────────────────┘
```

| # | Element | Position (1080p) | Notes |
|---|---|---|---|
| ① | **Notice** | top, centered, 28 px, muted | Never hidden. Text in 5.7. |
| ② | **Orb** | center x, y ≈ 330 px, radius 195 px | The only "character". Section 3. |
| ③ | **Status word** | under the orb, 28 px, uppercase, muted | Only for Listening, Thinking, Reconnecting, Out of order. Every other state is obvious from the captions. |
| ④ | **Captions** | bottom third, centered, max 1,400 px wide | **Only the bot's words.** Section 4. |
| ⑤ | **Input line** | bottom, 1,000 px wide, underline only, 36 px centered text | No box, no button. Hidden in Reconnecting and Out of order. |

**Not on screen:** the visitor's messages after sending, chat bubbles, a transcript,
history, buttons, a name tag.

---

## 3. The orb

One **filled circle** that moves like liquid light. Original design, no assets: it's drawn
on a `<canvas>` (the mockup's `frame()` function is the reference implementation).

How it's built:
1. **Shape:** a circle whose edge is pushed in and out by three slow sine waves
   (3, 5 and 2 lobes, drifting at different speeds). The amplitude is tiny (2–3% of the
   radius), so it reads as a circle that's alive, not a blob.
2. **Fill:** a dark core color, then three soft colored lights that drift inside it on
   slow looping paths, blended additively and clipped to the shape.
3. **Highlight:** a faint white glow at the top left, so it reads as a solid sphere.
4. **Glow:** a soft halo around it in the main light color.
5. **Breathing:** the radius swells ±2.5% on a ~6 s cycle in every state except out of order.

Every state sets **targets** (size, edge movement, speed, palette, glow); the orb eases
toward them over ~0.4 s, so changes never jump.

| State | Palette (lights) | Size | Movement |
|---|---|---|---|
| idle | teal, blue, violet | 100% | slow breathing, slow drift |
| listening | sky, blue, teal | 106% | brighter; **each keypress gives a small pulse** (voice: mic level) |
| thinking | violet, indigo, pink | 92% | lights swirl fast; a **ripple travels around the edge** |
| replying | teal, blue, violet | 102% | **swells and ripples with the words** while each sentence is "spoken" (voice: TTS audio level) |
| fallback | amber, orange, peach | 98% | one quick side-to-side shake, then calm |
| safety reply | teal, blue, violet | 100% | almost still: very slow drift, no pulses |
| reconnecting | coral, violet, blue | 85%, 75% opacity | slow dim pulse (0.5 Hz) |
| out of order | greys | 78%, 55% opacity | **no movement at all** |

The orb never has a face, eyes, or text inside it.

**Reduced motion** (`prefers-reduced-motion` or config `kiosk_reduced_motion`): the orb is
static (no breathing, drift, ripple or pulses); palette and size still change with state,
and the status word still shows. Nothing ever flashes faster than 3 times per second.

**Robot hook:** `action: wave` (first message of a session) → one gentle pulse now; the arm
later.

---

## 4. Captions

- **Only what the bot says.** The visitor's message is not displayed after it's sent.
- Sentences arrive one at a time from the server. The newest sentence is **56 px**, white.
  The one before it stays above at **40 px**, muted. Older ones disappear. At most two
  sentences on screen, so captions never reach the orb.
- Long replies (4+ sentences): the newest sentence drops to **44 px**.
- Centered, max ~45 characters per line at 56 px, 220 ms fade-up on arrival.
- After the reply ends, the last two sentences **stay** until the visitor starts typing, a
  new reply begins, or the session resets.
- Safety lines (`line:emergency`, `line:crisis_support`, `line:medical_legal`): the numbers
  (911, 988, phone numbers) are **bold**; the text is never changed.
- Muted captions (grey, not white) are for things the bot didn't "say" in a turn: the
  attract greeting, "Still there?", and the out-of-order line.
- Voice later: same captions; `speech_start` decides which sentence is current.

---

## 5. States and behavior

| State | Orb | Status word | Captions | Input |
|---|---|---|---|---|
| **idle** (attract) | idle | — | rotating greeting (muted), every 7 s | "Type a question and press Enter" |
| **listening** | listening | LISTENING | cleared | the visitor's text |
| **thinking** | thinking | THINKING | cleared | empty; Enter disabled |
| **replying** | replying | — | sentences as they arrive | Enter disabled until the turn ends |
| **ready** (between turns) | idle | — | last reply stays | "Ask me anything else" |
| **fallback** | fallback | — | the fallback line | "Try asking another way" |
| **reconnecting** | reconnecting | RECONNECTING | cleared | hidden |
| **out of order** | out of order | OUT OF ORDER | `out_of_order` line (muted) | hidden |

### 5.1 Idle (attract)
Rotating greeting from `idle_greetings` every 7 s, never the same twice in a row. Any key
press starts **listening** (the input always has focus).

### 5.2 Listening
Captions clear the moment the visitor starts typing, so nothing competes with their text.
If the input is emptied and nothing is typed for 10 s, back to idle (or ready, mid-session).

### 5.3 Thinking and replying
On Enter: the input clears, the orb switches to thinking. Captions appear with the first
sentence. If the visitor presses Enter during a turn, a small hint under the input says
"One sec, still answering"; the server also rejects it with `busy`.

### 5.4 Fallbacks and guards
The visitor's text is never shown on screen, so a blocked message needs no special
display (v1's "Message hidden" is gone). What remains:
- `source: line:guard_fallback` → fallback orb (amber + shake) and the line as captions.
- Other deflection lines (jailbreak, off-topic, insults…) → normal replying orb; the line
  carries the character.
- `reply_retracted` (output guard blocked a later sentence) → **clear that reply's
  captions**, then show the fallback line. A half reply never stays on screen.
- Safety lines → safety orb (calm, still), bold numbers.
- Connection lost > 3 s → reconnecting; > 30 s → the out-of-order look with the line "I'm
  having trouble connecting. Please ask a lab member." and status word CONNECTION LOST,
  so staff can tell it apart from the kill switch.

### 5.5 New visitor reset (inactivity)
The server owns the clock (`session_timeout_s`, 120 s, still TODO(PM)).
- 30 s before the timeout, a muted caption: "Still there? I'll reset for the next visitor
  in 30." The number counts down; it doesn't blink. Any key press cancels it.
- At the timeout: the server sends the goodbye line (shown as a normal reply for 4 s),
  then `session_reset`, then idle.
- Typing counts as activity (client sends `typing` at most every 5 s).
- **Esc** = start over (same goodbye → idle path; Esc twice in 2 s skips the goodbye).
  No visible button, to keep the screen clean (DES41).

### 5.6 Out of order (kill switch)
Immediate on `out_of_order {on: true}`, even mid-reply: captions replaced by the
out-of-order line, orb grey and still, input hidden. `on: false` → idle.

### 5.7 The notice (always visible)
> **AI** · {{bot_name}} can make mistakes · Text log only, no audio or video · Don't type personal info

One line, 28 px, muted, top center. Never hidden. It should come from config
(`kiosk_notice`) so it changes with the logging policy. At M6 (voice) it must change
(e.g. "Listens only when you press talk · No audio recorded").

---

## 6. Copy (all UI strings)

Proposed for `content/ui_strings.yaml` (designer-owned data, DES37).

| Key | Text |
|---|---|
| `notice` | (5.7) |
| `status_listening` | Listening |
| `status_thinking` | Thinking |
| `status_reconnecting` | Reconnecting |
| `status_out_of_order` | Out of order |
| `status_connection_lost` | Connection lost |
| `placeholder_idle` | Type a question and press Enter |
| `placeholder_ready` | Ask me anything else |
| `placeholder_fallback` | Try asking another way |
| `hint_busy` | One sec, still answering |
| `still_there` | Still there? I'll reset for the next visitor in {{seconds}}. |
| `connection_lost_line` | I'm having trouble connecting. Please ask a lab member. |

---

## 7. Design tokens

All text colors are ≥ 7:1 on `--bg` (WCAG AAA, because of the distance).

```css
:root {
  --u: calc(100vh / 1080);            /* 1 unit = 1 px on a 1080p screen */

  /* color */
  --bg:         #06080C;
  --text:       #F3F6FA;              /* captions, 18:1 */
  --text-muted: #B4C0CE;              /* previous sentence, notice, status: 10.5:1 */
  --line:       #2A3446;              /* input underline (decorative) */

  /* type: Atkinson Hyperlegible, bundled in ui/fonts (no network at runtime) */
  --font: "Atkinson Hyperlegible", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --fs-caption:      calc(56 * var(--u));
  --fs-caption-prev: calc(40 * var(--u));
  --fs-caption-long: calc(44 * var(--u));
  --fs-input:        calc(36 * var(--u));
  --fs-small:        calc(28 * var(--u));   /* notice, status word: the minimum */
  --lh-caption: 1.22;

  /* layout */
  --orb-cy: calc(330 * var(--u));
  --orb-r:  calc(195 * var(--u));
  --status-top: calc(590 * var(--u));
  --captions-bottom: calc(150 * var(--u));
  --captions-height: calc(330 * var(--u));
  --captions-max-w: calc(1400 * var(--u));
  --input-w: calc(1000 * var(--u));
  --input-bottom: calc(44 * var(--u));

  /* motion */
  --t-fast: 150ms;
  --t-med:  400ms;   /* orb easing between states */
  --t-caption: 220ms;
}
```

Orb palettes (`[core, light A, light B, light C]`; decorative, never the only signal).
Canvas code can't read CSS lists, so these live in a small JS module next to the orb code:

| Palette | Core | Light A | Light B | Light C |
|---|---|---|---|---|
| calm | `#0E1A2B` | `#3EE6D2` | `#5B8CFF` | `#B48CFF` |
| listen | `#0E1A2E` | `#6FD3FF` | `#5B8CFF` | `#3EE6D2` |
| think | `#140F2C` | `#B48CFF` | `#6E7CFF` | `#FF8FD8` |
| warn | `#2A1C0B` | `#FFD166` | `#FF9A5A` | `#FFB4A2` |
| err | `#22121A` | `#FF9A8B` | `#A86BFF` | `#5B8CFF` |
| off | `#15181D` | `#5A6472` | `#4A525E` | `#3A414B` |

---

## 8. Accessibility

- **Contrast:** captions 18:1, muted text 10.5:1.
- **Nothing by color alone.** The orb's color is never the only signal. Each state also
  has a different **movement** (still, breathing, pulsing, rippling, shaking), and the
  states where nothing else on screen changes get a **status word** (Listening, Thinking,
  Reconnecting, Out of order). Fallbacks are said in the caption itself.
- **Large text:** 28 px minimum; captions 56 px.
- **Captions for everything it says**, now and after voice arrives.
- **Motion:** reduced-motion mode (section 3); no flashing over 3 per second.
- **Keyboard only:** input always focused; Esc = start over.
- **Screen readers:** captions `aria-live="polite"`; status word `role="status"`; orb
  `aria-hidden`.

---

## 9. What the engineer needs from the server (websocket)

One websocket per screen at `/ws`, JSON `{ "type": ..., ... }`. The server owns sessions,
turns, guards, and the kill switch; the client owns idle ↔ listening and visual timers.
v2 needs fewer events than v1: since the visitor's text isn't shown, `turn_accepted` and
`input_blocked` are dropped.

### 9.1 Client → server

| type | fields | when |
|---|---|---|
| `message` | `text`, `client_msg_id` | Enter |
| `typing` | — | while typing, at most every 5 s (keeps the session alive) |
| `reset` | `immediate: bool` | Esc (`immediate` = Esc twice) |
| `pong` | `t` | reply to `ping` |

### 9.2 Server → client

| type | fields | UI does |
|---|---|---|
| `hello` | `bot_name`, `session_timeout_s`, `warn_before_s` (30), `out_of_order`, `out_of_order_line?`, `reduced_motion`, `notice`, `ui_strings`, `idle_greetings` | set up; idle or out of order. Every (re)connect. |
| `turn_start` | `turn_id`, `client_msg_id` | thinking; clear captions |
| `sentence` | `turn_id`, `index`, `text`, `source` (`"model"` / `"line:<category>"`) | replying; add caption; `source` picks the orb (5.4) |
| `reply_retracted` | `turn_id` | clear this turn's captions |
| `action` | `name` (`"wave"`) | orb pulse; robot later |
| `turn_end` | `turn_id`, `source` | ready (or fallback) |
| `busy` | `client_msg_id` | show "One sec, still answering" |
| `session_warning` | `seconds_left` | "Still there?" caption |
| `session_reset` | `reason`: `timeout` / `farewell` / `visitor` / `admin` | after the goodbye: idle |
| `out_of_order` | `on`, `line?` | enter/leave out of order immediately |
| `ping` | `t` | answer `pong`; 2 missed (20 s) = connection lost |

Wiring notes:
- `sentence` = `OutputSink.on_sentence`; `action` = `ActionHandler.on_action`;
  `source` = `TurnResult.source`; `out_of_order` = the kill-switch check.
- `sentence` is sent **only after** the output guard passes it (brief rule 2).
  `reply_retracted` is the only new mid-turn emit the pipeline needs.
- The server sends the goodbye line before `session_reset`; the UI never picks bot lines
  itself, except idle greetings.
- The UI keeps nothing in browser storage. Reload = idle.

### 9.3 Reserved for voice (M6) and the robot (M7)

| type | purpose |
|---|---|
| `listen_start` / `listen_end` | push-to-talk / wake word → listening |
| `audio_level` | 0–1 at ≤ 30/s: drives the orb in listening (mic) and replying (TTS) |
| `partial_transcript` | live speech-to-text, shown in the input line |
| `speech_start` / `speech_end` | `turn_id`, `index`: which caption is current |

The orb is built for this: listening and replying already react to a level, and voice
just swaps the fake level for the real one.

---

## 10. Open questions for the PM

1. **Name.** Config said the old working name (a film character's name); now Ada; it appears in greetings and the
   notice. The mockup's switcher defaults to **Ada** as a placeholder, not a decision.
2. **Visitor's text.** v2 hides it after sending, as asked. Should it show briefly (small,
   muted, while thinking) so a group knows what's being answered? Recommendation: no.
3. **Touch or keyboard only?** Spec assumes a keyboard (Esc = start over, no buttons).
4. **Screen size and orientation?** Spec assumes landscape 1080p+.
5. **Notice:** OK as one line? Retention (30 days) and who reads logs are no longer on
   screen; say if you want them back.
6. **`session_timeout_s: 120`** with a 30 s warning: OK?
