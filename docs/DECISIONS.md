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
*Update 2026-10-29:* PM confirmed `qwen3-embedding:0.6b` (measured ~0.11 s overhead on the GB10).
Switching to the 4b variant needs `make rag-check` numbers first (D23).

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

## 2026-10-29 · M2 lab knowledge (RAG)

**D18. The retrieval index is a JSON file with a linear scan, not Chroma.**
Alternatives: Chroma (role-file default), FAISS, sqlite-vec.
Why: the corpus is a few dozen `##` sections (the fixture corpus is 30). Scoring them all in
pure Python takes well under a millisecond, so a vector DB buys nothing and adds a large
dependency tree (aarch64 wheel risk) and, for Chroma, telemetry that is on by default (the
brief forbids telemetry). The file records the embed model, vector size, an index format
version, and a SHA-256 of every source doc. It is written atomically. If the corpus ever grows
past a few thousand sections, swap the store behind `Retriever`; nothing else changes.

**D19. Sections that still contain `TODO(` are left out of the index. (PM approved 2026-10-29.)**
Unconfirmed facts must never reach a public kiosk; that is the grounding rule (brief 5.3).
Ingest drops any `##` section whose heading or body contains `TODO(`, so a placeholder can't
be quoted, and safety doesn't depend on the model obeying "treat TODO as unknown".
Kept visible: `make ingest` ends with a "LEFT OUT, unconfirmed" list of every dropped
section, grouped by file with its heading, so the PM can see what still needs confirming.
To publish a section, confirm or delete its TODO, then re-run `make ingest`.
Alternatives: warn only (the lab_docs README's original ask), or drop only the TODO sentence
(leaves half-sentences like "Hoodies cost").
Designer: golden `dk_todo_hoodie_price` now passes because no hoodie doc is retrieved, not
because the model saw TODO and declined. The prompt rule stays as a backup.

**D20. Grounding = cosine cutoff + empty docs block + the designer's prompt rule.**
Only chunks with similarity >= `rag_min_score` reach `<lab_docs>`, top `rag_top_k` (4),
capped at `rag_max_context_chars` (3,000; the best chunk is always kept). With nothing above
the cutoff the block is empty, and the system prompt makes the model reply with "not sure" +
"lab member", the same fixed phrases as the `dont_know` lines. The 0.35 default is a
placeholder until `make rag-probe` reports real scores on the GB10.
Not done, deliberately: a code short-circuit that returns the canned `dont_know` line for a lab
question with no docs. Without M3's input routing it would misfire on attacks that mention the
lab or SFSU and expect a different line (e.g. redteam `is_president_here_now` -> `personal_info`,
`sfsu_endorses_lab` -> `official_sfsu`, `crack_lab_wifi` -> `unsafe_request`). Revisit in M3:
once the input guard has classified a message as a benign lab-fact question, "no docs" ->
`dont_know` line is safe and saves a model call.

**D21. Retrieval details.**
- Query = the visitor's message. If it finds nothing and there is an earlier visitor message
  in the session, retry once with both joined, so "and where is it?" still finds the meeting doc.
- Qwen3-Embedding gets an instruction prefix on queries only (`rag_query_instruction`).
  Documents are embedded as title + heading + body.
- Fail safe: an embedding error, a model swap (vector size mismatch), or a timeout
  (`rag_timeout_s`, 5 s) -> no docs for that turn, the error is logged on the turn result, and
  the visitor still gets a reply (which says "not sure" for lab facts).
- A missing or wrong-model index doesn't stop `make chat`: it runs with no docs and prints a
  warning. `make health` reports it as FAIL, and also FAILs if `lab_docs_dir` points at the
  invented eval corpus.

**D22. One index per docs folder, and visitors can't forge a docs block.**
`data/rag/lab_docs.json` and `data/rag/evals_fixtures_lab_docs.json` are separate files
named after the folder, so evals (`ADA_LAB_DOCS_DIR=evals/fixtures/lab_docs`) can't write
invented facts into the kiosk's index. `data/` is gitignored. `<lab_docs>` / `</lab_docs>`
typed by a visitor are rewritten to `[lab_docs]` / `[/lab_docs]` before the model sees them,
the same escaping already applied inside retrieved chunks (redteam `fake_lab_docs_tag_in_input`).

**D23. A retrieval check gates the embed model: `make rag-check`.**
`evals/retrieval.yaml` lists test questions per docs folder, each with the file(s) that should
come back (or `expect_none` for "nothing should pass the cutoff"). `make rag-check` embeds
only the questions (no chat model) and reports recall@k after the cutoff, hit@1, recall@k
ignoring the cutoff, and MRR, plus a per-question diagnosis:
- **cutoff too high**: the right file ranked in the top k but scored under `rag_min_score`.
  Fix: lower the cutoff.
- **cutoff too low**: an `expect_none` question let something through. Fix: raise it.
- **embedding miss**: the right file ranked below k. Fix: doc wording/headings first, then
  a bigger embed model.
Pass bar: 90% recall@k and every `expect_none` case clean. If real-doc recall misses that
because of embedding misses, I'll run the same check with `qwen3-embedding:4b` into a separate
index (`ADA_EMBED_MODEL=... ADA_RAG_INDEX_DIR=data/rag-4b`) and bring both sets of numbers,
plus the 4b embed latency from `make bench`, before proposing a switch.
The real-doc suite (17 questions) was drafted against the designer's unmerged
`task/des-lab-docs-content`; every expected file exists there and survives ingest.
Retrieval-only questions about TODO sections aren't in it (there is no right doc to find);
`make chat-lab` covers how the bot answers those.

**D24. `Source:` lines stay in chunks but are left out of the embedding.**
The designer ends every section with a `Source:` line (DES23) so the model can attribute
facts. URLs and dates add noise to a vector without adding meaning, so `embed_text` skips
lines starting with `Source:` / `Sources:`. The model still sees them in `<lab_docs>`.

## 2026-10-29 · Designer: real lab docs v1 (from the PM's research)

**DES21. The knowledge base now covers SFSU offices, not just the lab.**
The PM's research includes campus services, the academic calendar, Academic Technology, the
MakerSpace, AI programs, and AI policies. These live in their own files (`sfsu-*`,
`academic-technology.md`, `dms-*`) and are written as attributed statements ("According to
the Registrar..."), so the assistant relays and points instead of speaking for SFSU.
Follow-up: system prompt rule 4 still says "don't state SFSU policy". It needs a line
allowing attributed relaying of these docs. That's a prompt change, so it waits for
`make eval` (brief 5.9).

**DES22. Unconfirmed facts get `TODO(PM): confirm` on the sentence; conflicts are never written twice.**
When two sources disagree, the doc either states only the safe version and explains in an
HTML comment (shuttle discontinued; hackathon "early October"; colleges listed without a
count), or marks it TODO. Writing both values would make the assistant say "not sure"
anyway, and invites it to pick one.

**DES23. Every section ends with a short visible `Source:` line (domain/path, source date, checked date).**
Alternatives: full URLs, or sources only in comments. Why: the model needs the source to
attribute ("SFSU's AI site says..."), but long URLs are useless on a kiosk and awkward for
TTS later. Full URLs stay in the PM's research files.

**DES24. Deliberately left out of the docs:**
the AI Commons Coordinator's name (undated LinkedIn post, no consent); the College of
Business AI Hub GB10 systems (not connected to the Commons); the 415-388-2222 typo on one
health page; AI-detection guidance (conflicting, and not kiosk material); the raw join-form
URL (pointed to ai.sfsu.edu/ai-students instead).

**DES25. "AI Lab" ↔ "SFSU Student AI Commons" stays a TODO until the PM confirms it.**
The research couldn't establish that the student-founded AI Lab is the Commons. Until
confirmed, the assistant can describe the Commons and the AI Studio but will say it's not
sure how they relate to the AI Lab. If they're the same, the system prompt and persona
should probably use the Commons' name too.

## 2026-11-10 · Designer: real lab docs v1 restructure (`task/des-lab-docs-v1`)

**DES26. Twelve files, one topic each, per the PM:** `about`, `join`, `meetings-events`,
`projects`, `equipment`, `rules`, `roles`, `faq`, plus `sfsu`, `academic-technology`, `dms`,
`ai-policy`. The six `sfsu-*` files and two `dms-makerspace*` files are merged into `sfsu.md`
and `dms.md`/`rules.md`. `sfsu.md` (~1,500 words) breaks the ~600-word guide on purpose;
every `##` section stands alone, so retrieval isn't affected.

**DES27. The PM confirmed the research except CONFLICT / PRIVATE? items. TODOs now exist only for conflicts.**
Gaps (things no source covers: founding date, dues, GPU policy, officers...) are simply not
written, so the assistant says "not sure". They're listed in STATUS.md, not as TODOs.
No PRIVATE? items existed in the research.

**DES28. Ingest drops any `##` section containing `TODO(`** (PM rule). So every TODO sits in
its own section, and confirmed facts never share a section with one. This replaces the
"warn on leftover TODO" note in the old README. Safety-critical facts (University Police
415-338-2222) are in confirmed sections, never next to a TODO.

**DES29. Templates moved to `docs/lab_doc_templates/`**, outside `lab_docs/`, so no ingest
bug can index them.

**DES30. The docs describe the SFSU Student AI Commons and the AI Studio; they never say "the AI Lab is the Commons".**
The research still doesn't establish that relationship (DES25 stands). Also dropped vs v1:
the "robot assistant" and GB10 sections, which came from the brief, not the research, and
the AI Commons Coordinator's name (a personal LinkedIn; the role is kept without a name).

**DES31. PM answers applied (2026-11-10); `lab_docs/` has no TODOs left.**
PM-supplied facts cite "AI Lab PM (Atharva), confirmed 2026-11-10" as their source. The AI
Lab/Commons relationship now comes from the PM, superseding DES25/DES30. The AI-detection
section was removed rather than answered (PM: "no comments"), so the bot says it's not sure.
The old Discord invite was removed everywhere. Ethan Cortes is named (public AT directory
listing, provided by the PM); student officers still need consent before being named.
The GB10 is back in the docs as Commons checkout equipment; the College of Business AI Hub
GB10s remain separate.

## 2026-11-10 · M2 on the real docs (`task/m2-real-docs`)

**D25. A per-question diagnostic for real docs: `make rag-diagnose`.**
`make rag-check` says whether the right file came back; it can't say why a reply is wrong.
`rag-diagnose` runs each question in `evals/real_docs_qa.yaml` through the real pipeline on a
fresh session and prints the top 3 chunks with scores, what reached the prompt, the answer,
and a hint: `retrieval` (the expected doc never reached the prompt: cutoff, embedding, or doc
wording) or `answer` (it did, and the model still missed a fact, said "not sure", or invented
a link). Hints are substring/regex checks, not scoring; M4's runner and judge own pass/fail.
Also: `make ingest-report` lists every section ingested, dropped (TODO / empty heading), and
every skipped file with its reason. Empty sections used to vanish silently.

**D26. Prompt changes are tested as side-by-side copies before the designer adopts them.**
`prompts/experiments/` holds engineer-made variants that config never points at. The first:
the "what room do you guys meet in -> not sure" example swapped for an undocumented
question, because the docs now answer the meeting room. Compared with
`ADA_SYSTEM_PROMPT_FILE=... make rag-diagnose`. Adopting a variant is the designer's
change to `prompts/system_prompt.md`, followed by `make eval` (brief 5.9).
**Result (GB10, 2026-11-10): no gain, variant deleted.** Baseline 17/20 vs variant 15/20.
With the docs in the prompt, the baseline already answered "where do meetings take place"
from them, so the old example did no harm. The variant's extra misses (one wrong hours
comparison, one dropped contact) look like sampling noise at temperature 0.7, which is why
`rag-diagnose` now has `--repeat`.

**D27. Two-tier retrieval cutoff: a gate (0.50) for the best chunk, a floor (0.40) for the rest.**
Real-docs scores (GB10, qwen3-embedding:0.6b, 2026-11-10): every answerable question's best
chunk scored 0.560 to 0.847; the two "nothing answers this" questions scored 0.389 and 0.432,
so the old single 0.35 cutoff let both through. One cutoff can't fix that: raising it to
0.50 would also drop supporting chunks the answer needs (the DMS hours, 0.496, in "are AT's
hours the same as DMS's?"). So `rag_min_score` (0.50) now gates the turn: if the best chunk
is under it, no docs at all. Once past the gate, chunks down to `rag_extra_min_score` (0.40)
can join. `rag_extra_min_score` unset = old single-cutoff behavior (fixture tests use that).
Margins are about 0.06 to 0.07 either side of the gate on only 2 negatives, so the real-docs
check now has 6 "nothing answers this" questions and 6 casually worded answerable ones.
Re-tune if those overlap. The prompt rule ("not sure" + "lab member") stays as the backup.
Run 2: 5 of 6 negatives screened out. "Does the lab have a coffee machine?" scored 0.549
against equipment.md, above the gate and only 0.011 under the lowest answerable question
(0.560). That's a real overlap: "does the lab have X" sits next to the equipment docs in
embedding space whatever X is. Raising the gate to catch it would be fitting to one
question. So the gate is a coarse filter, and for "does the lab have X?" the grounding
guarantee is the prompt rule. The case moved to `real_docs_qa.yaml` (case 20), where the
bot's reply ("not sure", never "yes") is what's checked.

**D28. A real-docs FAQ section added by ENG: "How can I get involved if I don't have a project yet?"**
"How can I get involved without an existing project?" pulled only the project-proposal
sections (nothing else is phrased that way), so the bot said "not sure", even though the docs
cover joining, events, clubs, and the Leadership Committee. Retrieval can't fix missing
phrasing; a doc section can. Every fact in it is copied from `join.md` and
`meetings-events.md`, with their `Source:` lines. No new facts. An HTML comment flags it for
the designer, who owns the wording.

**D29. The bot speaks as if it knows lab facts, never "the docs say". (PM request, 2026-11-10; ENG edit to a designer-owned file.)**
In runs 1 to 3 many correct answers opened with "The docs say..." or "The docs don't
mention...". The PM: the assistant should speak like it knows things, not like it's
retrieving them. Nothing in the prompt said otherwise, and the docs block is literally
labelled `<lab_docs>`, so the model narrated it. Two bullets added to `prompts/system_prompt.md`
under "Lab facts come only from the lab docs". Nothing else was reworded, and no grounding
rule was loosened:
1. Talk about lab facts as things you simply know. Never mention docs, documents, notes,
   sources, context, or looking things up. When attribution helps, name the real source
   ("SFSU's AI site"), which keeps rule 4: it still doesn't speak for SFSU.
2. When not sure but the docs name a contact for the topic, give it alongside "lab member"
   (Q12 skipped ai@sfsu.edu 6 of 7 times).
The "not sure" + "lab member" fixed phrases are unchanged. `real_docs_qa.yaml` gains
`always_avoid`, which checks every answer for narration. Designer: please sync
`content/persona.md` (the prompt front matter says to keep them in sync) and reword freely.
Run 4 result: narration fell from most answers to 2 of 42 at temperature 0.7. What's left
is a designer item: a few-shot pair showing a docs-grounded answer in a natural voice would
likely finish it, but an example can't contain real lab facts, so it needs care.
Brief 5.9 says prompt changes pass the eval suite before merging. M4 doesn't exist yet, so
the gate for now is `make rag-diagnose` plus `make chat-demo` (persona and safety smoke run).
`make eval` follows when M4 lands.

## 2026-11-12 · M3 guardrails (`task/m3-guardrails`)

**D30. Two guard chains, rules first, guard model second, one place to build them.**
`app/guards/`: `InputRules` / `OutputRules` (regex + allowlist, microseconds) then
`LlamaGuard` (`guard_model`, Ollama), wrapped in `GuardChain("input" | "output")`. A rule
block skips the model call. `build_guards()` and `app/factory.py:build_pipeline()` are the
only constructors the app uses; `ChatPipeline` takes the guards and the kill switch as
**required** arguments, and app code has no allow-everything guard (tests build real
chains around a fake guard model). Patterns, limits, the blocklist, and the block -> line
mapping live in `content/guard_policy.yaml` (data, not code).
Alternatives: guard model only (slower, misses injections Llama Guard calls "safe": it
judges harm, not instruction-following); a single combined guard (can't skip the model).

**D31. Fail safe = block. Every guard failure shows a canned line, never the text.**
Guard model error, timeout (`guard_timeout_s`, 3 s), an answer that isn't `safe` /
`unsafe\nS..`, a broken rule, or an unreadable kill-switch file: all block. Cancellation is
never turned into "allowed". Llama Guard is called without the `think` field (it has no
thinking mode) at temperature 0.

**D32. Block -> line mapping (designer may edit `lines:` in guard_policy.yaml).**
Input: injection -> `jailbreak`; blocklist -> `hate_or_harassment`; visitor's own phone /
email / student ID -> `public_screen_privacy`; Llama Guard S1/S2/S9 -> `unsafe_request`;
S3/S4/S10/S12 -> `hate_or_harassment`; S5 -> `about_real_people`; S6 -> `medical_legal`;
S7 -> `personal_info`; S11 -> `crisis_support`; S13 -> `off_topic`; S14 -> `jailbreak`;
S8 (IP) -> `unsafe_request` (TODO designer). Output: everything -> `guard_fallback`, except
S11 -> `crisis_support` and S6 -> `medical_legal` (a crisis reply blocked for mentioning
self-harm must still give 988, not "I fumbled that"), and a prompt-canary leak -> `jailbreak`.
Several codes: priority S11 first (list in the file). Too-long messages map to `too_long`,
which lines.yaml lacks: **TODO(designer)**, falls back to `guard_fallback` until added.

**D33. Canned lines skip the guard model.** They're the guards' own output, written and
reviewed by the designer; guarding them would add latency to every refusal and could turn
an emergency line into "I fumbled that" on a false positive. A test checks every canned
sentence passes the output *rules*. Everything else the sink receives is model text the
output guard approved first (property test over 9 failure scenarios).

**D34. A blocked message never enters the chat history.** Otherwise the chat model reads the
attack on the next turn, which breaks "input guard runs before the model sees anything".
The history gets `[message withheld by the safety filter]` + the canned reply instead, so a
follow-up ("thanks") after a crisis line still has context. Visitor text is normalized
(NFKC, zero-width characters removed) once; guards and model see the same string.

**D35. Output guard per sentence, pipelined; input guard optionally parallel with retrieval.**
The output guard checks sentence N (rules + model, with the whole reply so far and the
question as context, so split-up content is judged together) while the model writes N+1.
On a block, generation is cancelled; sentences already shown stay, the blocked one and
everything after it are never shown. `guard_parallel_retrieval` (default **false**) embeds
the question while the input guard runs. Safe: only the embed model sees the text early;
the chat model, sink, and history wait for the guard; a block cancels retrieval. Off by
default until the PM sees `make m3-latency`.

**D36. Contacts allowlist built at ingest.** `make ingest` writes
`data/rag/<docs>.contacts.json` (phones, emails, hosts, links in the indexed sections, so
TODO sections stay out) and prints counts (`make ingest-report` lists them). Runtime merges
in contacts from `content/lines.yaml`. Missing file -> built from the docs with a warning.
Rules: phone/email must match exactly (7-digit campus forms match a listed number's tail);
a bare domain is fine if the host appears in the docs; a link with a path must match
exactly, so invented deep links are blocked. Consequence to watch: AI-chat answers that
name a site not in the docs ("pytorch.org", "huggingface.co") get blocked -> `guard_fallback`.
That's what was asked; if the probe shows it hurting AI chat, options are a small
`allow_hosts` list in guard_policy.yaml or stripping the link instead of blocking.

**D37. Transcripts: text only, local, one JSONL file per visitor, 30 days.**
`data/transcripts/YYYY-MM-DD/HHMMSS-<session>-v<n>.jsonl`, dirs 0700, files 0600. A visit
ends on goodbye / reset / timeout; the next message opens a new file. Each turn records the
visitor text, what was shown, source, every guard decision (stage, code, line, timings),
the blocked sentence (never shown), docs used, timings. Phone numbers, emails, and student
IDs the visitor types are redacted unless they're on the allowlist; guard reasons never
contain the contact itself. Retention `transcript_retention_days: 30`, pruned at startup and
by `make transcripts-prune`. 30 days confirmed by PM 2026-11-13 (D43); **who may read the logs is still open**
(designer open question 7). Scripted runs (`chat-demo`, `chat-lab`, diagnose, probes) don't
write transcripts. A write failure is logged and the visitor still gets the reply.

**D38. Kill switch: config flag OR state file, checked per message and per sentence.**
`out_of_order: true` (or `ADA_OUT_OF_ORDER=1`) forces it on. Otherwise
`data/kill_switch.json` decides; `make kill-on/kill-off/kill-status` and
`POST /admin/kill-switch` write it atomically and append to `kill_switch.json.log`
(who/when/why). The pipeline stats the file before each message and each sentence, so a
running `make chat` / kiosk goes out of order mid-reply. While on: `out_of_order` line, no
guards, no model, nothing remembered. The state survives restarts on purpose (a reboot must
not undo staff's decision). Unreadable file = ON.
Slack (M5.5): the bot runs on the GB10 and calls the localhost endpoint with
`ADA_ADMIN_TOKEN`; staff-only checks happen in the bot (Slack user/channel allowlist), and
`by` records the Slack user.

**D39. Admin endpoint: FastAPI on 127.0.0.1, bearer token required, no docs pages.**
`make admin` (port 8765). No token configured -> every request is 503; there's no open
mode. `admin_host` must be loopback (validated). The M5 server will mount the same router.
New dependencies `fastapi` + `uvicorn` (tech defaults; pure Python, aarch64-safe).
fastapi 0.162 pulls in `opentelemetry-api`: the API alone is a no-op with no SDK or
exporter, so nothing is collected or sent; `serve` also sets `OTEL_SDK_DISABLED=true`.

**D40. The app needs three resident models; probes report what Ollama has loaded.**
Chat + guard + embed is exactly Ollama's default `OLLAMA_MAX_LOADED_MODELS` (3 per GPU). In GB10
run 1 a fourth model (the other chat model, kept warm by `keep_alive: 30m`) made Llama Guard
time out on every call. The guards failed closed as designed, but nothing could be measured.
`make health`, `m3-probe`, and `m3-latency` now print `ollama ps` and warn about extra models.
Fix on the machine (PM's call): unload unused models, or raise the limit to 4.

**D41. Cold-start fix: load models before the first visitor; a timed-out guard call keeps running.**
GB10 run 2 showed Llama Guard never in `ollama ps`: a cold load takes longer than
`guard_timeout_s` (3 s), the chain cancelled the request, and Ollama abandons a load whose
request is cancelled. So every call restarted the load and timed out; the bot answered
everything with fallback lines (fail-safe, but useless). This also explains run 1's gemma
failures (guard evicted by a 4th model, then never reloaded). D40's eviction theory was the
trigger, not the cause. Fixes:
1. `factory.warm_up()` loads guard, chat, and embed with no guard timeout (`/api/chat` with
   empty messages). `make chat`, the probes, and the gb10 tests call it; the M5 server will too.
2. `LlamaGuard` shields its request: when the chain times out, the visitor still gets the
   blocked/fallback line, but the request keeps running so the load completes. While that
   orphaned call runs, new calls fail fast (blocked) instead of piling up.
The timeout stays 3 s and still blocks; nothing unchecked is shown.

**D42. One num_ctx per model on every call, warm-up included.**
GB10 run 3: the warm-up load sent no options, so qwen reloaded at Ollama's default context,
which on a 128 GB box is huge. The bigger KV cache pushed Llama Guard out right after warm-up
(`ollama ps` after warm-up: no guard), and the app's next call (num_ctx 8192) reloaded qwen
again (10.9 s first token). Ollama reloads a model whenever num_ctx changes. Now every call
to a model uses the same num_ctx: chat `num_ctx` (8192) and new `guard_num_ctx` (4096) in
the pipeline, warm-up, health, and bench (`guard_options()`). A test checks every request
in health + warm-up + a turn uses one num_ctx per model.

**D43. Chat model gemma4:12b, parallel retrieval on, 30-day transcripts (PM, 2026-11-13).**
Measured with both guards on (GB10 runs 3-4, 8 new questions per mode):

| chat model | retrieval | first token (median) | first sentence shown (median) |
|---|---|---|---|
| qwen3.8:27b | sequential | 2.11 s | 3.45 s |
| qwen3.8:27b | parallel | 1.18 s | 2.69 s |
| gemma4:12b | sequential | 1.46 s | 2.27 s |
| **gemma4:12b** | **parallel** | **1.05 s** | **1.92 s** |

gemma4:12b with parallel retrieval is the only setup under the brief's 1.5 s first-token
target. Red-team probe (15 cases): both models 15/15, 0 must_not_include hits. M2 run 7 had
gemma at 59/63 on lab questions. Brief section 3 still says "Qwen 27B-class": PM to update
the brief. Brief 5.9 (evals before merging a model change): M4 doesn't exist, so the gate is
the M3 probe + gb10 tests + `make chat-lab` on gemma (run 5).
Remaining gap: the first *sentence* is ~1.9 s because the output guard (~0.4 s) runs on it
before it is shown. Options if that matters: llama-guard3:1b (model change, PM call), or a
designer prompt tweak for shorter opening sentences.
