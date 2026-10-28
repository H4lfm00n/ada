.PHONY: setup health health-quick bench chat chat-demo run test test-gb10 lint format eval help

UV ?= uv
PY := $(UV) run python

help:
	@echo "setup        install Python 3.12 (uv-managed) + deps into .venv"
	@echo "health       Ollama up, model tags present, smoke-test each model"
	@echo "health-quick Ollama up + model tags only (no model loading)"
	@echo "bench        first-token latency + tokens/sec (thinking off, real system prompt)"
	@echo "chat         interactive terminal chat through the full pipeline"
	@echo "chat-demo    scripted conversation + latency report (evals/chat_demo.yaml)"
	@echo "test         unit tests, Ollama mocked"
	@echo "test-gb10    tests that need the real GB10 + Ollama"
	@echo "lint         ruff check + format check"

setup:
	$(UV) python install 3.12
	$(UV) sync

health:
	$(PY) -m app.health

health-quick:
	$(PY) -m app.health --quick

bench:
	$(PY) -m app.bench

test:
	$(UV) run pytest -m "not gb10" -q

test-gb10:
	RUN_GB10=1 $(UV) run pytest -m gb10 -v

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

chat:
	$(PY) -m app.chat

chat-demo:
	$(PY) -m app.chat --demo

run:
	@echo "make run: arrives with the server (M3/M5)"; exit 2

eval:
	@echo "make eval: arrives in M4 (eval harness)"; exit 2
