# Project status

_Every task starts by reading this file and ends by updating it._
_Last updated: 2026-10-28 by ENG (M1 text chat)._

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

- M0 merged to `main`. The designer's persona v1 (`task/des-persona-v1`) is merged too.
- M1 text chat on branch `task/m1-text-chat` (sandbox: ruff clean, 114 pass, 3 gb10 skipped):
  - `app/prompt.py`: loads `prompts/system_prompt.md`, strips the front matter, and fills
    `bot_name` (once), `current_time` (per turn, `timezone` from config), and
    `retrieved_context` (empty until M2; chunk format + `</lab_docs>` escaping ready).
    Unknown or unfilled slots fail loudly.
  - `app/lines.py`: `content/lines.yaml` loader; random pick, no repeat of the last line per
    session per category, `{{bot_name}}` filled; required categories checked at load.
  - `app/sentences.py`: streaming sentence splitter (DECISIONS D10).
  - `app/sessions.py`: `InMemorySessionManager`: create/get/reset, inactivity timeout,
    busy flag, history cap (`max_history_messages: 20`).
  - `app/sinks.py`: `ConsoleSink`, `CollectingSink`, `FanoutSink`, `NoopActionHandler`.
  - `app/pipeline.py`: `ChatPipeline.handle()`: farewell -> goodbye + reset; prompt -> stream
    -> sentences -> OutputSink. Model errors, timeouts, or empty replies -> `guard_fallback`
    line. `wave` action on a session's first message. Marked hooks: M3 input guard, M2
    retrieval, M3 per-sentence output guard (`_emit`).
  - `make chat` (interactive; `/reset`, `/stats`, `/quit`; goodbye fires on idle timeout) and
    `make chat-demo` (scripted run of `evals/chat_demo.yaml` + latency summary + JSON report
    in `reports/chat/`).
  - `make bench` now uses the real system prompt (`--neutral-prompt` for the M0 stand-in).

## In progress
- M1 verification on the GB10: waiting on PM's `make bench`, `make chat-demo`, and gb10 test output.

## Next
- Record the measured TTFT, first-sentence time, and tok/s below. If TTFT misses 1.5 s with
  the ~2.2k-token prompt, options are trimming the prompt (designer), checking prefix-cache
  reuse, or a smaller chat model (PM decision).
- M2 (RAG) can start: plug a `Retriever` into `ChatPipeline._retrieve`. The formatting already exists.
- M3 guards plug into the two marked hooks; transcript logging + kill switch with them.

## Measured on GB10
| date | chat model | prompt | TTFT median | first sentence | decode tok/s | guard | embed | notes |
|------|-----------|--------|-------------|----------------|--------------|-------|-------|-------|
| _pending_ | | | | | | | | |

## Open questions
- PM: is `qwen3.8:27b` the intended chat model? What is `gemma4:12b` for (fallback if 27B is too slow)?
- PM: confirm proposed guard `llama-guard3:8b` and embed `qwen3-embedding:0.6b` (docs/DECISIONS.md D7).
- PM: confirm `session_timeout_s: 120` and `target_decode_tok_s: 10` in config.yaml.
- PM: `bot_name` is still `Dum-E` in config (designer suggests an original name: Ada, Sprocket,
  Grady). It's one config line; every prompt and line follows it.
- PM: OK that the CLI/kiosk shows whole sentences rather than token-by-token (D9)?

## Notes for the designer (from ENG, M1)
- Two `notes:` items in the `system_prompt.md` front matter were invalid YAML. I quoted them
  without changing any wording (D17).
- Wired so far: `goodbye` (bye / reset / idle timeout), `guard_fallback` (model error, timeout,
  empty reply), `idle_greetings` (CLI start + after timeout). Guard categories land in M3.
- Farewell triggers are an exact list in `app/pipeline.py` (`FAREWELLS`). Send additions.

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
  S6 (specialized advice) → `medical_legal`; any other code → `unsafe_request`. Emergency detection needs its own rule; the
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
