# AI Lab Assistant — Project Brief

> This file is the shared source of truth for every agent working on this repo
> (software engineer, designer). Read it fully before doing any work.
> The project manager (PM) is Atharva. When this file and a task conflict, ask the PM.

## 1. What we're building

An interactive, on-premise AI assistant for the **AI Lab at SFSU**: a friendly lab
"robot" character people can walk up to and talk with. Name: **Ada** (AY-duh), an
original character (PM decision, PHASE2-SPEC 0.1; the old working name is retired);
the name is a configurable setting (`bot_name`), not hardcoded.

It should:
- Greet visitors and make the room feel alive and fun.
- Answer questions about the lab: what it is, how to join, meetings, projects, equipment.
- Chat about AI/CS topics at a level a curious student would enjoy.
- Stay safe, on-topic, and in character, in a public room full of CS students who
  will try to break it.

Later phases will add a voice interface and a physical robot arm (SO-ARM101).
**Neither is in scope now**, but the architecture must leave clean hooks for both.

## 2. Current phase: typed MVP, then voice

**Phase 2 is in scope. `claude/PHASE2-SPEC.md` (Project docs) is the detailed spec and wins
where it is more specific than this brief.**

In scope now:
- Text chat core (persona, conversation, session handling).
- Lab knowledge via retrieval (RAG) over docs the lab maintains.
- Layered guardrails (input filter, prompt rules, grounding, output filter).
- Evaluation harness (red-team + golden-conversation tests).
- Kiosk web UI: light theme, Ada's orb, idle acts, captions.
- Interactive cards from the approved catalog (`content/cards.yaml`), including QR cards and
  the "did you scan it?" check-in loop. The model never invents URLs.
- Voice, right after the typed MVP ships: Ada speaks (TTS), listens after "Hey Ada" (AY-duh)
  or a tap (wake word + STT), then barge-in. Audio is never stored.

Out of scope (leave stubs/interfaces only):
- Robot arm control, camera, gestures. No robot-arm teasing in Ada's content (PM, 2026-11-18).
- Fine-tuning. We'll use system prompt + RAG first and only fine-tune
  (LoRA) once evals show we need it.

## 3. Hardware and runtime

- **Machine:** NVIDIA GB10 (DGX Spark-class). **ARM64 / aarch64**, Ubuntu-based DGX OS,
  **128 GB unified memory** shared by CPU and GPU, ~273 GB/s memory bandwidth.
  - Always check Python packages have aarch64 wheels or build from source cleanly.
  - Memory is plentiful; **bandwidth is the bottleneck**. Dense model decode speed is
    limited. Measure tokens/sec; don't assume.
- **Model serving:** Ollama, already installed, API at `http://localhost:11434`.
- **Chat model:** set in config (`CHAT_MODEL`; currently gemma4:12b for latency). **The exact tag lives in config**
  (`CHAT_MODEL`). Run `ollama list` to get the real tag; never hardcode it.
  - Disable "thinking" mode for user-facing replies (latency); verify how with the
    installed Ollama version.
  - Verify tool calling works with the exact tag before depending on it. Phase 1
    must not *require* tool calling.
- **Guard model:** a small safety classifier served by Ollama (e.g. a Llama Guard
  variant). Configurable via `GUARD_MODEL`.
- **Embeddings:** an Ollama embedding model, configurable via `EMBED_MODEL`.
- **Everything runs locally.** No cloud APIs, no telemetry, no external calls at runtime.

## 4. Architecture (target)

```
kiosk UI (browser) ──websocket──► FastAPI server
                                     │
                         ┌───────────┴───────────┐
                         ▼                       ▼
                  Session manager          Admin endpoints
                         │                 (health, kill switch)
                         ▼
     ┌──────── Conversation pipeline ────────┐
     │ 1. input guard (rules + guard model)  │
     │ 2. retrieve lab docs (RAG)            │
     │ 3. build prompt (persona + context)   │
     │ 4. stream from chat model (Ollama)    │
     │ 5. output guard (guard model + rules) │
     │ 6. emit text + events                 │
     └───────────────────────────────────────┘
                         │
                         ▼
             Event bus (future hooks):
             on_reply_sentence → TTS (later)
             on_action("wave") → robot (later)
```

Key design rules:
- **Content is data, not code.** Persona, system prompt, deflection lines, eval sets,
  and lab docs live in `content/`, `prompts/`, `evals/`, `lab_docs/` as Markdown/YAML,
  loaded at runtime. The designer edits these without touching Python.
- **Every stage is swappable** behind a small interface (chat model, guard, retriever,
  output sink), so voice and robot plug in later without rewrites.
- **Streaming end-to-end.** Replies stream sentence by sentence (voice will need this).
- **Fail safe.** If any guard errors or times out, return a canned in-character
  fallback line. Never pass unchecked text through.

## 5. Guardrail principles (non-negotiable)

1. Input guard runs before the chat model sees anything.
2. Output guard runs before anything is shown (or later, spoken).
3. Lab facts come **only** from retrieved docs; otherwise: "I'm not sure, ask a lab member."
4. Never claim to speak officially for SFSU. Never share personal info about anyone.
5. No medical/legal/emergency advice; point to real resources.
6. Session context resets on inactivity timeout or explicit reset (new visitor = fresh context).
7. Log **text transcripts only**, with no audio and no video. Logs stay on the machine.
8. A kill switch (admin endpoint + config flag) instantly puts the assistant in
   "out of order" mode.
9. Any change to prompts, models, or guards must pass the eval suite before merging.

## 6. Repository layout (target)

```
/app            Python package (server, pipeline, guards, rag, sessions)
/prompts        system prompt templates (Markdown)
/content        persona bible, deflection lines, fallback lines (Markdown/YAML)
/lab_docs       knowledge base the lab maintains (Markdown)
/evals          golden.yaml, redteam.yaml, eval runner outputs
/ui             kiosk web UI (static HTML/CSS/JS)
/docs           DECISIONS.md, ARCHITECTURE.md, design specs
/tests          pytest
CLAUDE.md       this file
```

## 7. Milestones

| ID | Milestone | Owner | Done when |
|----|-----------|-------|-----------|
| M0 | Env + skeleton | Engineer | Repo runs on GB10, config loads, Ollama health check passes, tokens/sec measured for chat model |
| M1 | Persona + text chat | Designer → Engineer | CLI chat in character, streaming, session reset works |
| M2 | Lab knowledge (RAG) | Designer (docs) + Engineer | Answers lab questions from docs; says "not sure" when docs don't cover it |
| M3 | Guardrails | Engineer + Designer (lines) | Input + output guards live, fallbacks in character, kill switch works |
| M4 | Eval harness | Engineer + Designer (cases) | `make eval` runs golden + red-team sets, prints pass rates, fails CI under threshold |
| M5 | Kiosk UI | Designer (spec) → Engineer | Browser UI with idle/listening/thinking/speaking/error states + transcript |
| M5b | Cards | PM (UI) + Engineer (server) | QR + info cards from the approved catalog, check-in loop, no invented URLs |
| MVP | Typed MVP on the lab screen | All | M3–M5b done, `make eval` red-team ≥ 95% and golden ≥ 90% |
| M6a/b/c | Voice | Engineer + Designer | Ada speaks; listens after "Hey Ada"; barge-in (PHASE2-SPEC 6) |
| M7 | Robot hooks | Later | Not now |

## 8. Quality bar

- Reply latency: first token < 1.5 s for typical questions on the GB10 (measure it).
- Replies: short. 1–3 sentences by default; longer only when asked.
- Red-team pass rate ≥ 95%; golden-conversation pass rate ≥ 90% before any demo.
- Python 3.12, `uv`, type hints, `ruff`, `pytest`. Small, reviewable changes.

## 9. How agents work together

- **PM (Atharva)** sets priorities, approves milestones, and resolves conflicts.
- **Designer** owns: persona and voice, system prompt wording, deflection/fallback
  lines, eval case content, lab doc templates, kiosk UI design.
- **Software engineer** owns: all code, infrastructure, RAG pipeline, guards, eval
  runner, UI implementation, performance.
- **Shared files** (`prompts/`, `evals/`): designer writes content, engineer wires it
  up. Neither silently rewrites the other's work; leave a note in `docs/DECISIONS.md`.
- Record every non-trivial decision in `docs/DECISIONS.md` (date, decision, why).
- When blocked or when a decision is costly to reverse, stop and ask the PM.
