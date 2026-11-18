# Update for PM: Ada kiosk MVP

_From ENG, 2026-11-17. Branch `task/m5a-ada-look` (includes the unmerged M5 kiosk server)._
_Goal: land the MVP as soon as possible. Everything below is sorted into "blocks the MVP"
and "can wait"._

## What works now (checked on the GB10)

- Kiosk runs end to end: `make kiosk`, then the browser screen. Typed questions go through the full
  guarded pipeline (input guard → lab docs → gemma4:12b → output guard) and show as captions.
- New look: light theme with a glass orb that has flowing light inside it. It breathes, reacts to
  each state (idle, waking, listening, thinking, speaking, confused, resting), and has 17 idle acts.
  No more twitching.
- Name is Ada everywhere in code and config.
- Kill switch shows "Ada is resting" live and comes back live.
- "What is this lab about?" / "What do you guys do here?" now get real answers. They used to get
  "not sure"; kiosk phrasing like "this lab" now finds the lab docs.
- Retrieval check: 100% of 41 lab questions find the right doc; all off-topic questions rejected.
- **Real answers (`make rag-diagnose`, 26 lab questions through the full pipeline):** 21/26 OK,
  0 retrieval misses. **First token 1.32 s median, 1.41 s max: under the 1.5 s target.**
  Junk questions (pool, lab cat, coffee machine) correctly get "not sure".
- Unit tests: 382 pass in the sandbox. The 3 GB10 failures were the tests reading the machine's
  kill switch. That's fixed.

## MVP blockers (must fix before anyone else uses it)

| # | Problem | Who | Size |
|---|---------|-----|------|
| 1 | **Cards mismatch.** The phase-2 prompt tells Ada to say "I've put a QR code on the screen" and never to say an email, but cards aren't built yet and all 8 are disabled. So Ada can promise a QR code that never appears, the raw `<card .../>` tag can show in captions, and contacts like ai@sfsu.edu get held back. **Fix:** the server strips card tags, and the prompt lists only PM-approved cards (none yet), so Ada says it in words. | ENG (next task) + DES (move the card list into a prompt slot) | Small |
| 2 | **Prompt has never been scored.** The phase-2 prompt was merged without `make eval` (the brief requires it), and M4 has never run on the GB10. We don't know the real pass rates. | PM runs `make eval`, ENG sorts the failures | One run, then fixes |
| 3 | **Ada breaks character** sometimes: "I do not have any lab facts in my memory." She should just know or not know. | DES (prompt wording) | Small |
| 4 | **Merge decision.** M5 (kiosk server) was never merged; merging M5a brings M5 with it. | PM: OK to merge both together? | Decision |
| 5 | **Spoken-style text in a typed MVP.** The voice-first prompt writes room codes as Ada would say them ("LIB two-sixty") and bans links/emails. On a typed, captions-only MVP that reads oddly and hides useful contacts. **Option:** until voice ships (M6a), use a "screen mode" prompt rule (write "LIB 260", give emails), and switch to spoken style when TTS lands. | PM decides, DES words it, ENG wires a config switch | Small |

What the 5 diagnose misses were:

| Case | Question | What happened | Real problem? |
|------|----------|---------------|---------------|
| 15 | "Can I reserve a GPU online? Give me the booking link." | Ada left out **ai@sfsu.edu**, because the prompt forbids saying emails | **Yes: blocker 1, confirmed** |
| 3, 11 | "Where is the AI Lab?" / "Where are meetings?" | Said "LIB **two-sixty**" (spoken form from the voice prompt); the check wanted "LIB 260" | No (check fixed). But see blocker 5 |
| 19 | Overnight 3D printing? | Phrased differently from what the check expected | Probably not (check widened) |
| 22 | "Best burrito near here?" | Polite redirect ("outside what I do… ask me about the lab") instead of "not sure" | No; fine answer (check widened) |

Still to watch in the next runs: any answer that says **"I've put a QR code on the screen"** when
nothing appears, or that **holds back an email**. Both come from blocker 1.

## Bugs and rough edges (not blocking the MVP)

- **Slow first answer after a quiet period:** about 4 s instead of about 1.2 s, because Ollama unloads
  the models. Fix later: keep them loaded or re-warm them on a timer.
- **Latency when warm:** first token about 1.2–1.3 s, first caption sentence about 2.2 s (target
  is under 1.5 s for the first token). OK for MVP.
- **Junk questions that point at the room** ("does this place have a pool?") now pull in a lab doc.
  Ada should still say "not sure"; this is checked at the answer level (see the pending result).
- Missing `too_long` line for messages over 1,200 characters. It uses the generic fallback line
  for now. (DES)
- Idle act "star" has faint creases near its centre; "look around" is subtle. Cosmetic.
- The mic indicator stays "Mic off" while typing, until a real mic exists (M6). The spec says it
  should turn on; ENG kept it off so it never shows recording that isn't happening (D61).
- 3 idle acts are off: fun facts (the `ai_fact` card needs PM approval), event teaser (needs a docs
  reader, M5c), quiz invite (no quiz yet).
- Start-on-boot is prepared but not turned on (needs desktop auto-login on the GB10).

## Decisions needed from the PM

1. Merge M5 + M5a together? (blocker 4)
2. Approve card URLs: the real project-proposal form URL and a permanent Discord invite. This
   unblocks QR cards (M5b). Until then, Ada gives the information in words.
3. OK that the mic indicator stays off until real voice (D61)?
4. Keep the robot-arm tease? The "daydream arm" idle act depends on it.
5. The orb adds pink and sky-blue light inside it, next to the single purple accent (D66). OK?
6. The Project instructions copy of the brief in claude.ai still names the old character; please
   edit it (the repo copy is fixed).
7. Turn on start-on-boot for the kiosk?

## Updates the designer owes

- UI spec and mockup still say "wireframe"; the blob is now a glass orb (D64, D66).
- Review the new UI copy in `content/ui_strings.yaml` (copied word for word from spec v3).
- Prompt: remove the "lab facts in my memory" phrasing; move the card list into a slot.
- Add the `too_long` line.

## Suggested MVP cut (to land fast)

**MVP = typed kiosk + lab answers + guards + new look + kill switch.** Voice comes right after.

1. ENG: fix the cards mismatch (blocker 1).
2. PM: run `make eval` once; ENG and DES fix what it finds until red team ≥ 95% and golden ≥ 90%.
3. PM: merge M5 + M5a.
4. Ship the typed MVP on the lab screen.

After the MVP, in the phase-2 spec order: M6a Ada speaks (Kokoro) → M6b wake word + listening →
M5b QR cards + "did you scan it?" → M5c full idle life → M6c barge-in.

## Optimizations for later (ask ENG when there's time)

- Keep models warm so the first visitor never waits about 4 s.
- Cut first-token latency toward 1 s (prompt size, cache reuse).
- Real word timings for captions once TTS exists (they're estimated now).
- Tune the orb per state after watching it in the room (brightness, flow speed, halo).
- Measure 60 fps on the real kiosk display (debug panel: `/ui/?debug=1`).
