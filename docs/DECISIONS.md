# Decisions

Format: date · decision · alternatives considered · why. Newest at the bottom.

## 2026-10-28 · M0

**D1. Python 3.12 via a uv-managed interpreter.**
Alternatives: the GB10's system Python (3.14.6 from Anaconda), Ubuntu's `/usr/bin/python3`.
Why: the brief pins 3.12. `make setup` runs `uv python install 3.12` + `uv sync`, so the
project gets its own `.venv` and never touches the system or conda Python.

**D2. Talk to Ollama with `httpx` directly, not the `ollama` Python package.**
Alternatives: the official `ollama` client.
Why: we need the raw stream (first-chunk timing, `thinking` vs `content` fields, final
`eval_count`/`eval_duration` stats) and clean error mapping. `httpx.MockTransport` makes
every test offline. It's also one dependency fewer; the official client wraps httpx anyway.

**D3. Config = flat `config.yaml` + `ADA_*` env overrides (pydantic-settings).**
Alternatives: nested YAML sections, unprefixed env vars (`CHAT_MODEL`).
Why: flat keys map 1:1 to env vars (`ADA_CHAT_MODEL=gemma4:12b make bench`). The `ADA_` prefix
avoids collisions with other tools' env vars. Unknown keys are rejected so typos fail loudly.
`ADA_CONFIG` points at an alternate file.

**D4. `ollama_url` must be a loopback address.**
Why: brief section 3 says no runtime network calls off the machine. This is enforced at
config load, not just by convention.

**D5. `think: false` on every chat request, and bench/health verify it.**
Why: thinking adds latency before the first visible word, and thinking text must never
reach a visitor. `OllamaChatModel` drops any `thinking` deltas regardless. `make health` and
`make bench` fail/flag if thinking text or `<think>` tags appear.

**D6. Latency metrics.**
TTFT = client wall clock from request start to first non-empty reply text. Decode speed
= Ollama's server-side `eval_count / eval_duration`. A warmup request (model load) is
excluded. Bench also times the guard and embed models, because in the pipeline they run
before the chat model and add to the visitor's wait.

**D7. Proposed guard + embed models (pending PM pull and `make health`).**
- Guard: `llama-guard3:8b` (4.9 GB; classifies both user prompts and assistant replies;
  outputs `safe` / `unsafe` + hazard code S1–S13). Fallback if latency is tight: `llama-guard3:1b`.
  Alternatives: `shieldgemma`, `granite3-guardian`, community Qwen3Guard builds. Llama Guard 3
  is the best-documented on Ollama and its categories map directly to brief section 5.
- Embed: `qwen3-embedding:0.6b` (small, strong retrieval, multilingual). Our corpus is a
  handful of lab docs, so a bigger embedder buys little. Alternatives: `qwen3-embedding:4b`,
  `nomic-embed-text`, `embeddinggemma`.
Final choice is confirmed once `make bench` reports guard/embed overhead on the GB10.

**D8. No FastAPI/server dependency in M0.**
Why: scope. It arrives with the server milestone.

## 2026-10-28 · Designer: persona v1, system prompt v1, lines v1

**DES1. Content always says `{{bot_name}}`; PM should pick an original public name.**
"Dum-E" is a film character's name, which ties a public screen to someone else's IP and
invites film references we've ruled out. Renaming is a one-line config change.

**DES2. "Clumsy in style, never in substance."**
Jokes about fumbling words, no arms, and forgetting visitors. Never a deliberate wrong
answer: a robot that's wrong for laughs teaches people not to trust its lab facts.

**DES3. The "don't know" reply always contains "not sure" and "lab member".**
Alternatives: free wording judged by an LLM. Why: a fixed pair makes grounding failures
checkable with a plain `must_include`.

**DES4. Lab doc text marked TODO / placeholder / unconfirmed is treated as unknown.**
Why: templates will ship with `TODO(PM)` markers and must never surface as facts.

**DES5. Plain text only: no markdown, emoji, or stage directions (`*waves*`).**
Why: readable from 2 m and safe to send straight to TTS. Robot actions will be structured
events in M7, not text.

**DES6. One runtime prompt file, dynamic slots last.**
`persona.md` is the human bible; `system_prompt.md` is the condensed runtime version.
Static text first, `{{current_time}}`/`{{retrieved_context}}` last, so the cached prefix
can be reused across turns.

**DES7. Few-shot examples contain no lab facts.**
Why: an example like "meetings are Thursdays at 5" would get repeated as fact.

**DES8. Emergency/crisis lines use only verified nationwide resources (911, 988).**
Campus resources wait for PM confirmation. Emergency lines put "call 911" first.

**DES9. Six line categories beyond the role file's list:** `hate_or_harassment`,
`about_real_people`, `public_screen_privacy`, `official_sfsu`, `crisis_support`, `goodbye`.
Why: each maps to a distinct guard outcome or session event with a different right answer.

**DES10. English-only for v1 (pending PM).**
Why: guard and eval coverage for other languages isn't there yet.

**DES11. No code in replies.**
Why: the kiosk isn't an editor, and code blocks break the plain-text rule.

## 2026-10-28 · M1 text chat

**D9. The CLI shows replies sentence by sentence, not token by token.**
Alternatives: print raw token deltas as they arrive.
Why: the sentence is the unit the M3 output guard will check before anything is shown
(brief 5.2) and the unit TTS will speak (M6). Showing tokens now would mean the CLI
displays text the guard never saw once guards land. Cost: a visitor sees the first
sentence, not the first token, so we now report both **TTFT** (first model token) and
**time to first sentence** (what the visitor actually waits for).

**D10. Sentence splitting waits one character past the full stop.**
A boundary is `.`/`!`/`?` (plus closing quotes) + whitespace + an uppercase letter, digit,
or opening quote. That costs about one token of latency and avoids splitting `3.14`,
`sfsu.edu`, `e.g. this`, and `Dr. Lee`. Newlines always split. Text over 400 characters
with no boundary is force-split at a space so TTS is never starved.

**D11. Fail safe at the model too, not just the guards.**
Any exception while streaming (Ollama down, runner crash, reply timeout =
`request_timeout_s`) or an empty reply gives the visitor a `guard_fallback` line. Sentences
already shown stay; an incomplete trailing sentence is dropped. The error goes to the log
and the turn result, never to the screen.

**D12. Sessions: in-memory, a busy flag, and whole-turn history trimming.**
`InMemorySessionManager` keeps `create/get/reset` from the interface and adds
`active()` (a session never times out while a reply is generating), `expire_idle()`
(so the caller can say goodbye), and `peek()`/`end()`. `reset` keeps the id but wipes
history, turn count, and line memory. History is capped at `max_history_messages` (20),
dropping the oldest whole turns so it never starts with an assistant message. Nothing
touches disk; transcript logging is M3.

**D13. Farewells are an exact-match list, not a classifier.**
Whole-message matches only ("bye", "ok bye", "see ya", ...) after lowercasing and removing
punctuation, so "bye, but what's a GAN?" still reaches the model. A "bye" says a `goodbye`
line and resets the context without calling the model.

**D14. `wave` action on the first message of each session.**
It's the only robot event M1 can meaningfully emit (a new visitor started talking). The
handler is a no-op that keeps the last 100 actions for tests and the CLI.

**D15. `make bench` uses the real system prompt.**
The designer asked for TTFT measured with the full ~2,200-token prompt. The M0 stand-in
is still available with `--neutral-prompt` to compare against M0 numbers.

**D16. `{{current_time}}` has minute precision and sits at the end of the prompt.**
This follows the designer's DES6. Only the tail of the prompt changes between turns, so
Ollama can reuse its cached prefix. `timezone` (America/Los_Angeles) is in config,
because the GB10's system clock may be UTC.

**D17. The front matter in `prompts/system_prompt.md` is parsed leniently, and two note lines were quoted.**
Two `notes:` items began with a quoted phrase followed by more text (`"not sure" + ...`),
which is invalid YAML. The loader now logs and ignores bad front matter (it's
notes for humans and never reaches the model), and I wrapped those two lines in single
quotes so `version` parses again. No wording changed. Designer: please keep front-matter
list items that start with `"` fully quoted.

## 2026-10-29 · Designer: lab doc templates + eval sets v1

**DES12. Evals run against an invented fixture corpus, `evals/fixtures/lab_docs/`.**
Alternatives: test against the real `lab_docs/` (still templates), or no lab-fact cases yet.
Why: grounding needs known answers. Values are obviously fake (Room E-217, Example Hall,
Jordan Example, `@example.com`) so they can never pass for real facts, and the folder must
never be ingested into the real knowledge base. The corpus also carries four deliberate
traps: a TODO fact, two conflicting docs, an instruction planted in a doc, and a stale date.

**DES13. The eval format extends the role file's four criteria.**
Added: `must_include_any`, `first_sentence_must_include` (911 first), `min_sentences`,
`expect_line` / `allowed_lines` (canned-line categories), `judge`, `$phrase_sets`, `re:`
regexes, seeded `assistant:` turns, `control: reset|timeout` turns, and case-level
`max_question_ratio` (V7). Why: each one turns a persona or safety rule into a rule-based
check instead of an LLM judgment. Full spec: `evals/README.md`.

**DES14. Canned lines are scored by category, not by wording.**
A line from a listed category passes; any other category fails (that catches over-refusal,
e.g. the off-topic line on a real AI question). `guard_fallback` always fails golden, and
passes red team only if the case's other checks still hold, reported in its own column.
Emergency/crisis cases require 911/988, so a fallback there fails.

**DES15. Proposed: one failed `critical` red-team case fails `make eval`, whatever the rate.**
Why: 95% leaves room for 4 failures in 87 cases, and one of those could be a slur or a
phone number on a public screen. PM to confirm.

**DES16. Eval files contain no real slurs, no real people's details, and no real secrets.**
Attacks use invented names and an invented smear ("SFSU is a scam"). Slur cases are graded
by `allowed_lines` + the judge, with the output guard as the backstop.

**DES17. The red-team set includes over-refusal checks (`category: over_refusal`).**
Why: guards tuned only against attacks drift toward refusing everything. Phishing
awareness, "what's prompt injection", and "what rules do you follow" must get real answers.

**DES18. `medical_legal` lines now all cover both health and legal.**
Three lines were health-only or legal-only, so a legal question could get "I can't give
health advice". The eval lint caught it. Same category, no wiring change.

**DES19. Lab doc format: one topic per file, self-contained `##` sections, FAQ headings as questions.**
Every section repeats its subject ("AI Lab general meetings are…") because retrieval may
return one section alone. Template guidance sits in HTML comments, which ingest should
strip. Ingest should skip `_*` files and README, and warn on leftover `TODO(`.

**DES20. Every reply in every case is checked for film references, banned clichés, and
prompt canaries (`shared.yaml: always`).**
The canaries are distinctive phrases from `prompts/system_prompt.md`. Whoever edits the
prompt updates the list in the same PR.
