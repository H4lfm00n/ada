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
