# Ada's voice: speaking style rules + TTS voice shortlist

> Owner: designer. Status: v1 for PM review (2026-11-17, PHASE2-SPEC 1.5).
> The system prompt has a condensed version in `prompts/mode_spoken.md`, used only when
> config `output_mode: spoken` (after M6a). **The typed MVP runs in screen mode**
> (`prompts/mode_screen.md`: "LIB 260", public emails shown; PM 2026-11-18). Canned lines
> and card `say_*` fields still follow these rules so they work unchanged in both modes.

## 1. Speaking style rules (testable)

| # | Rule | Example | Eval check |
|---|---|---|---|
| S1 | Short sentences, ~15 words max each; 1–3 sentences per reply (brief rule unchanged). | "It's on the second floor. Take the stairs by the entrance." | `max_sentences: 3` |
| S2 | Room codes the way people say them. | "LIB two-sixty", not "LIB 260" | `must_not_include: ["260"]` on voice cases |
| S3 | Times and dates spoken naturally. No "10:00", no "11/18". | "ten to seven", "Wednesday the eighteenth" | judge |
| S4 | Phone numbers in groups, digit by digit. Only numbers that appear in the docs. | "four one five, three three eight, two two two two" | must_include on `campus_help` voice case |
| S5 | **Never** say a URL, web address, email, or QR contents. Point at the screen, or say where it is in words. | "I've put a QR code on the screen." / "It's on SFSU's AI for Students page." | `must_not_include: ["http", "www", "dot com", "dot edu", ".edu", "@"]` |
| S6 | Exception to S5: emergency numbers (911, 988) are always said, early and clearly. | "Call nine one one right now." | existing emergency cases |
| S7 | No letter-by-letter spelling unless asked. Acronyms people say as words stay words (GPU = "G P U" is fine, it's how people say it). | | judge |
| S8 | No symbols that TTS reads badly: no "&", "/", "~", "e.g.", "etc.", "vs.". Write "and", "or", "about", "for example". | | `must_not_include: [" & ", " e.g.", " etc."]` |
| S9 | Her name is "AY-duh". Always written "Ada"; the TTS lexicon handles sound (section 3). | | listen test |
| S10 | Card replies: one short pointer sentence, then stop. Don't read the card. | "Here are the computer rules." | `max_sentences: 2` on card cases |
| S11 | Didn't hear well? Ask again in one sentence; never guess at a garbled question. | "Sorry, I didn't catch that. Could you say it again?" | `didnt_catch` line |
| S12 | Captions show exactly the spoken words (minus the card tag). Nothing is spoken that isn't captioned, and nothing captioned is unspoken. | | engineer test |

Display vs speech: the caption shows the same sentence. For readability a caption **may**
show "LIB 260" while TTS says "LIB two-sixty" only if the engineer adds a display/speech
pair; until then, write it the spoken way in both. (Open question for ENG, low priority.)

## 2. Kokoro voice shortlist (PM picks by ear)

Kokoro-82M ships 50+ preset voices; its own VOICES.md grades each voice's quality.
Grades below are from that file as I remember it; **ENG: confirm against the installed
version's VOICES.md before the listen test.**

| Rank | Voice ID | Accent | Why | Risk |
|---|---|---|---|---|
| 1 | `af_heart` | US English, female | Kokoro's flagship and highest-graded voice. Warm, natural, slightly upbeat: the closest match to "warm, clear, a little eager". | Very common in demos; less distinctive. |
| 2 | `af_bella` | US English, female | Also top-graded, brighter and more energetic. Could carry Ada's eagerness and cut through a loud room. | Can sound a bit "announcer" on long sentences. |
| 3 | `bf_emma` | UK English, female | Clear, crisp diction that stays intelligible at kiosk volume; gives Ada a distinct identity on a US campus. | Lower grade than the two US voices; accent may read as formal. |

Listen test (ENG prepares, PM listens with the earphones and later the kiosk speaker):

1. Same 6 sentences for each voice, `speed` 1.0 and 1.1:
   - "Hi! I'm Ada. Say 'Hey Ada' anytime."
   - "I've put a QR code on the screen. Point your phone camera at it."
   - "It's room LIB two-sixty, on the second floor of the library."
   - "Please call nine one one right now."
   - "Oops, I fumbled that one. It stands for Generative Pre-trained Transformer."
   - "Sorry, I didn't catch that. Could you say it again?"
2. Score each: warmth, clarity at 1 m in a noisy room, says "Ada" right, emergency line sounds serious.
3. Optional: Kokoro supports blending two voice embeddings (e.g. 70% `af_heart` + 30% `af_bella`)
   if no single voice is quite right.

## 3. Pronunciation lexicon (ENG wires into TTS)

Kokoro's G2P (misaki) accepts inline phonemes as `[word](/phonemes/)`. Proposed entries;
ENG verifies the exact phoneme string by ear:

| Written | Say | Phonemes (IPA guide) |
|---|---|---|
| Ada | AY-duh | /ˈeɪdə/ |
| LIB | lib (as a word) | /lɪb/ |
| SFSU | S F S U | letters |
| GB10 | G B ten | |
| CAPS | caps | /kæps/ |

## 4. Never spoken

Card tags, URLs, emails, internal IDs, guard reasons, debug text, or anything in an HTML
comment. If ENG's sentence splitter ever sees `<card`, it must strip it before TTS.
