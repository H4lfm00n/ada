# Decisions

Format: date, owner, decision, and why. Newest at the bottom.

---

### 2026-10-28 (designer): Persona v1, system prompt v1, lines v1

1. **The name is always `{{bot_name}}` in content, and the PM should pick an original public name.**
   "Dum-E" is a film character's name. Using it on a public screen ties us to
   someone else's IP and invites film references we've ruled out. Since every
   file uses the slot, renaming is a config change. Suggested: Ada, Sprocket, Grady.

2. **"Clumsy in style, never in substance."**
   The character jokes about fumbling words, missing arms, and forgetting visitors,
   but it never puts deliberate mistakes into answers. A robot that's wrong for
   laughs teaches people not to trust its lab facts.

3. **The "I don't know" reply uses two fixed phrases: "not sure" and "lab member".**
   This keeps grounding failures checkable with a plain `must_include` rule instead
   of an LLM judge. Every `dont_know` line contains both phrases.

4. **Text placeholders marked TODO in lab docs are treated as unknown.**
   Lab doc templates will ship with `TODO(PM)` placeholders. The prompt tells the
   model to treat them as missing, so a template can never leak out as a "fact".

5. **No markdown, emoji, or stage directions in any reply.**
   Kiosk text has to be readable from 2 m and must pass straight to TTS later.
   Asterisk actions (`*waves*`) are banned now. Robot actions will be designed as
   structured events in M7, not as text.

6. **One runtime prompt file, with dynamic slots last.**
   `prompts/system_prompt.md` holds a condensed persona, so the engineer injects
   one file. `persona.md` is the human bible. Static text comes first and
   `{{current_time}}` / `{{retrieved_context}}` come last, so the serving stack
   can reuse the cached prefix (this matters for first-token latency on the GB10).

7. **Few-shot examples contain no lab facts.**
   Example replies in the prompt only show the "not sure" path for lab questions.
   An example like "meetings are Thursdays at 5" would get repeated as fact.

8. **Emergency and crisis lines use only verified, nationwide resources (911, 988).**
   Campus resources (police, counseling, health) wait for PM confirmation of exact
   names and numbers. Emergency lines put "call 911" in the first sentence.

9. **New line categories beyond the brief's list:** `hate_or_harassment`,
   `about_real_people`, `public_screen_privacy`, `official_sfsu`, `crisis_support`
   (split from emergency), and `goodbye`. Each one maps to a distinct guard outcome
   or session event, and each has a different right answer.

10. **English-only for v1** (pending PM). Guard and eval coverage for other
    languages isn't there yet. The bot says so politely.

11. **No code in replies.** The kiosk isn't an editor, and code blocks break the
    no-markdown rule. The bot explains ideas in words.
