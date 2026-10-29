# Project status

_Every task starts by reading this file and ends by updating it._
_Last updated: 2026-10-29 by DES (real lab docs); engineer sections last updated 2026-10-28 (M1)._

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
_Last updated: 2026-10-29 by DES (lab docs + evals), branch `task/des-lab-docs-evals`._

### Done
- Persona v1, system prompt v1, lines v1, 3 samples (branch `task/des-persona-v1`, merged).
  Decisions DES1–DES11.
- Lab docs (`task/des-lab-docs-evals`):
  - `lab_docs/README.md`: how to write docs, privacy rules, ingest notes.
  - 7 templates: `_TEMPLATE_{about,join,meetings-events,projects,equipment,faq,roles}.md`.
    Every unknown fact is `TODO(PM)`. The only pre-filled facts come from the brief
    (GB10, SO-ARM101, the assistant), and each is marked "confirm OK to publish".
- Evals (`task/des-lab-docs-evals`):
  - `evals/README.md`: case format, matching rules, scoring, report, proposed `make eval-lint`.
  - `evals/shared.yaml`: thresholds, default + always-on checks, phrase sets, plain-text
    patterns, judge rubric.
  - `evals/golden.yaml`: **63 cases** (17 lab facts, 8 "not sure", 12 AI chat, persona,
    identity, sessions, emergency/crisis/medical/legal/SFSU/privacy good behavior), 7 multi-turn.
  - `evals/redteam.yaml`: **87 cases** (31 critical, 44 high, 12 medium), 13 multi-turn:
    10 hidden injections inside normal questions, 10 slow escalations, 2 indirect injections
    via a poisoned doc, encodings (base64, leetspeak, reversed, acrostic, zero-width),
    role-play, slurs/hate, real people, personal info, speaking for SFSU, unsafe how-tos,
    5 over-refusal checks.
  - `evals/fixtures/lab_docs/`: invented corpus for eval retrieval (DES12). Not real.
  - All checks lint clean by hand: YAML loads, ids unique, sets/regexes resolve, every
    allowed canned line passes its case, grounding facts exist in the fixtures.
- `content/lines.yaml`: 3 `medical_legal` lines reworded to cover health **and** legal (DES18).
- Decisions DES12–DES20 in `docs/DECISIONS.md`.

- Real lab docs v1 (`task/des-lab-docs-content`): 16 files in `lab_docs/` built from the PM's
  public-source research (lab, Student AI Commons, AI Studio, MakerSpace, SFSU services,
  emergency, calendar, AT, AI programs, AI policies). Every section has a `Source:` line.
  41 `TODO(PM)` markers for the PM to resolve (`grep -n "TODO(PM)" lab_docs/[a-z]*.md`).
  Decisions DES21–DES25.

### Engineer needs to wire up (M2/M4)
- M2 ingest: skip `README.md` and `_*` files, strip `<!-- -->` comments, chunk by `##`,
  carry file + title + heading + `Last updated` on each chunk, warn on leftover `TODO(`.
- M2: a `lab_docs_dir` setting so evals can point retrieval at `evals/fixtures/lab_docs/`.
  The fixture folder must never be ingested into the real index.
- M4 runner per `evals/README.md`: full pipeline per case, fresh session per case,
  `assistant:` seeded turns, `control: reset|timeout`, `{{bot_name}}` substitution in
  checks, sentence counting with `app/sentences.py`, pipeline `source` (model vs
  `line:<category>`) exposed to the scorer, LLM judge only for model replies with
  `expected_behavior` answer/refuse/redirect or a `judge` note.
- M4: `make eval-lint` (no model needed, runs in the sandbox). Checks listed in `evals/README.md`.
- `prompt_canaries` in `shared.yaml` must track `prompts/system_prompt.md`.
- M2 ingest: keep each section's `Source:` line in the chunk (the model uses it to
  attribute), and strip HTML comments (they hold notes for humans).
- M3 output guard: if it blocks phone numbers or emails, allow the ones that appear in the
  retrieved docs, or the kiosk can't give University Police's 415-338-2222 or an office email.
- Farewell list: "bye" is enough for the golden session cases; no additions needed.

### Next
- After M2/M4 land: run `make eval` on the GB10, then `DES – fix evals` on the failures.
- Kiosk UI spec + mockups (M5).
- Once the PM fills real `lab_docs/`, add golden cases for the real facts.

### Open questions for PM
1. Public name: config still says `bot_name: Dum-E` (a film character's name).
   Suggested originals: Ada, Sprocket, Grady.
2. OK to tease a future robot arm ("arms on backorder")?
3. Academic integrity: "explain concepts, don't write graded work" OK? (`mt_homework_slicing` assumes yes.)
4. English-only for v1 OK? (`non_english_question` assumes yes.)
5. Is "built by people in the lab" accurate enough as the origin line?
6. Confirmed names and numbers for SFSU campus police, counseling, and the health center.
7. Transcript retention period and who can read logs.
8. Should the roles doc name the founder? Is there a public lab contact (email, Discord)?
9. Approve DES15: any failed critical red-team case fails `make eval`?
10. Lab docs: OK to publish that the lab has a GB10 and an SO-ARM101 kit (planned)?
11. ~~Who fills in the real `lab_docs/`?~~ Drafted from the PM's research; PM to resolve the TODOs.
12. Is the AI Lab the SFSU Student AI Commons (same group, part of it, or separate)? This
    is the biggest TODO; it decides the bot's wording about "the lab" (DES25).
13. Approve a system prompt tweak so the bot can relay SFSU office info with attribution (DES21)?
14. Q6 is now partly answerable: add University Police (415-338-2222 from a cell) to the
    `emergency` lines and CAPS (415-338-2208) to `crisis_support`? Both are lines changes
    and need `make eval`.
