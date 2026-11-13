"""Load and validate evals/shared.yaml and the suite files (format: evals/README.md).

Everything is checked at load time (unknown keys, bad regexes, unknown $sets, turn
structure), so a content mistake fails before any model runs. `load_*` raise SpecError
with every problem found; `validate_*` return the problems as a list (used by lint).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.config import REPO_ROOT
from app.evals.match import MatchError, Phrase, compile_items, expand, fill_bot

EVALS_DIR = REPO_ROOT / "evals"
SHARED_PATH = EVALS_DIR / "shared.yaml"

# Suites `make eval` knows. golden_real is the small real-docs set (DECISIONS D44).
SUITE_FILES: dict[str, Path] = {
    "golden": EVALS_DIR / "golden.yaml",
    "redteam": EVALS_DIR / "redteam.yaml",
    "golden_real": EVALS_DIR / "golden_real.yaml",
}
DEFAULT_SUITES = ("golden", "redteam", "golden_real")
REDTEAM_LIKE = frozenset({"redteam"})  # suites where an output-guard catch counts (DES14)

BEHAVIORS = frozenset({"answer", "refuse_in_character", "redirect", "dont_know"})
JUDGED_BEHAVIORS = frozenset({"answer", "refuse_in_character", "redirect"})
SEVERITIES = ("critical", "high", "medium")
CONTROLS = frozenset({"reset", "timeout"})

CASE_KEYS = frozenset({"id", "category", "severity", "tags", "note", "max_question_ratio", "turns"})
TURN_KEYS = frozenset({"user", "assistant", "control", "expect", "note"})
EXPECT_KEYS = frozenset(
    {
        "expected_behavior",
        "must_include",
        "must_include_any",
        "must_not_include",
        "first_sentence_must_include",
        "max_sentences",
        "min_sentences",
        "plain_text",
        "expect_line",
        "allowed_lines",
        "judge",
    }
)
SUITE_KEYS = frozenset(
    {"version", "suite", "shared", "cases", "lab_docs_dir", "threshold", "gating", "note"}
)
LIST_CHECKS = (
    "must_include",
    "must_include_any",
    "must_not_include",
    "first_sentence_must_include",
)
ID_RE = re.compile(r"^[a-z0-9_]+$")


class SpecError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


# -- shared.yaml ---------------------------------------------------------------------


@dataclass(slots=True)
class Shared:
    lab_docs_dir: str
    thresholds: dict[str, float]
    critical_failure_fails_run: bool
    defaults: dict[str, Any]
    always: dict[str, Any]
    phrase_sets: dict[str, list[str]]
    plain_text_patterns: dict[str, re.Pattern[str]]
    judge_rubric: dict[str, str]
    bot_name: str
    always_not: list[Phrase] = field(default_factory=list)

    def phrases(self, items: list[Any] | None) -> list[Phrase]:
        return compile_items(items, self.phrase_sets, self.bot_name)


def load_shared(bot_name: str, path: Path = SHARED_PATH) -> Shared:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    problems: list[str] = []
    sets = {str(k): [str(x) for x in (v or [])] for k, v in (data.get("phrase_sets") or {}).items()}
    patterns: dict[str, re.Pattern[str]] = {}
    for name, rx in (data.get("plain_text_patterns") or {}).items():
        try:
            patterns[str(name)] = re.compile(str(rx), re.MULTILINE)
        except re.error as exc:
            problems.append(f"shared.yaml plain_text_patterns.{name}: {exc}")
    for name, items in sets.items():
        try:
            compile_items(items, sets, bot_name)
        except MatchError as exc:
            problems.append(f"shared.yaml phrase_sets.{name}: {exc}")
    shared = Shared(
        lab_docs_dir=str(data.get("lab_docs_dir") or "evals/fixtures/lab_docs"),
        thresholds={str(k): float(v) for k, v in (data.get("thresholds") or {}).items()},
        critical_failure_fails_run=bool(data.get("critical_failure_fails_run", False)),
        defaults=dict(data.get("defaults") or {}),
        always=dict(data.get("always") or {}),
        phrase_sets=sets,
        plain_text_patterns=patterns,
        judge_rubric={str(k): str(v) for k, v in (data.get("judge_rubric") or {}).items()},
        bot_name=bot_name,
    )
    try:
        shared.always_not = shared.phrases(shared.always.get("must_not_include"))
    except MatchError as exc:
        problems.append(f"shared.yaml always.must_not_include: {exc}")
    if problems:
        raise SpecError(problems)
    return shared


# -- suites --------------------------------------------------------------------------


@dataclass(slots=True)
class Turn:
    kind: str  # "user" | "assistant" | "control"
    text: str  # message, seeded reply, or control name
    n: int  # 1-based position in the case's turn list (reports use it)
    expect: dict[str, Any] | None = None
    seeded: bool = False  # a user turn whose reply is the next `assistant:` turn


@dataclass(slots=True)
class Case:
    id: str
    suite: str
    category: str
    severity: str | None
    turns: list[Turn]
    tags: list[str] = field(default_factory=list)
    note: str = ""
    max_question_ratio: float | None = None


@dataclass(slots=True)
class Suite:
    name: str
    path: Path
    cases: list[Case]
    lab_docs_dir: str
    threshold: float
    gating: bool


def _case_problems(raw: dict[str, Any], where: str, suite: str, shared: Shared) -> list[str]:
    p: list[str] = []
    unknown = set(raw) - CASE_KEYS
    if unknown:
        p.append(f"{where}: unknown keys {sorted(unknown)}")
    cid = raw.get("id")
    if not isinstance(cid, str) or not ID_RE.match(cid):
        p.append(f"{where}: id must be snake_case")
    if not raw.get("category"):
        p.append(f"{where}: missing category")
    sev = raw.get("severity")
    if suite in REDTEAM_LIKE and sev not in SEVERITIES:
        p.append(f"{where}: red-team cases need severity {list(SEVERITIES)}")
    if sev is not None and sev not in SEVERITIES:
        p.append(f"{where}: bad severity {sev!r}")
    turns = raw.get("turns")
    if not isinstance(turns, list) or not turns:
        p.append(f"{where}: needs a non-empty turns list")
        return p
    prev_kind = None
    for i, t in enumerate(turns, 1):
        tw = f"{where} turn {i}"
        if not isinstance(t, dict):
            p.append(f"{tw}: not a mapping")
            continue
        if set(t) - TURN_KEYS:
            p.append(f"{tw}: unknown keys {sorted(set(t) - TURN_KEYS)}")
        kinds = [k for k in ("user", "assistant", "control") if k in t]
        if len(kinds) != 1:
            p.append(f"{tw}: needs exactly one of user / assistant / control")
            continue
        kind = kinds[0]
        if kind == "control" and t["control"] not in CONTROLS:
            p.append(f"{tw}: control must be one of {sorted(CONTROLS)}")
        if kind == "assistant":
            if prev_kind != "user":
                p.append(f"{tw}: a seeded assistant turn must follow a user turn")
            elif "expect" in turns[i - 2]:
                p.append(
                    f"{where} turn {i - 1}: seeded (next turn is assistant:), can't have expect"
                )
            if "expect" in t:
                p.append(f"{tw}: seeded assistant turns can't have expect")
        if "expect" in t:
            p += _expect_problems(t["expect"], tw, shared)
        prev_kind = kind
    if raw.get("max_question_ratio") is not None:
        r = raw["max_question_ratio"]
        if not isinstance(r, int | float) or not 0 <= r <= 1:
            p.append(f"{where}: max_question_ratio must be 0..1")
    return p


def _expect_problems(e: Any, where: str, shared: Shared) -> list[str]:
    if not isinstance(e, dict):
        return [f"{where}: expect must be a mapping"]
    p: list[str] = []
    if set(e) - EXPECT_KEYS:
        p.append(f"{where}: unknown expect keys {sorted(set(e) - EXPECT_KEYS)}")
    b = e.get("expected_behavior")
    if b is not None and b not in BEHAVIORS:
        p.append(f"{where}: expected_behavior {b!r} not in {sorted(BEHAVIORS)}")
    for key in LIST_CHECKS:
        if key in e:
            if not isinstance(e[key], list):
                p.append(f"{where}: {key} must be a list")
                continue
            try:
                shared.phrases(e[key])
            except MatchError as exc:
                p.append(f"{where}: {key}: {exc}")
    for key in ("max_sentences", "min_sentences"):
        if key in e and (not isinstance(e[key], int) or e[key] < 1):
            p.append(f"{where}: {key} must be a positive int")
    if "allowed_lines" in e and not isinstance(e["allowed_lines"], list):
        p.append(f"{where}: allowed_lines must be a list")
    return p


def parse_suite(name: str, path: Path, shared: Shared) -> tuple[Suite | None, list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    problems: list[str] = []
    if set(data) - SUITE_KEYS:
        problems.append(f"{path.name}: unknown top-level keys {sorted(set(data) - SUITE_KEYS)}")
    raw_cases = data.get("cases") or []
    cases: list[Case] = []
    for i, raw in enumerate(raw_cases, 1):
        where = f"{path.name} case {i} ({raw.get('id', '?') if isinstance(raw, dict) else '?'})"
        if not isinstance(raw, dict):
            problems.append(f"{where}: not a mapping")
            continue
        cp = _case_problems(raw, where, name, shared)
        problems += cp
        if cp:
            continue
        cases.append(_build_case(raw, name, shared.bot_name))
    threshold = data.get("threshold", shared.thresholds.get(name))
    if threshold is None:
        problems.append(f"{path.name}: no threshold (shared.yaml thresholds.{name} or threshold:)")
        threshold = 1.0
    suite = Suite(
        name=name,
        path=path,
        cases=cases,
        lab_docs_dir=str(data.get("lab_docs_dir") or shared.lab_docs_dir),
        threshold=float(threshold),
        gating=bool(data.get("gating", True)),
    )
    return suite, problems


def _build_case(raw: dict[str, Any], suite: str, bot_name: str) -> Case:
    turns: list[Turn] = []
    raw_turns = raw["turns"]
    for i, t in enumerate(raw_turns, 1):
        kind = next(k for k in ("user", "assistant", "control") if k in t)
        text = fill_bot(str(t[kind]), bot_name)
        seeded = kind == "user" and i < len(raw_turns) and "assistant" in raw_turns[i]
        turns.append(Turn(kind, text, i, t.get("expect"), seeded))
    return Case(
        id=raw["id"],
        suite=suite,
        category=str(raw["category"]),
        severity=raw.get("severity"),
        turns=turns,
        tags=list(raw.get("tags") or []),
        note=str(raw.get("note") or ""),
        max_question_ratio=raw.get("max_question_ratio"),
    )


def duplicate_ids(suites: list[Suite]) -> list[str]:
    seen: dict[str, str] = {}
    out = []
    for s in suites:
        for c in s.cases:
            if c.id in seen:
                out.append(f"duplicate id {c.id!r} in {seen[c.id]} and {s.name}")
            seen[c.id] = s.name
    return out


def load_suites(names: list[str] | tuple[str, ...], shared: Shared) -> list[Suite]:
    suites: list[Suite] = []
    problems: list[str] = []
    for name in names:
        path = SUITE_FILES.get(name)
        if path is None:
            problems.append(f"unknown suite {name!r} (known: {', '.join(SUITE_FILES)})")
            continue
        suite, p = parse_suite(name, path, shared)
        problems += p
        if suite:
            suites.append(suite)
    problems += duplicate_ids(suites)
    if problems:
        raise SpecError(problems)
    return suites


__all__ = [
    "BEHAVIORS",
    "DEFAULT_SUITES",
    "JUDGED_BEHAVIORS",
    "REDTEAM_LIKE",
    "SUITE_FILES",
    "Case",
    "Shared",
    "SpecError",
    "Suite",
    "Turn",
    "expand",
    "load_shared",
    "load_suites",
    "parse_suite",
]
