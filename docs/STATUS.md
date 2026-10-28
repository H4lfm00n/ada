# Project status

_Every task starts by reading this file and ends by updating it._
_Last updated: 2026-10-28 by ENG (M0 verified on GB10)._

## Machine (from PM diagnostics, 2026-10-28)
- GB10, aarch64, Ubuntu 24.04.4, driver 580.173.02 / CUDA 13.0, 121 GiB RAM visible.
- Ollama 0.32.14. Installed: `qwen3.8:27b` (chat, 27.3B Q4_K_M), `gemma4:12b`,
  `llama-guard3:8b` (guard), `qwen3-embedding:0.6b` (embed, dim 1024).
- System Python 3.14.6 (Anaconda); project uses uv-managed CPython 3.12.15 (aarch64).
- Repo lives at `~/ada` on the GB10, cloned via a read-only deploy key (ssh host alias
  `github-ada`). The GB10 only pulls; Claude pushes branches.

## Done
- **M0 env + skeleton** (branch `task/m0-skeleton`). Verified on GB10 2026-10-28:
  `make health` OK (all 3 models present + smoke-tested), `make test-gb10` 2/2 pass,
  `make bench` meets every target (table below). Sandbox: ruff clean, 29 tests pass.
  - uv project, ruff, pytest (`gb10` marker skipped unless `RUN_GB10=1`).
  - `config.yaml` + pydantic-settings (`ADA_*` env overrides, loopback-only Ollama URL).
  - Stable interfaces in `app/interfaces.py`: ChatModel, Guard, Retriever, OutputSink,
    ActionHandler, SessionManager (only ChatModel implemented).
  - `app/ollama_client.py`: async streaming client + `OllamaChatModel` (think=false,
    thinking text never yielded).
  - `make health` and `make bench` (JSON reports stay local in `reports/`).

## In progress
- Nothing. M0 is waiting for the PM to merge `task/m0-skeleton` into `main`.

## Next
- M1 (text chat): SessionManager, prompt builder over `prompts/system_prompt.md`, `make chat`,
  OutputSink sentence events, ActionHandler stub. Needs the designer's persona branch merged.
- M1 must re-bench with the real system prompt (see "Latency notes").

## Measured on GB10
| date | chat model | TTFT median (max) | decode tok/s median (min) | guard | embed | est. first word | notes |
|------|-----------|-------------------|---------------------------|-------|-------|-----------------|-------|
| 2026-10-28 | qwen3.8:27b | 0.57 s (0.65) | 19.6 (16.9) | 0.25 s | 0.11 s | 0.93 s | think off verified; cold load 8.2 s |
| 2026-10-28 | gemma4:12b *(comparison only)* | 0.51 s (0.54) | 42.7 (36.1) | 0.23 s | 0.11 s | 0.84 s | not in use |

Targets: first token ≤ 1.5 s, decode ≥ 10 tok/s. The bench prompt is short (~70 tokens).

## Latency notes (for M1–M3)
- Prefill was measured at ~255 tok/s on a short prompt, where fixed overhead dominates.
  The real system prompt + retrieved docs could be 1–3k tokens, so TTFT must be re-measured
  in M1/M2. Ollama reuses the KV cache for an unchanged prompt prefix, so keep the system
  prompt stable and put retrieved context after it.
- `keep_alive: 30m` avoids the ~8 s cold load between visitors. A reply after a long idle
  pays the load cost again; consider a periodic keep-warm ping in the server milestone.
- Output guard costs ~0.25 s per check. In M3, guard per sentence and overlap it with
  decoding of the next sentence, rather than guarding the whole reply at the end.

## Open questions
- PM: confirm `qwen3.8:27b` as the chat model. It meets the targets with headroom;
  `gemma4:12b` is ~2x faster on decode and could be a fallback later (would need evals).
- PM: confirm `session_timeout_s: 120` and `target_decode_tok_s: 10` in config.yaml.
- The chat model reports `tools` capability. It isn't exercised yet; phase 1 doesn't need it.

## Designer section
_(designer updates this)_
