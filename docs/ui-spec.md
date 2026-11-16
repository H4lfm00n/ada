# Kiosk UI spec (M5, design half)

> Owner: designer. Status: v1 for PM review, branch `task/des-kiosk-ui`.
> Clickable mockup: `ui/mockups/kiosk.html` (open it in any browser, no server needed).
> Decisions: DES32–DES39 in `docs/DECISIONS.md`.
> The bot's name is `{{bot_name}}` everywhere; the UI gets it from the server (`hello` event).

---

## 1. Who is at the screen

The screen hangs in the AI Commons (LIB 260). People read it from about **2 m away**, often
standing, often in pairs, sometimes walking past. They type on a keyboard in front of it.
Voice comes later, so every state below has to make sense for typing **and** speaking.

Three things follow from that:
1. **Big, short, high-contrast text.** The smallest text on screen is 28 px at 1080p.
2. **One focal point at a time.** In each state, one element is clearly "the thing to look at".
3. **It's a public screen.** Whatever the visitor types might be read by the whole room, so
   the UI hides visitor messages that the guards catch (section 5.4).

---

## 2. Layout

Designed on a **1920 × 1080 landscape canvas**. Every pixel value in this doc is at that
size; scale everything by `100vh / 1080` (the tokens in section 7 do this for you).

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ① AI notice bar (always visible)                                [Start over] │
├───────────────────────────┬──────────────────────────────────────────────────┤
│                           │                                                  │
│      ② Face               │      ④ Conversation area                         │
│      (420 px)             │         attract: big greeting + "Try asking"     │
│                           │         chat:    transcript, newest at bottom    │
│      {{bot_name}}         │                  current reply as large captions │
│      AI Lab robot helper  │                                                  │
│                           │                                                  │
│   ③ Status line           │                                                  │
│   [shape] Label           │                                                  │
├───────────────────────────┴──────────────────────────────────────────────────┤
│ ⑤ Input: "Type a question and press Enter"                         [Enter ↵] │
└──────────────────────────────────────────────────────────────────────────────┘
```

| # | Element | Size / position | Notes |
|---|---|---|---|
| ① | **AI notice bar** | full width, 104 px tall (two lines at 28 px), top | Never hidden, in any state. Text in 5.6. Holds a **Start over** button on the right (also the `Esc` key). |
| ② | **Face** | left column (620 px wide), face 420 × 420 px | Original robot face (section 3). Name tag under it: `{{bot_name}}` (40 px bold) + "AI Lab robot helper" (28 px). |
| ③ | **Status line** | under the name tag | Shape icon + text label + optional hint. Never color alone (section 8). |
| ④ | **Conversation area** | right column (1300 px wide) | Attract content in idle; transcript + captions otherwise. No scrolling: old turns fade off the top. |
| ⑤ | **Input** | full width, 120 px tall, bottom | 40 px text. Placeholder changes per state. Hidden in out of order. |

### 2.1 Transcript rules

- Newest at the bottom. Show at most the **last 3 exchanges**; older ones fade out under a
  140 px gradient mask at the top of the column, and turns older than the last one are
  shown at 55% opacity.
- Visitor messages: right-aligned, labeled **"You"**, 36 px, muted text, outlined bubble.
- Bot replies: left-aligned, labeled **`{{bot_name}}`**, filled bubble.
- Who said what is shown by **label + alignment + bubble style**, never by color only.
- The **reply in progress** is shown as captions (2.2), not a bubble. When the reply ends,
  it collapses into a normal bubble at 36 px.

### 2.2 Captions (the reply in progress)

- Sentences arrive one at a time from the server (D9: whole sentences, not tokens).
- Each new sentence fades in (180 ms) at caption size; earlier sentences of the same reply
  stay visible above it, dimmed to the muted color.
- Caption size: **56 px** for replies of 1–3 sentences, **44 px** from the 4th sentence on
  (long replies, V2 in the persona allows up to 8). The switch happens once per reply, never
  back and forth.
- Max line length ~45 characters at 56 px. Left-aligned, never justified, never centered.
- Voice later: captions stay exactly the same; the `speech_*` events (section 9) decide
  which sentence is "current" instead of arrival time.

---

## 3. The face

An original, simple robot face built from five shapes so the engineer can animate it with
CSS or SVG and no image assets. Not based on any film, show, or game robot.

```
              ●            ← lamp (status light on a short stem)
              │
      ╭──────────────╮
      │  ▮        ▮  │     ← eyes: two rounded rectangles
      │              │
      │   ▪ ▪ ▪ ▪ ▪  │     ← mouth: five small bars (doubles as a level meter)
      ╰──────────────╯     ← head: a rounded "screen" with a thin bezel
   ( ring )                ← thinking ring around the head (only while thinking)
```

| Part | Shape | Default |
|---|---|---|
| `head` | rounded square, radius 30% | surface-2 fill, 6 px bezel in `--line` |
| `eyeL`, `eyeR` | rounded rects 56 × 96 px | `--text` fill |
| `mouth` | 5 bars, 14 px wide, 8–40 px tall | `--accent` |
| `lamp` | 28 px circle on a stem | state color + state shape (8) |
| `ring` | circle around the head, 2 arcs | thinking only |

### 3.1 Expressions per state

| State | Eyes | Mouth | Head / lamp | Motion |
|---|---|---|---|---|
| idle | normal | small, still | lamp steady | blink every 4–7 s (random), glance left/right every ~9 s, float 6 px over 6 s |
| listening | 10% taller, looking down toward the input | bars react to each keypress (voice: mic level) | lamp steady | small hop when the visitor starts typing |
| thinking | look up and to the side, slightly narrowed | flat line | ring with two arcs spins (1.4 s/turn) | the "fans spinning up" from the persona |
| replying | relaxed (slightly squinted) | bars bounce for ~0.8 s each time a sentence lands | lamp steady | none besides the mouth |
| fallback | one eye bigger than the other | wavy | head tilts 8° | one wobble, then still |
| safety reply | normal, steady | flat, calm | upright | **no** bounce, no wobble, slow blink only |
| error (connection) | hollow outlines | hidden | lamp pulses 1 Hz | slow |
| out of order | flat lines (closed) | hidden | whole face at 40% opacity, lamp off | none |

`action: wave` (sent on a visitor's first message, robot hook): the head tilts left-right
twice and the lamp bobs (600 ms). Later this event also goes to the arm.

**Reduced motion** (`prefers-reduced-motion` or the config flag `kiosk_reduced_motion`):
no float, no spinning, no bounce. State changes become 150 ms crossfades; the thinking ring
is shown static with the status label doing the work. Nothing ever flashes faster than
3 times per second in any mode.

---

## 4. States

Six states, plus two variants. The state shown is the **pairing of face + status line +
conversation area + input**, so it's never conveyed by any one of them alone.

| State | Status line (shape + label) | Conversation area | Input placeholder | Input |
|---|---|---|---|---|
| **idle** (attract) | ● circle · "Ready. Type a question to say hi." | big rotating greeting + 3 example questions | "Type a question and press Enter" | enabled |
| **listening** | ◆ diamond · "Listening…" | transcript (or attract, dimmed, if first message) | — (the visitor's text) | enabled |
| **thinking** | ◐ spinner · "Thinking…" | transcript + the visitor's message just sent | "Still thinking. You can type your next question." | enabled, Enter disabled |
| **replying** | ▶ triangle · "Replying" | transcript + captions | "Type your next question…" | enabled, Enter disabled |
| **error / fallback** | ▲ warning triangle · see 4.5 | see 4.5 | per variant | per variant |
| **out of order** | ■ square · "Out of order" | out-of-order card | — | hidden |

### 4.1 Idle (attract mode)

- Shown when there's no session: on startup, after a reset, or after a timeout.
- **Greeting**: one line from `lines.yaml` → `idle_greetings`, 88 px, rotating every **7 s**
  with a 400 ms crossfade. Never the same line twice in a row.
- **Try asking:** three example questions (36 px) under the greeting, rotating as a set
  every **21 s** (every third greeting). Examples come from a new list the designer owns,
  `ui_examples` (proposed for `content/lines.yaml`, see DES36). They are plain text, not
  buttons, until we know whether the screen is touch.
- The face is in its idle loop. The transcript is empty.
- Any key press moves to **listening** and starts typing into the input (the input always
  has focus in kiosk mode).

### 4.2 Listening / typing

- Entered when the visitor types (focus + first character) or, later, when the wake word
  or push-to-talk fires.
- Status: "Listening…". Voice later: same label; the mouth bars show mic level.
- If this is the first message of a session, the attract content dims to 30% and stays put
  until the message is sent, so the example questions remain readable while typing.
- If the visitor clears the input and stops typing for **10 s**, go back to idle (no session
  is created until a message is sent).

### 4.3 Thinking

- Entered the moment a message is sent. The visitor's message appears immediately in the
  transcript as a **pending** bubble (outlined, 70% opacity) until the server confirms it
  passed the input guard (`turn_accepted`) or hides it (`input_blocked`).
- Status: "Thinking…". After **4 s** without a sentence, the hint "Big question, still
  working on it" appears under the label (no change of state). The server's own timeouts
  decide when a fallback replaces it.
- The visitor may type their next question, but Enter is disabled until the turn ends; the
  hint "I'm still answering, one sec" shows if they press it.

### 4.4 Replying

- Entered on the first `sentence` event of the turn.
- Captions per 2.2. Mouth bounces per sentence.
- On `turn_end`: the captions collapse into a bubble, status goes to "Ready. Ask me
  anything else." (● circle), Enter is enabled again. That ready-in-a-session state uses
  idle's status shape but keeps the transcript; it isn't a separate state.

### 4.5 Error / fallback (two variants)

**Variant A, fallback (in character).** Any reply whose `source` is `line:guard_fallback`,
`line:jailbreak`, `line:unsafe_request`, `line:off_topic`, etc. is still a normal reply: it
shows as captions like any other. What changes is the face:
- `line:guard_fallback`: fallback expression (tilt, uneven eyes), status ▲ "Couldn't answer
  that one. Try asking another way."
- Other deflection lines (jailbreak, insults, off-topic, …): normal replying face; the line
  itself carries the character. No warning status: from the visitor's side it's just an answer.
- If the output guard blocked a reply **after some sentences were already shown**, those
  sentences are **removed** (`reply_retracted`), then the fallback line shows. A blocked reply
  may have started with a harmless lead-in ("Sure, here's how…"), and we don't leave half of
  it on a public screen.

**Variant B, error (system).** The websocket drops or the server says it can't take
messages.
- 0–3 s: nothing visible (brief blips are normal).
- After 3 s: error face, status ▲ "Reconnecting…", input disabled with placeholder
  "One moment, I'm reconnecting." The transcript stays.
- After 30 s: show the out-of-order card with the line "I'm having trouble connecting.
  Please ask a lab member." and the small text "Connection lost" so staff can tell this
  apart from the kill switch.
- On reconnect: if the server says the session is gone, reset to idle; otherwise resume.

### 4.6 Out of order (kill switch)

- Entered on `out_of_order {on: true}` at any time, even mid-reply. The reply in progress is
  cleared immediately (the server has stopped it anyway).
- Out-of-order card replaces the conversation area: ■ "Out of order" (64 px) + one line from
  `out_of_order` (44 px), chosen by the server. Face: closed eyes, dimmed, still.
- Input is hidden. The AI notice bar stays. Start over is hidden.
- `out_of_order {on: false}` returns to idle (fresh session, attract mode).

### 4.7 Variant: safety replies (emergency, crisis)

Replies with `source` `line:emergency`, `line:crisis_support`, or `line:medical_legal`:
- Calm face (3.1), no wobble or bounce, no running joke (they're canned and joke-free).
- The phone numbers in the line (911, 988, …) are drawn **bold** at caption size. The
  engineer finds them with a simple digits pattern; the text itself is never changed.
- The visitor's own message is **hidden** (5.4): someone typing about a crisis shouldn't
  have their words on a big screen.
- These replies are **not** dimmed or collapsed when the turn ends; they stay at caption
  size until the visitor sends another message or the session ends.

### 4.8 Variant: "Still there?" (session about to reset)

See 5.1.

---

## 5. Behavior

### 5.1 New visitor = fresh screen (inactivity reset)

Server owns the clock (`session_timeout_s`, now 120 s, still TODO(PM) to confirm). The UI
mirrors it from `hello`.

| Time since last activity | What the screen does |
|---|---|
| 0 – (timeout − 30 s) | Normal. |
| timeout − 30 s | A banner slides in above the input: "Still there? I'll reset for the next visitor in **30**." Counts down each second (the number is the only thing that changes; no flashing). Any key press hides it and counts as activity. |
| timeout | Server sends the goodbye line (`line:goodbye`) as a normal reply, then `session_reset`. The goodbye stays 4 s, then everything fades (600 ms) to idle. |

"Activity" = a sent message **or** typing. The client sends `typing` at most once every
5 s while keys are pressed, so someone composing a long question doesn't get reset.
While the bot is thinking or replying, the clock is paused (server side).

Explicit resets go through the same goodbye → fade path:
- **Start over** button or `Esc`: client sends `reset`. (Esc twice within 2 s skips the
  goodbye and resets immediately, for staff.)
- Visitor types a farewell ("bye"): already handled by the pipeline.

After any reset, nothing from the previous visitor stays on screen: transcript cleared,
input cleared, "Still there?" banner gone.

### 5.2 How long things stay on screen

| Thing | Stays |
|---|---|
| Idle greeting | 7 s each |
| Example question set | 21 s each |
| A finished reply | until it scrolls off (3 exchanges) or the session ends |
| Safety reply (4.7) | at caption size until the next message or session end |
| Goodbye line | 4 s, then fade to idle |
| Thinking hint | from 4 s until the first sentence |
| "Still there?" banner | the last 30 s before timeout |
| Out-of-order card | until the kill switch is turned off |

### 5.3 Input

- Max length shown as a soft counter from 1,000 characters ("200 left"), hard stop at
  1,200 (the server's `too_long` limit). The counter is text, not just a color change.
- Enter sends; Shift+Enter does nothing (single-line questions keep it simple).
- Empty or whitespace-only messages are not sent.
- The input never shows autocomplete or suggestions from previous visitors.

### 5.4 Guard outcomes on screen

| Server says | Visitor's message | Bot's reply | Face / status |
|---|---|---|---|
| `turn_accepted` | pending → normal bubble | model reply as captions | normal |
| `input_blocked` (any category) | replaced by *"Message hidden"* (italic, muted) | the canned line, as captions | per category: fallback face for `guard_fallback`, calm face for safety lines, normal otherwise |
| `reply_retracted` (output guard) | stays as sent | partial sentences removed, then the canned line | fallback face, ▲ status |
| `line:emergency` / `crisis_support` / `medical_legal` | hidden, even if the input guard didn't block it | canned line, pinned (4.7) | calm |
| guard error / timeout | stays | `guard_fallback` line | fallback face |

Why hide blocked messages: they're the ones most likely to contain a slur, someone's phone
number, or something personal, and they'd otherwise be shown 60 px tall to the whole room.

### 5.5 Busy and double sends

One turn at a time per session. While a turn is open, Enter is disabled on the client, and
the server rejects extra messages with `busy` (the client shows the "still answering" hint).

### 5.6 The AI notice (always visible)

Bar text (28 px, muted, with a small "AI" badge that's text, not an icon):

> **AI** · {{bot_name}} is an AI and can make mistakes. This screen keeps a **text log
> only** (no audio, no video) on this computer for 30 days. Please don't type personal info.

- Never hidden, never smaller than 28 px, never under 7:1 contrast.
- When voice arrives, the wording must change (it will listen); that's a PM + designer
  decision at M6, flagged here so nobody forgets. Proposed M6 text: "…keeps a **text log
  only**. It listens only after you press the talk button, and doesn't record audio."
- "30 days" comes from D43. If retention changes, this string changes (it should come from
  config, `kiosk_notice`, so it can't drift).

---

## 6. Copy (all UI strings)

Proposed to live in `content/ui_strings.yaml` (designer-owned data, DES37) so they can be
edited without touching the UI code. `{{bot_name}}` is filled at runtime.

| Key | Text |
|---|---|
| `notice` | (5.6) |
| `name_subtitle` | AI Lab robot helper |
| `status_idle` | Ready. Type a question to say hi. |
| `status_ready` | Ready. Ask me anything else. |
| `status_listening` | Listening… |
| `status_thinking` | Thinking… |
| `hint_thinking_slow` | Big question, still working on it |
| `status_replying` | Replying |
| `status_fallback` | Couldn't answer that one. Try asking another way. |
| `status_reconnecting` | Reconnecting… |
| `status_out_of_order` | Out of order |
| `placeholder_idle` | Type a question and press Enter |
| `placeholder_busy` | Still thinking. You can type your next question. |
| `placeholder_reply` | Type your next question… |
| `placeholder_reconnecting` | One moment, I'm reconnecting. |
| `hint_busy` | I'm still answering, one sec |
| `try_asking` | Try asking |
| `still_there` | Still there? I'll reset for the next visitor in {{seconds}}. |
| `message_hidden` | Message hidden |
| `start_over` | Start over |
| `chars_left` | {{n}} left |
| `connection_lost_line` | I'm having trouble connecting. Please ask a lab member. |
| `connection_lost_small` | Connection lost |

Proposed `ui_examples` (questions that the real docs answer, checked against `lab_docs/`):
"What is the AI Lab?", "How do I join?", "Where do you meet?", "What equipment can I use?",
"What's a neural network?", "Can I 3D print overnight?", "What's the difference between AI
and machine learning?", "Who can I ask about the lab?", "What's a GB10?"

---

## 7. Design tokens

Dark is the only theme for v1. Colors are checked against `--bg`, `--surface`, and
`--surface-2`; every color used for text is ≥ 7:1 on all three (WCAG AAA, because of
the distance). `--dim` is for decoration only, never text.

```css
:root {
  /* scale: 1 unit = 1 px on a 1080p screen */
  --u: calc(100vh / 1080);

  /* color */
  --bg:          #0A0E13;
  --surface:     #121821;
  --surface-2:   #1A2230;
  --line:        #2A3446;   /* borders, bezel (decorative) */
  --text:        #F3F6FA;   /* 17.9:1 on bg */
  --text-muted:  #B4C0CE;   /* 10.5:1 */
  --dim:         #8592A3;   /*  6.1:1, decoration only */
  --accent:      #6EE7D8;   /* bot, ready, replying: 13.0:1 */
  --on-accent:   #0A0E13;   /* text on accent fills: 13.0:1 */
  --visitor:     #A5C8FF;   /* visitor label */
  --st-listening:#8EC5FF;   /* 10.7:1 */
  --st-thinking: #C9B6FF;   /* 10.7:1 */
  --st-warn:     #FFD166;   /* fallback: 13.4:1 */
  --st-error:    #FF9A8B;   /* reconnecting: 9.4:1 */
  --st-off:      #A3AEBB;   /* out of order: 8.6:1 */

  /* type: Atkinson Hyperlegible, bundled in ui/fonts (no network at runtime) */
  --font: "Atkinson Hyperlegible", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --fs-display:  calc(88 * var(--u));  /* attract greeting */
  --fs-oo-title: calc(64 * var(--u));  /* out-of-order title */
  --fs-caption:  calc(56 * var(--u));  /* reply in progress, 1-3 sentences */
  --fs-caption-long: calc(44 * var(--u)); /* 4th sentence on */
  --fs-name:     calc(40 * var(--u));
  --fs-input:    calc(40 * var(--u));
  --fs-body:     calc(36 * var(--u));  /* transcript, examples */
  --fs-status:   calc(32 * var(--u));
  --fs-small:    calc(28 * var(--u));  /* notice, labels: the minimum */
  --lh-tight: 1.15;
  --lh-text:  1.35;
  --fw-regular: 400;
  --fw-bold: 700;

  /* spacing: 8-based */
  --s-1: calc(8 * var(--u));
  --s-2: calc(16 * var(--u));
  --s-3: calc(24 * var(--u));
  --s-4: calc(32 * var(--u));
  --s-5: calc(48 * var(--u));
  --s-6: calc(64 * var(--u));
  --s-7: calc(96 * var(--u));

  --radius-s: calc(12 * var(--u));
  --radius-m: calc(24 * var(--u));
  --radius-l: calc(40 * var(--u));

  /* layout */
  --notice-h: calc(104 * var(--u));
  --input-h:  calc(120 * var(--u));
  --left-w:   calc(620 * var(--u));
  --face-size: calc(420 * var(--u));
  --caption-measure: 45ch;

  /* motion */
  --t-fast: 150ms;
  --t-med:  400ms;
  --t-slow: 600ms;
  --ease:   cubic-bezier(.2, .7, .2, 1);
}
@media (prefers-reduced-motion: reduce) {
  :root { --t-med: 150ms; --t-slow: 150ms; }
}
```

Font: **Atkinson Hyperlegible** (SIL Open Font License), designed for low-vision legibility,
with clearly different `I l 1` and `O 0`. It must be bundled in `ui/fonts/`; the kiosk never
loads fonts or anything else from the internet. The mockup falls back to the system font.

---

## 8. Accessibility

- **Contrast:** all text ≥ 7:1 (table in section 7). Bubbles and the input have a visible
  2 px border, so they don't depend on a fill color.
- **Nothing by color alone.** Every state has a distinct **shape** in the status line and on
  the lamp (● ◆ ◐ ▶ ▲ ■), a **text label**, and a **face expression**. Visitor vs bot =
  label + alignment + outline vs fill. Warnings use the word, not just amber.
- **Large text:** 28 px minimum, captions 56 px, ~45 characters per line.
- **Captions for everything it says.** Every sentence the bot says is on screen as text,
  now and after voice arrives. Voice never says something that isn't captioned.
- **Motion:** respects `prefers-reduced-motion` plus a config flag; no flashing over
  3 per second; the countdown changes a number, it doesn't blink.
- **Keyboard only:** the kiosk is fully usable with a keyboard. Input always has focus;
  `Esc` = Start over. Focus ring: 4 px `--accent` outline.
- **Screen readers** (for a visitor with their own device or a staff check): the transcript
  is a `role="log"` with `aria-live="polite"`; status line is `role="status"`; the face is
  `aria-hidden` (it's decoration; the status line says the same thing in words).
- **Plain language** in every UI string; no jargon ("guard", "session", "model") on screen.

---

## 9. What the engineer needs from the server (websocket)

One websocket per kiosk screen at `/ws`, JSON messages `{ "type": ..., ...fields }`.
The server is the source of truth for sessions, turns, guards, and the kill switch; the
client only owns idle ↔ listening (typing) and visual timers.

### 9.1 Client → server

| type | fields | when |
|---|---|---|
| `message` | `text`, `client_msg_id` | visitor presses Enter |
| `typing` | — | while typing, at most every 5 s (keeps the session alive) |
| `reset` | `immediate: bool` | Start over / Esc (`immediate` = Esc twice, skip goodbye) |
| `pong` | `t` | reply to `ping` |

### 9.2 Server → client

| type | fields | UI does |
|---|---|---|
| `hello` | `bot_name`, `session_timeout_s`, `warn_before_s` (30), `out_of_order: bool`, `out_of_order_line?`, `reduced_motion: bool`, `notice`, `ui_strings`, `ui_examples`, `idle_greetings` | set up; show idle or out of order. Sent on every (re)connect. |
| `turn_start` | `turn_id`, `client_msg_id` | thinking; pending visitor bubble |
| `turn_accepted` | `turn_id` | pending bubble → normal (input guard passed) |
| `input_blocked` | `turn_id`, `category` | replace visitor bubble with "Message hidden" |
| `sentence` | `turn_id`, `index`, `text`, `source` (`"model"` or `"line:<category>"`) | replying; append caption. `source` picks the face (4.5, 4.7) |
| `reply_retracted` | `turn_id` | remove this turn's captions (output guard block), fallback face |
| `action` | `name` (`"wave"`) | face animation now; robot later |
| `turn_end` | `turn_id`, `source`, `hide_user_message: bool` | collapse captions, ready. `hide_user_message` = true for the safety categories (5.4) |
| `busy` | `client_msg_id` | show the "still answering" hint |
| `session_warning` | `seconds_left` | show "Still there?" banner |
| `session_reset` | `reason`: `timeout` / `farewell` / `visitor` / `admin` | after the goodbye line: fade to idle, clear everything |
| `out_of_order` | `on: bool`, `line?` | enter/leave out of order immediately |
| `ping` | `t` | answer with `pong`; 2 missed pings (20 s) = connection lost |

Notes for wiring:
- These map onto what the pipeline already has: `sentence` = `OutputSink.on_sentence`,
  `action` = `ActionHandler.on_action`, `source` = `TurnResult.source`,
  `input_blocked` / `reply_retracted` = `TurnResult.input_guard` / `output_block`,
  `out_of_order` = the kill-switch check. `turn_accepted` and `reply_retracted` are the
  only two that need the pipeline to emit something mid-turn.
- `sentence` events must only be sent **after** the output guard passes that sentence
  (brief rule 2). `reply_retracted` exists only because guarded sentences can be followed
  by a blocked one.
- The **server** sends the goodbye line before `session_reset` (same pipeline path as
  `make chat`), so the UI never picks lines itself, except `idle_greetings` and examples,
  which aren't model output.
- Messages never include the visitor's text back (the client already has it), so a blocked
  message is never re-sent over the socket or logged by the UI.
- The UI keeps nothing in browser storage. Reload = fresh idle screen.

### 9.3 Reserved for voice (M6) and the robot (M7), not built now

| type | direction | purpose |
|---|---|---|
| `listen_start` / `listen_end` | server → client | wake word / push-to-talk; listening state with `source: "mic"` |
| `mic_level` | server → client | 0–1, drives the mouth bars (≤ 15 per second) |
| `partial_transcript` | server → client | live speech-to-text shown in the input area |
| `speech_start` / `speech_end` | server → client | `turn_id`, `index`: which caption is being spoken; captions highlight in sync |
| `action` | server → client | more names later (`nod`, `point`); the UI ignores unknown names |

The states don't change for voice: listening already exists, captions already exist.

---

## 10. Open questions for the PM

1. **Name on screen.** Config says `Dum-E`, a film character's name, and it will be 40 px
   tall under the face. The mockup has a switcher (Dum-E / Ada / Sprocket / Grady) so you
   can see each one; it defaults to **Ada** as a placeholder, not a decision.
2. **Touch or keyboard only?** If the screen is touch, the example questions become buttons
   and Start over gets bigger. The spec assumes a physical keyboard.
3. **Screen size and orientation?** Spec assumes a landscape 1080p+ screen. Portrait needs
   a stacked layout (face on top), which is a small change if you tell me now.
4. **Notice wording:** OK to say "on this computer for 30 days"? And who may read the logs
   (still open from M3) — should the notice say "lab staff can read it"?
5. **`session_timeout_s: 120`** with a 30 s warning: OK for a walk-up kiosk? (Still TODO in config.)
