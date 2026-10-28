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
_(designer updates this)_
