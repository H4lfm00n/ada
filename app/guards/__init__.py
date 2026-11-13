"""Guardrails (M3): input guard before the chat model, output guard before the visitor.

    input:  InputRules  -> LlamaGuard(user turn)       = GuardChain("input")
    output: OutputRules -> LlamaGuard(assistant turn)  = GuardChain("output")

`build_guards()` is the only constructor the app uses, so every entry point gets the same
real guards (there is deliberately no "allow everything" guard in app code).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.guards.chain import GuardChain, GuardDecision
from app.guards.contacts import ContactAllowlist
from app.guards.llama_guard import LlamaGuard
from app.guards.policy import GuardPolicy
from app.guards.rules import InputRules, OutputRules
from app.lines import LineBook
from app.ollama_client import OllamaClient
from app.rag.ingest import parse_corpus

__all__ = ["GuardChain", "GuardDecision", "Guards", "allowlist_path", "build_guards"]

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Guards:
    input: GuardChain
    output: GuardChain
    policy: GuardPolicy
    allowlist: ContactAllowlist
    warnings: list[str] = field(default_factory=list)


def allowlist_path(settings: Settings) -> Path:
    """data/rag/<docs slug>.contacts.json, next to the index it was built with."""
    return settings.rag_index_path.with_suffix(".contacts.json")


def build_doc_allowlist(settings: Settings) -> ContactAllowlist:
    """Contacts in the lab docs, from the same parse as ingest (TODO sections left out)."""
    corpus = parse_corpus(settings.lab_docs_path)
    return ContactAllowlist.from_texts([c.text for c in corpus.chunks])


def load_allowlist(settings: Settings, lines: LineBook) -> tuple[ContactAllowlist, list[str]]:
    warnings: list[str] = []
    path = allowlist_path(settings)
    try:
        docs = ContactAllowlist.load(path)
    except FileNotFoundError:
        warnings.append(f"no contact allowlist at {path.name}; built from docs (run `make ingest`)")
        docs = build_doc_allowlist(settings)
    except (ValueError, OSError) as exc:
        warnings.append(f"contact allowlist unreadable ({exc}); built from docs")
        docs = build_doc_allowlist(settings)
    # The designer's canned lines (sfsu.edu, ...) are vetted content too.
    canned = ContactAllowlist.from_texts(lines.all_lines())
    return docs.merged(canned), warnings


def build_guards(settings: Settings, client: OllamaClient, lines: LineBook) -> Guards:
    policy = GuardPolicy.load(
        settings.content_path(settings.guard_policy_file),
        settings.content_path(settings.guard_blocklist_local_file),
    )
    policy.check_lines(set(lines.categories))
    allowlist, warnings = load_allowlist(settings, lines)
    warnings += [f"guard policy maps to missing line {c!r} (TODO designer); uses guard_fallback"
                 for c in sorted(policy.todo_categories)]  # fmt: skip
    model = LlamaGuard(client, settings)
    t = settings.guard_timeout_s
    return Guards(
        input=GuardChain("input", InputRules(policy, allowlist), model, policy, t),
        output=GuardChain("output", OutputRules(policy, allowlist), model, policy, t),
        policy=policy,
        allowlist=allowlist,
        warnings=warnings,
    )
