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
- **M2 on real docs** (branch `task/m2-real-docs`). `task/m2-rag` was never merged, so `main`
  had the docs but no RAG; this branch = `main` + `task/m2-rag` + the work below. Merging it
  supersedes `task/m2-rag`.
  - Tools: `make ingest-report` (every section ingested / dropped / skipped, with reasons),
    `make rag-diagnose` (D25; per question: top 3 chunks, the answer, a retrieval-vs-answer
    hint; `ARGS="--repeat 3"` asks each question 3 times), real-docs `evals/retrieval.yaml`.
  - **GB10 run 1 (2026-11-10):** ingest 12 files, 133 sections, 0 dropped, README skipped.
    Retrieval: recall@4 100%, hit@1 100% on 29 questions. Both "nothing" questions leaked
    through the 0.35 cutoff (0.389, 0.432). Answers: 17/20 OK on the baseline prompt.
    - Q10 (get involved without a project): retrieval pulled only proposal sections and the
      bot said "not sure". Fixed with a FAQ section (D28).
    - Q12 (weekly day and time): "not sure, ask a lab member" but no ai@sfsu.edu, though
      the doc in the prompt names it. Prompt wording (see designer notes).
    - Q19 (overnight 3D printing): the answer was right; my hint wrongly required "seven
      hours". Case fixed.
    - The prompt experiment (D26) didn't help (15/20). Deleted.
  - **Fixes in run 2:** two-tier cutoff, gate 0.50 / floor 0.40 (D27). Q10 FAQ section
    (D28). Diagnose cases fixed, plus checks for overclaims seen in run 1. `--repeat`.
    Section-level expectations. First-token median in the summary. Retrieval check grew to
    41 questions (6 negatives, 6 casual). Sandbox: ruff clean, 158 pass, 4 gb10 skipped.
  - **GB10 run 2 (2026-11-10, gate/floor 0.50/0.40, each question asked 3 times):**
    - Real-docs retrieval: recall@4 100%, hit@1 100%, negatives 5/6. Fixture corpus: PASS,
      100% recall, negatives 3/3.
    - The one leak: "does the lab have a coffee machine?" scores 0.549 against equipment.md,
      and the lowest answerable question scores 0.560. No cutoff separates them, so it moved
      to the diagnose set (case 20), where the reply is checked (D27).
    - Answers 56/60 at both temperature 0.7 and 0.3. Q10 fixed (3/3). Q4 and Q15 3/3.
    - Q14 (proposal guarantee): every answer was correct; my hint was too narrow ("doesn't
      guarantee", "not necessarily"). Fixed.
    - **Q12 (weekly day and time) is the only real miss:** "not sure, ask a lab member"
      without ai@sfsu.edu, which the doc in the prompt gives. 1/3 at 0.7, 0/3 at 0.3. Prompt
      wording, so it goes to the designer (see designer notes).
    - **Latency: misses the target for new questions.** The reported 0.60s median was
      misleading: repeats of the same question hit Ollama's prompt cache. First asks were
      2.27 to 2.68s (4 visible samples), against a 1.5s target. Diagnose now reports new
      questions separately, with prefill token counts.
  - **GB10 run 3 (partial paste):** the smaller docs block (k=3, 1,500 chars) scored 20/21,
    the same quality as k=4. Q12 is still the only miss. The coffee-machine case is OK ("not
    sure"). Latency lines weren't pasted.
  - **PM feedback: answers say "the docs say...", which sounds like retrieval, not like the
    assistant knowing things.** Prompt fix (D29, PM-requested edit to the designer's
    prompt), plus the Q12 contact rule. Every diagnose answer is now checked for narration
    (`always_avoid`).
  - **GB10 run 4 (D29 prompt, each question asked 2 times):** 38/42 by the hints. Rescored
    against the recalibrated hints, the only real misses are **2 of 42 answers that still say
    "the docs say/don't say"** (Q12, Q14), down from most answers before. The other two were
    my hints being too narrow ("reaches out" for "follows up"; Q12 pointing to the clubs'
    GatorXperience listings, which is grounded and fine). Hints fixed. Retrieval is unchanged
    and 100%.
  - chat-demo on the new prompt: in character, safe, grounded ("LIB 260"), and it doesn't
    claim to remember the previous visitor.
  - **Latency (run 4):** chat without docs starts in ~0.9 s (PASS). Lab questions take
    ~2.5 s to the first token (MISS; 2.46 s median, 2.82 s in chat-demo), and the gap is all
    prefill (2.07 to 2.45 s). Decode is 22 to 38 tok/s. Ollama's prompt token count includes
    cached tokens, so the old "uncached tokens" label was wrong; fixed. `make prefill-probe`
    decides between (A) the cached system prompt is reused and the ~700 docs tokens are just
    slow to process, and (B) reuse breaks whenever the docs change.
  - **GB10 run 5, `make prefill-probe`:** the prompt is processed at **783 tok/s** with nothing
    cached (2,795 tokens in 3.57 s). Swapping docs took 1.20 s, about 940 tokens of work,
    against ~480 new tokens. A no-docs call with the docs block as the only change took
    1.52 s (~1,190 tokens of work) where ~50 were expected. An identical repeat took 0.19 s.
    So **reuse is partial**: identical prompts are fully cached, but any change re-processes
    roughly 900 to 1,200 tokens, including part of the static system prompt. The probe's first
    verdict ("A") was too coarse; it now reports PARTIAL. Likely cause (unconfirmed): how
    Ollama's cache handles this model (slots, or cache checkpoints if the architecture is
    hybrid). Shrinking the docs alone saves at most ~0.3 to 0.4 s, not enough for 1.5 s.
  - **Cause found (run 6, `ollama show`):** `qwen3.8:27b` is architecture `qwen35` (Qwen3.5
    family). Qwen3.5 is a hybrid model: about 3 of every 4 layers are Gated DeltaNet
    (recurrent state), the rest full attention. A recurrent state can't be rolled back to an
    arbitrary prefix, so inference engines only reuse the cache from saved checkpoints. That
    matches run 5's partial reuse exactly. This is the model's architecture, not our code or
    prompt order. The model also loads a vision projector (clip, 461M) that we don't use.
  - **GB10 run 6b, model comparison on the same 21 questions (one run each, temp 0.7):**
    | chat model | answers ok | first token, new question | prefill median |
    |---|---|---|---|
    | qwen3.8:27b, k=4 | 38/42 (2 narration slips) | 2.48 s median, 2.69 s max [MISS] | 2.08 s |
    | qwen3.8:27b, k=3 / 1,500 chars | 20/21 | 1.74 s median, 2.29 s max [MISS] | 1.29 s |
    | **gemma4:12b**, k=4 | 20/21 (Q18 miss, answer not yet seen) | **1.15 s median, 1.27 s max [PASS]** | 0.63 s |
    gemma4:12b is the only option that meets 1.5 s. It's untested on persona, AI chat,
    and red-team behavior; a smaller model may be easier to jailbreak, and M3 guards + M4
    evals must confirm before any demo. **No config change yet; PM decision.**
  - **GB10 run 7, gemma4:12b (each question asked 3 times, plus chat-demo):** 59/63 answers OK.
    First token: new lab question 1.10 s median, 1.20 s max; chat 0.94 s median, 1.29 s max
    (all PASS). Decode 50.5 tok/s (qwen: ~28). In character, grounded, and it gave ai@sfsu.edu
    with the meeting room (the Q12 fix works on gemma).
    - Q14: 1 real narration slip ("as the docs do not say"). 1 correct answer my hint missed
      ("does not say"); hint fixed.
    - **Q18 hedges:** 2 of 3 answers open "I am not sure about that" and then give the right
      answer (no remote access; overnight jobs only if started in person). gemma follows the
      "not sure" rule more eagerly than qwen.
    - **Stiffer voice:** gemma avoids contractions ("that is what I am here for", "I do not
      know"). The persona wants warm and casual. It also said "Oops, I fumbled that" when
      nothing had gone wrong.
    - Both are prompt-level (designer): "use contractions"; "say 'not sure' only when the
      docs don't answer; if they do, answer plainly".
  - **PM decision pending: switch `chat_model` to `gemma4:12b`?** Not yet tested on red-team
    behavior. M3 guards + M4 evals gate any demo either way.
    Earlier list, still open: Ollama's environment,
    the k3-small latency from run 3, and a diagnose pass with `gemma4:12b` for a quality and
    latency comparison. **No model change without the PM.** M2's functional goal is met;
    merging doesn't depend on latency.

## Next
- Record the measured TTFT, first-sentence time, and tok/s below. If TTFT misses 1.5 s with
  the ~2.2k-token prompt, options are trimming the prompt (designer), checking prefix-cache
  reuse, or a smaller chat model (PM decision).
- Confirm the D27 gate/floor (0.50/0.40) on run 2's 6 negatives. Then check the fixture
  corpus (`make rag-check-fixtures`), because M4's golden set runs on it with the same cutoffs.
- M3 guards plug into the two marked hooks; transcript logging + kill switch with them. Then
  add the `dont_know` line short-circuit for guard-classified lab questions with no docs (D20).
- M4 runner: set `ADA_LAB_DOCS_DIR=evals/fixtures/lab_docs`, run `make ingest-fixtures` first.

## Measured on GB10
| date | chat model | prompt | TTFT median | first sentence | decode tok/s | guard | embed | notes |
|------|-----------|--------|-------------|----------------|--------------|-------|-------|-------|
| 2026-11-10 | qwen3.8:27b | real + RAG k=4 (<=3,000 chars), lab question | 2.46s median, 2.64s max (21 new questions) | ~3.7s (chat-demo) | 22-38 | n/a | 0.11-0.25s retrieval | rag-diagnose run 4; prefill 2.07s median of ~2,700-token prompts |
| 2026-11-10 | qwen3.8:27b | real, no docs (chat) | 0.97s median (chat-demo, 6 turns) | 2.02s median | 27.9 median | n/a | n/a | chat-demo run 4 |

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
- PM: is the AI Lab physically in LIB 260 / the AI Studio? No doc says so. The bot infers
  "the AI Lab is in the DMS, LIB 260" from "resources in the DMS in LIB 260". In one run it
  said "the AI Lab is in the AI Studio", which nothing states. If it's true, the designer adds
  a "Where is the AI Lab?" section; if not, the docs need to say where it is.
- PM: chat `temperature`. Run 2: 0.7 and 0.3 both scored 56/60. 0.3 gave near-identical
  answers on repeats, which is more consistent but more robotic. Recommendation: keep 0.7
  until M4's evals give a bigger sample. No change made.
- PM: **first token for a lab question is ~2.5 s, target 1.5 s.** Cause (run 5): processing
  runs at 783 tok/s and each new question redoes ~900 to 1,200 tokens. Options:
  (1) an Ollama cache setting: unlikely to help much, since the hybrid architecture limits
      prefix reuse in every engine (llama.cpp, vLLM and SGLang have open issues on it);
  (1b) a non-hybrid dense model (a Qwen3-generation dense model, or another full-attention
      model) gets normal prefix caching: only ~500 new tokens per lab question;
  (2) **`gemma4:12b` (run 6b: 1.15 s median, 20/21 answers): the leading option**, your decision;
  (3) a shorter static system prompt (designer, ~2,100 tokens now);
  (4) accept ~2.5 s for lab questions and let the kiosk show its "thinking" state (M5).
  This changes a brief target (section 8), so it's your call.
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
- I added one section to `lab_docs/faq.md`: "How can I get involved if I don't have a
  project yet?" (D28). It only copies facts from join.md and meetings-events.md, and it has
  an HTML comment. Reword it freely.
- **D29 (2026-11-10): at the PM's request I edited `prompts/system_prompt.md`.** I added two
  bullets: speak as if you know lab facts and never mention "the docs"; give the contact the
  docs name when not sure. Please sync `content/persona.md` and reword as you like.
- Prompt suggestions from GB10 run 1 (items 1 and 2 are now covered by D29):
  1. **Most important (the only real miss in run 2).** When the docs don't answer but **name
     a contact** ("contact the AI Commons team, ai@sfsu.edu"), give that contact as well
     as "not sure + lab member". Q12 skipped it 5 of 6 times; Q13 and Q15 included it.
     Your few-shot "not sure" example (no contact) is probably what the model copies.
  2. The bot tells visitors "the docs say / the docs only mention" (Q10, Q12, Q14). That
     breaks character; it should just know or not know.
  3. Overclaims seen: "the team also helps with costs or dues" (the docs only say contact
     them); "the AI Lab is in the AI Studio" (not stated); "Yes, I have robotic dogs" (the
     Commons has them, not the bot).
- The "what room do you guys meet in -> not sure" example didn't hurt in practice (D26).
  It still contradicts the docs, so changing it some time is fine; it isn't urgent.
- The persona says "the AI Lab"; the docs say "Student AI Commons". It held up: Q1 and Q11
  answered correctly.

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
