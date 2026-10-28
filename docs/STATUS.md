# Status

Every task starts by reading this file and ends by updating its own section.

## Engineer

_Not started in this repo yet (M0 pending)._

## Designer

**Last updated:** 2026-10-28, branch `task/des-persona-v1`

### Done
- `content/persona.md`: persona bible v1 (traits, lore, 5 running jokes, 10 testable
  voice rules, 14 do/don't pairs).
- `prompts/system_prompt.md`: system prompt v1 with slots `{{bot_name}}`,
  `{{current_time}}`, `{{retrieved_context}}`. Covers every guardrail in brief §5
  that the prompt can enforce.
- `content/lines.yaml`: 17 categories, 5–7 variants each.
- `content/samples/`: 3 annotated sample conversations (first-timer, faculty at an
  event, 4pm breaker), each with eval seeds.
- `docs/DECISIONS.md`: 11 designer decisions.

### Next
- `DES – lab docs + evals`: `lab_docs/_TEMPLATE_*.md`, `evals/golden.yaml` (40+),
  `evals/redteam.yaml` (60+). The eval seeds named in `content/samples/` are the
  starting list.
- After M4: iterate on failing eval cases.

### Engineer needs to wire up
- Load `prompts/system_prompt.md`, strip the YAML front matter, fill the 3 slots.
  Format `{{retrieved_context}}` as described in the front matter, and escape any
  `</lab_docs>` inside chunks.
- Load `content/lines.yaml`. Map guard and session events to categories via
  `used_when`. Pick at random, without repeating the last line used per session.
  Fill `{{bot_name}}`.
- Use `guard_fallback` for guard errors/timeouts **and** for blocked outputs.
- `out_of_order` replaces every reply while the kill switch is on (model not called).
- `public_screen_privacy` needs an input-side check for the visitor's own phone,
  email, student ID, or address.

### Open questions for PM
1. Public name (Ada / Sprocket / Grady / other)?
2. OK to tease a future robot arm ("arms on backorder")?
3. Academic integrity: "explain concepts, don't write graded work" OK?
4. English-only for v1 OK?
5. Is "built by people in the lab" accurate enough as the origin line?
6. Campus resources: confirmed names and numbers for SFSU police, counseling, and
   the health center.
7. Logs: retention period and who can read them (so the bot can answer if asked).
8. Roles doc: should it name the founder, and is there a public lab contact
   channel (email, Discord) the bot may share?
