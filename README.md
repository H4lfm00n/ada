# AI Lab Assistant (ada)

A local, in-character AI assistant for the SFSU AI Lab. It runs entirely on the lab's
NVIDIA GB10 through Ollama. No cloud APIs, no telemetry.

Project brief: [CLAUDE.md](CLAUDE.md) · status: [docs/STATUS.md](docs/STATUS.md) ·
decisions: [docs/DECISIONS.md](docs/DECISIONS.md)

## Quick start (on the GB10)

```bash
make setup     # uv-managed Python 3.12 + deps
make health    # Ollama up? models present? each model answers?
make bench     # first-token latency + tokens/sec (thinking off)
make test      # unit tests (Ollama mocked)
make test-gb10 # live tests against local Ollama
```

Models and settings live in `config.yaml`. Override any key with `ADA_<KEY>`:

```bash
ADA_CHAT_MODEL=gemma4:12b make bench
```

## Layout

```
app/       Python package (config, interfaces, Ollama client, health, bench)
prompts/   system prompt templates (designer)
content/   persona bible, lines (designer)
lab_docs/  lab knowledge base (Markdown)
evals/     eval sets + bench prompts
ui/        kiosk web UI
docs/      STATUS, DECISIONS, specs
tests/     pytest
```
