"""Retrieval check: does the right lab doc come back for each test question?

Cases live in evals/retrieval.yaml, grouped by the docs folder they were written for.
Model-free scoring; only the question embeddings need Ollama.

Per case we look at two rankings:
  * the chunks that pass rag_min_score (what the model actually sees), and
  * the full ranking, ignoring the cutoff.
That split tells the two failure modes apart: an expected file that ranks in the top k but
falls under the cutoff is a threshold problem; one that ranks low is an embedding problem
(the case for trying a bigger embed model, DECISIONS D23).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.interfaces import Chunk


class CheckFileError(ValueError):
    """evals/retrieval.yaml is malformed."""


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    question: str
    expect: tuple[str, ...]  # any of these files counts as a hit; empty -> expect_none
    expect_none: bool = False  # nothing should pass the cutoff ("not sure")


@dataclass(slots=True)
class Suite:
    lab_docs_dir: str
    cases: list[RetrievalCase]
    note: str = ""


@dataclass(slots=True)
class CheckFile:
    pass_bar: float
    suites: list[Suite]

    def suite_for(self, lab_docs_dir: str) -> Suite | None:
        want = Path(lab_docs_dir).as_posix().rstrip("/")
        return next((s for s in self.suites if s.lab_docs_dir.rstrip("/") == want), None)


def load_check_file(path: Path) -> CheckFile:
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    suites = []
    for i, raw in enumerate(data.get("suites") or []):
        if not isinstance(raw, dict) or "lab_docs_dir" not in raw:
            raise CheckFileError(f"{path.name}: suite {i} needs lab_docs_dir")
        cases = []
        for c in raw.get("cases") or []:
            q = c.get("q")
            expect = c.get("expect") or []
            expect = (expect,) if isinstance(expect, str) else tuple(expect)
            none = bool(c.get("expect_none"))
            if not q or bool(expect) == none:
                raise CheckFileError(
                    f"{path.name}: case {c!r} needs `q` and exactly one of expect / expect_none"
                )
            cases.append(RetrievalCase(q, expect, none))
        suites.append(Suite(str(raw["lab_docs_dir"]), cases, str(raw.get("note", ""))))
    if not suites:
        raise CheckFileError(f"{path.name}: no suites")
    return CheckFile(float(data.get("pass_bar", 0.9)), suites)


@dataclass(slots=True)
class CaseResult:
    case: RetrievalCase
    passed_files: list[str]  # files of chunks that passed the cutoff, best first
    ranked: list[tuple[str, float]]  # (file, score) for every chunk, best first
    k: int

    @property
    def expected_rank(self) -> int | None:
        """1-based rank of the first chunk from an expected file, ignoring the cutoff."""
        for i, (f, _) in enumerate(self.ranked, 1):
            if f in self.case.expect:
                return i
        return None

    @property
    def hit_at_1(self) -> bool:
        return bool(self.passed_files) and self.passed_files[0] in self.case.expect

    @property
    def hit(self) -> bool:
        """Recall@k as the model sees it: an expected file passed the cutoff."""
        if self.case.expect_none:
            return not self.passed_files
        return any(f in self.case.expect for f in self.passed_files)

    @property
    def diagnosis(self) -> str:
        if self.hit:
            return "ok"
        if self.case.expect_none:
            return "cutoff too low (something passed)"
        rank = self.expected_rank
        if rank is not None and rank <= self.k:
            return f"cutoff too high (expected file ranked {rank})"
        return f"embedding miss (expected file ranked {rank or 'nowhere'})"


@dataclass(slots=True)
class CheckSummary:
    results: list[CaseResult] = field(default_factory=list)
    pass_bar: float = 0.9

    def _answerable(self) -> list[CaseResult]:
        return [r for r in self.results if not r.case.expect_none]

    @property
    def recall_at_k(self) -> float:
        rs = self._answerable()
        return sum(r.hit for r in rs) / len(rs) if rs else 1.0

    @property
    def hit_at_1(self) -> float:
        rs = self._answerable()
        return sum(r.hit_at_1 for r in rs) / len(rs) if rs else 1.0

    @property
    def recall_ignoring_cutoff(self) -> float:
        rs = self._answerable()
        if not rs:
            return 1.0
        return sum(1 for r in rs if (r.expected_rank or 10**9) <= r.k) / len(rs)

    @property
    def mrr(self) -> float:
        rs = self._answerable()
        return sum(1 / r.expected_rank for r in rs if r.expected_rank) / len(rs) if rs else 1.0

    @property
    def none_ok(self) -> tuple[int, int]:
        rs = [r for r in self.results if r.case.expect_none]
        return sum(r.hit for r in rs), len(rs)

    @property
    def passed(self) -> bool:
        ok, total = self.none_ok
        return self.recall_at_k >= self.pass_bar and ok == total


def score_case(
    case: RetrievalCase, passed: list[Chunk], ranked: list[tuple[str, float]], k: int
) -> CaseResult:
    return CaseResult(case, [c.source_file for c in passed], ranked, k)
