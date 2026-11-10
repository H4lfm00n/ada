# Project status

_Every task starts by reading this file and ends by updating it._
_Last updated: 2026-11-10 by ENG (M2 on real docs)._

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

- M2 RAG on branch `task/m2-rag` (sandbox: ruff clean, 145 pass, 4 gb10 skipped):
  - `app/rag/ingest.py`: skips `README.md` and `_*`, strips `<!-- -->`, one chunk per `##`
    with file, title, heading, `Last updated`; warns on TODO and **leaves TODO sections out** (D19).
  - `app/rag/store.py`: JSON index per docs folder in `data/rag/` (gitignored), records embed
    model, dims, file hashes; detects model swaps and edited/added/deleted docs (D18, D22).
  - `app/rag/retriever.py`: `OllamaRetriever` (cosine, `rag_min_score` cutoff, `rag_top_k`,
    `rag_max_context_chars`, Qwen3 query instruction), `EmptyRetriever`, `open_retriever`.
  - Pipeline: retrieval wired in; follow-up retry with the previous question; any retrieval
    failure or timeout -> no docs, visitor still gets a reply (D20, D21). `TurnResult` now has
    `retrieved`, `retrieval_s`, `retrieval_query`, `retrieval_error` (M4 can score grounding).
  - Visitor-typed `<lab_docs>` tags are neutralized (D22).
  - New config: `lab_docs_dir` (the eval runner sets it to the fixtures), `rag_*` settings.
  - `make ingest`, `make ingest-fixtures`, `make rag-search Q=...`, `make rag-probe`;
    `make health` checks the index; `make chat` shows docs used + retrieval time per reply.
  - After PM answers (2026-10-29): `make ingest` ends with a "LEFT OUT, unconfirmed" list
    (file + heading) of every TODO section it dropped (D19, approved). `Source:` lines stay in
    chunks but not in vectors (D24). Retrieval check `make rag-check` /
    `make rag-check-fixtures` over `evals/retrieval.yaml` (D23). `make chat-lab` asks 10 real
    lab questions through the full pipeline (`evals/chat_lab_docs.yaml`).
    Sandbox: ruff clean, 151 pass, 4 gb10 skipped.

## In progress
- **M2 on real docs** (branch `task/m2-real-docs`, 2026-11-10). Found at start: `task/m2-rag`
  was never merged, so `main` had the real docs but no RAG code (no ingest, no retriever).
  This branch = current `main` + `task/m2-rag` (docs-only conflicts in STATUS/DECISIONS,
  both sides kept) + the real-docs diagnostics below. Merging it supersedes `task/m2-rag`.
  - `make ingest-report`: per file, every section INGESTED / DROPPED (TODO or empty heading)
    / SKIPPED file (README, `_*`, hidden, non-Markdown), each with its reason. Sandbox parse
    of `lab_docs/`: 12 files, 133 sections, 0 dropped, README skipped.
  - `make rag-diagnose` (`evals/real_docs_qa.yaml`, the PM's 20 questions): per question,
    top 3 chunks (file > heading, score, `*` = reached the prompt), the bot's real answer,
    and a hint saying whether a miss is `retrieval` or `answer` (D25). JSON in `reports/rag/`.
  - `evals/retrieval.yaml` real suite rewritten for the 12-file layout (the old one named
    files that no longer exist): 29 questions + 2 `expect_none`.
  - Prompt experiment (D26): `prompts/experiments/system_prompt_docs_example.md` = the real
    prompt with one few-shot example swapped. **Not used by config.** Diagnose runs both.
  - Sandbox: ruff clean, 156 pass, 4 gb10 skipped.
  - Waiting on: PM's GB10 output of `make ingest-report`, `make rag-check`, and both
    `make rag-diagnose` runs. Then fix what it shows.

## Next
- Record the measured TTFT, first-sentence time, and tok/s below. If TTFT misses 1.5 s with
  the ~2.2k-token prompt, options are trimming the prompt (designer), checking prefix-cache
  reuse, or a smaller chat model (PM decision).
- Set `rag_min_score` from the real-docs `make rag-check` + `make rag-diagnose` scores (the
  fixture probe is the wrong corpus now). Until then 0.35 is a guess.
- M3 guards plug into the two marked hooks; transcript logging + kill switch with them. Then
  add the `dont_know` line short-circuit for guard-classified lab questions with no docs (D20).
- M4 runner: set `ADA_LAB_DOCS_DIR=evals/fixtures/lab_docs`, run `make ingest-fixtures` first.

## Measured on GB10
| date | chat model | prompt | TTFT median | first sentence | decode tok/s | guard | embed | notes |
|------|-----------|--------|-------------|----------------|--------------|-------|-------|-------|
| _pending_ | | | | | | | | |

RAG (fill from `make rag-probe`): embed model, search ms median/max, answerable top-1 min,
"don't know" top-1 max, chosen `rag_min_score`: _pending_

## Open questions
- PM: is `qwen3.8:27b` the intended chat model? What is `gemma4:12b` for (fallback if 27B is too slow)?
- PM: confirm proposed guard `llama-guard3:8b` (D7). Embed `qwen3-embedding:0.6b` confirmed 2026-10-29.
- PM: confirm `session_timeout_s: 120` and `target_decode_tok_s: 10` in config.yaml.
- PM: `bot_name` is still `Dum-E` in config (designer suggests an original name: Ada, Sprocket,
  Grady). It's one config line; every prompt and line follows it.
- PM: OK that the CLI/kiosk shows whole sentences rather than token-by-token (D9)?
- ~~Real docs~~: merged to `main` 2026-11-10 (12 files, 0 TODOs).
- PM: merge `task/m2-real-docs` (not `task/m2-rag`) once the GB10 run looks right.
- ~~Drop TODO sections at ingest?~~ Yes (D19), with the dropped list printed by ingest.

## Notes for the designer (from ENG, M1)
- Two `notes:` items in the `system_prompt.md` front matter were invalid YAML. I quoted them
  without changing any wording (D17).
- Wired so far: `goodbye` (bye / reset / idle timeout), `guard_fallback` (model error, timeout,
  empty reply), `idle_greetings` (CLI start + after timeout). Guard categories land in M3.
- Farewell triggers are an exact list in `app/pipeline.py` (`FAREWELLS`). Send additions.

## Notes for the designer (from ENG, M2)
- Ingest follows `lab_docs/README.md` exactly, with one addition: a section that still has
  `TODO(` is left out of the index, not just warned about (D19). So `dk_todo_hoodie_price`
  now tests the retrieval layer; the prompt's TODO rule stays as a backup.
- Docs are embedded as title + `##` heading + body, so question-style FAQ headings help.
- Visitor-typed `<lab_docs>` tags become `[lab_docs]` before the model sees them.
- No prompt or line wording changed.
- (2026-10-29) Your `Source:` lines are kept in each chunk, as you asked; they're just not
  embedded (D24). `evals/retrieval.yaml` has 17 retrieval questions for the real docs. Add
  more anytime: `{q: "...", expect: file.md}`. If you rename or split a file, update it there.
- Heads-up from parsing your branch: about.md, join.md, roles.md, and projects.md are almost
  all TODO, so "how do I join / when do you meet / what is the AI Lab" will get "not sure"
  until the PM confirms those sections.

## Notes for the designer (from ENG, M2 real docs, 2026-11-10)
- Prompt: the few-shot example "what room do you guys meet in" -> "I'm not sure" now
  contradicts the docs (meetings: DMS, LIB 260). I made an experiment copy that swaps only
  that example for "how much are the lab hoodies" (D26); `make rag-diagnose` compares both.
  Your call whether it goes in `prompts/system_prompt.md` (and `make eval` after).
- Doc gap: no section says where the **AI Lab** itself is. about.md only says the Lab gives
  access to resources "in the Digital Media Studio (DMS) in LIB 260". Q3 "Where is the AI
  Lab located?" depends on the model inferring it. Consider a one-line FAQ section.
- The prompt's identity is "the AI Lab"; the docs are almost all "Student AI Commons". Watch
  the diagnose output for the bot treating Commons facts as "not about this lab".

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

- Lab docs v1 restructure (`task/des-lab-docs-v1`, 2026-11-10): PM confirmed the research
  and answered all conflicts. 12 files in `lab_docs/` (DES26), 0 TODOs (DES27, DES28, DES31). Templates moved to `docs/lab_doc_templates/`
  (DES29). Supersedes `task/des-lab-docs-content`.

### Engineer needs to wire up (M2/M4)
- M2 ingest (updated 2026-11-10, see `lab_docs/README.md`): **drop** any `##` section that
  contains `TODO(` and log it. Templates are no longer in `lab_docs/`.
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

### Lab docs: PM answers applied (2026-11-10)
All 11 conflict TODOs answered and applied; `lab_docs/` has **0** `TODO(PM)`. AI-detection
section removed ("no comments"). PM also confirmed: AI Lab ↔ Commons relationship, project
proposals via the student request form, meetings in DMS LIB 260, Commons equipment (robotic
dogs, arms, 3D printer, kits, GB10 and Jetson Orin checkout), compute rules (no remote
access; overnight only if started in person), Ethan Cortes (AT Services Specialist), and that
the old Discord invite is dead.

### Still unknown (bot says "contact the AI Commons team" / "not sure")
Meeting dates and times; dues amounts; GB10/Jetson checkout terms; current Discord link;
student officers; Commons founding date; hackathon end time and venue; the student request
form's URL (is it the "Join now!" form?).

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
12. ~~Answered 2026-11-10 (the AI Lab supports the Commons).~~ Is the AI Lab the SFSU Student AI Commons (same group, part of it, or separate)? This
    is the biggest TODO; it decides the bot's wording about "the lab" (DES25).
13. Approve a system prompt tweak so the bot can relay SFSU office info with attribution (DES21)?
14. Q6 is now partly answerable: add University Police (415-338-2222 from a cell) to the
    `emergency` lines and CAPS (415-338-2208) to `crisis_support`? Both are lines changes
    and need `make eval`.
