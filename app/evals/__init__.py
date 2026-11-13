"""M4 eval harness: golden + red-team suites through the full guarded pipeline.

    python -m app.evals run   [--suite golden,redteam,golden_real] [--repeat N] ...
    python -m app.evals lint  (no model needed)

Modules: `spec` (load + validate the YAML), `match` (phrase matching rules from
evals/README.md), `scorer` (rule checks, pure), `judge` (local Ollama LLM judge),
`runner` (drives the pipeline), `report` (files under evals/reports/<timestamp>/),
`lint` (content checks).
"""
