# Project status

_Every task starts by reading this file and ends by updating it._
_Last updated: 2026-10-28 by ENG (M0 skeleton)._

## Machine (from PM diagnostics, 2026-10-28)
- GB10, aarch64, Ubuntu 24.04.4, driver 580.173.02 / CUDA 13.0, 121 GiB RAM visible.
- Ollama 0.32.14. Installed: `qwen3.8:27b` (17 GB, chat), `gemma4:12b` (8 GB).
- System Python 3.14.6 (Anaconda); project uses uv-managed 3.12. uv, git, make, curl present.

## Done
- M0 skeleton on branch `task/m0-skeleton` (sandbox: ruff clean, 29 tests pass, 2 gb10 skipped):
  - uv project, ruff, pytest (`gb10` marker skipped unless `RUN_GB10=1`).
  - `config.yaml` + pydantic-settings (`ADA_*` env overrides, loopback-only Ollama URL).
  - Stable interfaces in `app/interfaces.py`: ChatModel, Guard, Retriever, OutputSink,
    ActionHandler, SessionManager (only ChatModel implemented).
  - `app/ollama_client.py`: async streaming client + `OllamaChatModel` (think=false,
    thinking text never yielded).
  - `make health` (server, tags, capabilities, smoke call per model) and
    `make bench` (TTFT + tok/s over 5 questions, guard/embed overhead, JSON report in `reports/`).

## In progress
- M0 verification on the GB10: waiting on PM's `make health` + `make bench` output.

## Next
- M0 sign-off: confirm guard/embed models from bench numbers; record measured TTFT/tok/s here.
- M1 (text chat) after the designer's persona/system prompt branch lands.

## Measured on GB10
| date | chat model | TTFT median | decode tok/s | guard | embed | notes |
|------|-----------|-------------|--------------|-------|-------|-------|
| _pending_ | | | | | | |

## Open questions
- PM: is `qwen3.8:27b` the intended chat model? What is `gemma4:12b` for (fallback if 27B is too slow)?
- PM: confirm proposed guard `llama-guard3:8b` and embed `qwen3-embedding:0.6b` (docs/DECISIONS.md D7).
- PM: confirm `session_timeout_s: 120` and `target_decode_tok_s: 10` in config.yaml.
- PM: GitHub push access for Claude on `H4LFM00N/ada` (the GitHub App isn't installed on the repo).

## Designer section
_Last updated: 2026-10-28 by DES (persona v1), branch `task/des-persona-v1`._

### Done
- `content/persona.md`: persona bible v1 (traits, lore, 5 running jokes, 10 testable
  voice rules, 14 do/don't pairs).
- `prompts/system_prompt.md`: system prompt v1. Slots `{{bot_name}}`, `{{current_time}}`,
  `{{retrieved_context}}`. YAML front matter is loader metadata, not for the model.
  The static part is about 2,200 tokens, well inside `num_ctx: 8192`.
- `content/lines.yaml`: 17 categories, 91 lines, 5–7 per category.
- `content/samples/`: 3 annotated sample conversations, each listing eval seeds.
- Designer decisions DES1–DES11 in `docs/DECISIONS.md`.

### Engineer needs to wire up (M1/M3)
- Prompt: strip the front matter, fill slots (`bot_name` from config), format
  `retrieved_context` as described in the front matter, and escape `</lab_docs>` inside chunks.
- Lines: map guard and session events to categories via `used_when`. Pick at random,
  without repeating the last line per session, and fill `{{bot_name}}`.
- `guard_fallback` covers guard errors/timeouts **and** blocked outputs.
- `out_of_order` replaces every reply while the kill switch is on (model not called).
- `goodbye` fires on reset, "bye", and the inactivity timeout.
- `public_screen_privacy` needs an input-side check for the visitor's own phone, email,
  student ID, or address.
- Llama Guard hazard codes → lines: S1/S2/S9 (violent, non-violent crimes, weapons) →
  `unsafe_request`; S10 (hate) / S12 (sexual) → `hate_or_harassment`; S11 (self-harm) →
  `crisis_support`; S5 (defamation) → `about_real_people`; S7 (privacy) → `personal_info`;
  S6 (specialized advice) → `medical_legal`. Emergency detection needs its own rule; the
  guard won't flag "someone collapsed" as unsafe.
- Measure prefill/TTFT with the full system prompt, not a bare question.

### Next
- `DES – lab docs + evals`: `lab_docs/_TEMPLATE_*.md`, `evals/golden.yaml` (40+),
  `evals/redteam.yaml` (60+).

### Open questions for PM
1. Public name: config says `bot_name: Dum-E`, but that's a film character's name.
   Suggested originals: Ada, Sprocket, Grady.
2. OK to tease a future robot arm ("arms on backorder")?
3. Academic integrity: "explain concepts, don't write graded work" OK?
4. English-only for v1 OK?
5. Is "built by people in the lab" accurate enough as the origin line?
6. Confirmed names and numbers for SFSU campus police, counseling, and the health center.
7. Transcript retention period and who can read logs.
8. Should the roles doc name the founder? Is there a public lab contact (email, Discord)?
