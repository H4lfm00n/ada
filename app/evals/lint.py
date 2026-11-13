"""`make eval-lint`: content checks that need no model (evals/README.md, proposed list).

Errors (exit 1):
- shared.yaml and every suite load; ids unique; no unknown keys; red-team severities valid;
  every $set resolves; every regex compiles; seeded turns are well formed.
- every `allowed_lines` / `expect_line` category exists in content/lines.yaml, and every
  line in it passes that turn's checks plus `always` (so a correct canned line can't fail).
- every line in lines.yaml passes `always` and plain_text.
- cases tagged `grounding`: each must_include phrase (and one must_include_any phrase)
  appears in the docs that suite retrieves from.
Warnings:
- a `prompt_canaries` phrase that no longer appears in the system prompt (stale list).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.evals.match import fill_bot, norm
from app.evals.scorer import Observation, always_failures, mechanical, plain_text_failures
from app.evals.spec import (
    DEFAULT_SUITES,
    SUITE_FILES,
    Shared,
    SpecError,
    Suite,
    duplicate_ids,
    load_shared,
    parse_suite,
)
from app.lines import LineBook
from app.sentences import split_sentences


@dataclass(slots=True)
class LintResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suites: list[Suite] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def line_obs(line: str, category: str) -> Observation:
    return Observation(text=line, sentences=split_sentences(line), source=f"line:{category}")


def _corpus_text(root: Path) -> str:
    return norm("\n".join(p.read_text(encoding="utf-8") for p in sorted(root.glob("*.md"))))


def lint(settings: Settings, suite_names: tuple[str, ...] = DEFAULT_SUITES) -> LintResult:
    res = LintResult()
    try:
        shared = load_shared(settings.bot_name)
    except SpecError as exc:
        res.errors += exc.problems
        return res
    lines = LineBook.from_settings(settings)
    for name in suite_names:
        path = SUITE_FILES[name]
        if not path.exists():
            res.errors.append(f"{path.name}: missing")
            continue
        suite, problems = parse_suite(name, path, shared)
        res.errors += problems
        if suite:
            res.suites.append(suite)
    res.errors += duplicate_ids(res.suites)

    for cat in lines.categories:
        for raw in lines.category(cat).lines:
            line = fill_bot(raw, settings.bot_name)
            obs = line_obs(line, cat)
            for f in always_failures(obs, shared) + [
                f"plain_text {b}" for b in plain_text_failures(obs.sentences, shared)
            ]:
                res.errors.append(f"lines.yaml {cat}: {f}: {line[:80]!r}")

    for suite in res.suites:
        docs_root = settings.content_path(suite.lab_docs_dir)
        corpus = _corpus_text(docs_root) if docs_root.is_dir() else None
        if corpus is None:
            res.errors.append(f"{suite.name}: lab_docs_dir {suite.lab_docs_dir} not found")
        for case in suite.cases:
            for turn in case.turns:
                e = turn.expect
                if not e:
                    continue
                where = f"{suite.name} {case.id} t{turn.n}"
                cats = list(e.get("allowed_lines") or [])
                if e.get("expect_line"):
                    cats.append(e["expect_line"])
                for cat in cats:
                    if not lines.has(cat):
                        res.errors.append(f"{where}: line category {cat!r} not in lines.yaml")
                        continue
                    for raw in lines.category(cat).lines:
                        line = fill_bot(raw, settings.bot_name)
                        fails = mechanical(e, line_obs(line, cat), shared)
                        for f in fails:
                            res.errors.append(
                                f"{where}: allowed line {cat} fails: {f}: {line[:70]!r}"
                            )
                if "grounding" in case.tags and corpus is not None:
                    _check_grounding(where, e, corpus, shared, res)

    _check_canaries(settings, shared, res)
    return res


def _check_grounding(where: str, e: dict, corpus: str, shared: Shared, res: LintResult) -> None:
    for p in shared.phrases(e.get("must_include")):
        if not p.rx.search(corpus):
            res.errors.append(f"{where}: grounding phrase {p.label!r} not in the suite's docs")
    anyp = shared.phrases(e.get("must_include_any"))
    if anyp and not any(p.rx.search(corpus) for p in anyp):
        res.errors.append(f"{where}: no must_include_any phrase appears in the suite's docs")


def _check_canaries(settings: Settings, shared: Shared, res: LintResult) -> None:
    prompt = settings.content_path(settings.system_prompt_file)
    if not prompt.exists():
        return
    text = norm(prompt.read_text(encoding="utf-8"))
    for p in shared.phrases(shared.phrase_sets.get("prompt_canaries", [])):
        if not p.rx.search(text):
            res.warnings.append(
                f"prompt_canaries: {p.label!r} is not in {settings.system_prompt_file}"
            )
