"""Pass rates, the gate, and the files under evals/reports/<timestamp>/.

    SUMMARY.md     short, readable on the GB10 (paste this back)
    failures.md    every failure: input, reply, which guard fired, why, latency
    summary.json   all numbers, machine-readable
    results.jsonl  one line per case attempt, every turn
    cases.csv      one row per case attempt: status, method, latency

Pass rate = passed attempts / attempts (with --repeat N each case counts N times).
A case is flaky when it passed some attempts but not all.
"""

from __future__ import annotations

import csv
import json
import statistics
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT, Settings
from app.evals.runner import CaseRun, RunOutput
from app.evals.spec import REDTEAM_LIKE, Shared, Suite

REPORTS_DIR = REPO_ROOT / "evals" / "reports"
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, None: 3}
MAX_IN_SUMMARY = 20


@dataclass(slots=True)
class SuiteStats:
    name: str
    threshold: float
    gating: bool
    cases: int
    attempts: int
    passed: int
    unjudged: int
    rule_scored: int  # attempts decided by rules alone
    judge_scored: int  # attempts that needed the judge
    by_category: dict[str, tuple[int, int]]  # category -> (passed, attempts)
    sources: dict[str, int]  # turn sources: model, line (input block), output_caught, infra
    flaky: list[tuple[str, int, int]]  # (id, passes, attempts)
    critical_failed: list[str]
    latency: dict[str, float | None]
    gate_reasons: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.passed / self.attempts if self.attempts else 0.0

    @property
    def gate_ok(self) -> bool:
        return not self.gate_reasons


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))], 3)


def suite_stats(suite: Suite, runs: list[CaseRun], shared: Shared) -> SuiteStats:
    rs = [r for r in runs if r.case.suite == suite.name]
    cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    per_case: dict[str, list[bool]] = defaultdict(list)
    sources: dict[str, int] = defaultdict(int)
    fs: list[float] = []
    ttft: list[float] = []
    for r in rs:
        cat[r.case.category][1] += 1
        cat[r.case.category][0] += r.passed
        per_case[r.case.id].append(r.passed)
        for t in r.turns:
            if t.infra:
                sources["infra"] += 1
            elif t.verdict.caught_by_output_guard or "output:BLOCK" in t.guard:
                sources["output_caught"] += 1
            elif t.source == "model":
                sources["model"] += 1
                if t.latency.get("first_sentence_s") is not None:
                    fs.append(float(t.latency["first_sentence_s"]))  # type: ignore[arg-type]
                if t.latency.get("ttft_s") is not None:
                    ttft.append(float(t.latency["ttft_s"]))  # type: ignore[arg-type]
            elif "input:BLOCK" in t.guard:
                sources["input_blocked"] += 1
            else:
                sources["line"] += 1  # goodbye etc.
    flaky = [(cid, sum(v), len(v)) for cid, v in per_case.items() if 0 < sum(v) < len(v)]
    critical = sorted({r.case.id for r in rs if r.case.severity == "critical" and not r.passed})
    st = SuiteStats(
        name=suite.name,
        threshold=suite.threshold,
        gating=suite.gating,
        cases=len(per_case),
        attempts=len(rs),
        passed=sum(r.passed for r in rs),
        unjudged=sum(r.status == "unjudged" for r in rs),
        rule_scored=sum(r.method == "rules" for r in rs),
        judge_scored=sum(r.method == "judge" for r in rs),
        by_category={k: (v[0], v[1]) for k, v in sorted(cat.items())},
        sources=dict(sources),
        flaky=sorted(flaky),
        critical_failed=critical,
        latency={
            "first_sentence_median_s": round(statistics.median(fs), 3) if fs else None,
            "first_sentence_p90_s": _pct(fs, 0.9),
            "first_sentence_max_s": round(max(fs), 3) if fs else None,
            "ttft_median_s": round(statistics.median(ttft), 3) if ttft else None,
            "ttft_p90_s": _pct(ttft, 0.9),
        },
    )
    if suite.gating:
        if st.rate < suite.threshold:
            st.gate_reasons.append(f"pass rate {st.rate:.1%} < {suite.threshold:.0%}")
        if suite.name in REDTEAM_LIKE and shared.critical_failure_fails_run and critical:
            st.gate_reasons.append(f"critical failures: {', '.join(critical)}")
        if not rs:
            st.gate_reasons.append("no cases ran")
    return st


def git_info() -> dict[str, str]:
    def g(*args: str) -> str:
        try:
            out = subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    return {
        "commit": g("rev-parse", "--short", "HEAD"),
        "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": "yes" if g("status", "--porcelain") else "no",
    }


def sorted_failures(runs: list[CaseRun]) -> list[CaseRun]:
    bad = [r for r in runs if r.status == "fail"]
    return sorted(
        bad,
        key=lambda r: (SEVERITY_ORDER.get(r.case.severity, 3), r.case.suite, r.case.id, r.attempt),
    )


def _clip(s: str | None, n: int) -> str:
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 3] + "..."


def failure_block(r: CaseRun, full: bool) -> list[str]:
    c = r.case
    sev = f"/{c.severity}" if c.severity else ""
    rep = "" if r.attempt == 1 and not full else f" (attempt {r.attempt})"
    out = [f"### {c.suite} · {c.id} [{c.category}{sev}]{rep}"]
    for f in r.case_failures:
        out.append(f"- case: {f}")
    n = 400 if full else 220
    turns = [t for t in r.turns if t.failures] or r.turns[-1:]
    shown = r.turns if full else turns[:2]
    for t in shown:
        mark = "FAIL" if t.failures else "ok"
        out.append(f"- t{t.n} {mark} · visitor: {_clip(t.user, n)}")
        out.append(f"  - reply ({t.source}): {_clip(t.reply, n)}")
        out.append(f"  - guard: {t.guard}")
        if t.blocked_sentence:
            out.append(f"  - blocked, not shown: {_clip(t.blocked_sentence, n)}")
        for f in t.failures:
            out.append(f"  - why: {f}")
        if full and t.retrieved:
            out.append(f"  - docs: {'; '.join(t.retrieved)}")
        lat = t.latency
        out.append(
            f"  - latency: first sentence {lat.get('first_sentence_s')}s, "
            f"first token {lat.get('ttft_s')}s, total {lat.get('total_s')}s"
        )
    return out


def build_summary(
    out: RunOutput, stats: list[SuiteStats], settings: Settings, shared: Shared, started: str
) -> dict[str, Any]:
    gate_ok = all(s.gate_ok for s in stats)
    return {
        "started": started,
        "git": git_info(),
        "models": {
            **settings.model_tags,
            "judge": settings.judge_model if out.judge_used else None,
        },
        "settings": {
            "temperature": settings.temperature,
            "guard_parallel_retrieval": settings.guard_parallel_retrieval,
            "rag_top_k": settings.rag_top_k,
            "rag_min_score": settings.rag_min_score,
            "bot_name": settings.bot_name,
        },
        "repeat": out.repeat,
        "judge_used": out.judge_used,
        "judge_calls": out.judge_calls,
        "seconds": {"generate": round(out.gen_s, 1), "judge": round(out.judge_s, 1)},
        "warm_up_s": out.warm,
        "warnings": out.warnings,
        "gate": {
            "ok": gate_ok,
            "provisional": not out.judge_used and any(s.unjudged for s in stats),
            "critical_failure_fails_run": shared.critical_failure_fails_run,
            "reasons": {s.name: s.gate_reasons for s in stats if s.gate_reasons},
        },
        "suites": {
            s.name: {
                "pass_rate": round(s.rate, 4),
                "passed": s.passed,
                "attempts": s.attempts,
                "cases": s.cases,
                "threshold": s.threshold,
                "gating": s.gating,
                "unjudged": s.unjudged,
                "rule_scored": s.rule_scored,
                "judge_scored": s.judge_scored,
                "by_category": {
                    k: {"passed": p, "attempts": a} for k, (p, a) in s.by_category.items()
                },
                "turn_sources": s.sources,
                "flaky": [{"id": i, "passed": p, "attempts": a} for i, p, a in s.flaky],
                "critical_failed": s.critical_failed,
                "latency": s.latency,
            }
            for s in stats
        },
        "rule_scored_ids": sorted({r.case.id for r in out.runs if r.method == "rules"}),
        "judge_scored_ids": sorted({r.case.id for r in out.runs if r.method == "judge"}),
    }


def summary_md(summary: dict[str, Any], stats: list[SuiteStats], out: RunOutput) -> str:
    g = summary["gate"]
    verdict = "PASS" if g["ok"] else "FAIL"
    if g["provisional"]:
        verdict += " (PROVISIONAL: --no-judge, judge cases counted as pass)"
    m = summary["models"]
    L = [
        f"# make eval — {verdict}",
        "",
        f"{summary['started']} · git {summary['git']['commit']} ({summary['git']['branch']}"
        f"{', dirty' if summary['git']['dirty'] == 'yes' else ''}) · repeat {out.repeat}",
        f"chat {m['chat']} · guard {m['guard']} · embed {m['embed']} · judge {m['judge'] or 'off'}"
        f" · generate {summary['seconds']['generate']}s · judge {summary['seconds']['judge']}s"
        f" ({summary['judge_calls']} calls)",
        "",
        "| suite | pass rate | passed/attempts | bar | gate | rules/judge-scored "
        "| turns: model / input-block / output-caught / infra | first sentence median, p90 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in stats:
        gate = ("PASS" if s.gate_ok else "FAIL") if s.gating else "report only"
        src = s.sources
        lat = s.latency
        L.append(
            f"| {s.name} | **{s.rate:.1%}** | {s.passed}/{s.attempts} | {s.threshold:.0%} "
            f"| {gate} | "
            f"{s.rule_scored}/{s.judge_scored} | {src.get('model', 0)} / "
            f"{src.get('input_blocked', 0)} / {src.get('output_caught', 0)} / "
            f"{src.get('infra', 0)} | "
            f"{lat['first_sentence_median_s']}s, {lat['first_sentence_p90_s']}s |"
        )
    for name, reasons in g["reasons"].items():
        L.append(f"- gate {name}: {'; '.join(reasons)}")
    infra = sum(s.sources.get("infra", 0) for s in stats)
    if infra:
        L.append(
            f"- **WARNING: {infra} turns hit a guard/model error or timeout.** Check `ollama ps`; "
            "the run may say more about the machine than the bot."
        )
    for w in summary["warnings"]:
        L.append(f"- warn: {w}")
    L += ["", "## Categories below 100%", ""]
    any_cat = False
    for s in stats:
        weak = [f"{k} {p}/{a}" for k, (p, a) in s.by_category.items() if p < a]
        if weak:
            any_cat = True
            L.append(f"- {s.name}: " + ", ".join(weak))
    if not any_cat:
        L.append("- none")
    flaky = [(s.name, f) for s in stats for f in s.flaky]
    if out.repeat > 1:
        L += ["", "## Flaky (passed some attempts, not all)", ""]
        L += [f"- {n} · {i}: {p}/{a}" for n, (i, p, a) in flaky] or ["- none"]
    crit = [(s.name, c) for s in stats for c in s.critical_failed]
    if crit:
        L += ["", "## Critical failures", ""] + [f"- {n} · {c}" for n, c in crit]
    fails = sorted_failures(out.runs)
    seen: set[tuple[str, str]] = set()
    worst = []
    for r in fails:  # one attempt per case in the summary; all of them in failures.md
        key = (r.case.suite, r.case.id)
        if key not in seen:
            seen.add(key)
            worst.append(r)
    L += [
        "",
        f"## Failures ({len(worst)} cases; worst first, top {MAX_IN_SUMMARY}; all in failures.md)",
        "",
    ]
    for r in worst[:MAX_IN_SUMMARY]:
        L += [*failure_block(r, full=False), ""]
    if not worst:
        L.append("- none")
    L += [
        "",
        f"Judge-scored cases: {len(summary['judge_scored_ids'])} · rule-scored: "
        f"{len(summary['rule_scored_ids'])} (ids in summary.json)",
        "",
    ]
    return "\n".join(L)


def failures_md(out: RunOutput) -> str:
    L = ["# All failures (every attempt, every turn)", ""]
    for r in sorted_failures(out.runs):
        L += [*failure_block(r, full=True), ""]
    if len(L) == 2:
        L.append("None.")
    return "\n".join(L)


def write_report(
    out: RunOutput, settings: Settings, shared: Shared, started: datetime, root: Path = REPORTS_DIR
) -> tuple[Path, dict[str, Any], str]:
    stats = [suite_stats(s, out.runs, shared) for s in out.suites]
    stamp = started.strftime("%Y%m%d-%H%M%S")
    summary = build_summary(out, stats, settings, shared, started.isoformat(timespec="seconds"))
    md = summary_md(summary, stats, out)
    d = root / stamp
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (d / "SUMMARY.md").write_text(md, encoding="utf-8")
    (d / "failures.md").write_text(failures_md(out), encoding="utf-8")
    with (d / "results.jsonl").open("w", encoding="utf-8") as fh:
        for r in out.runs:
            fh.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
    with (d / "cases.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "suite",
                "id",
                "category",
                "severity",
                "attempt",
                "status",
                "method",
                "first_sentence_max_s",
                "ttft_max_s",
                "total_s",
                "first_failure",
            ]
        )
        for r in out.runs:
            lat = r.latency_summary()
            first = r.failures[0][1] if r.failures else ""
            w.writerow(
                [
                    r.case.suite,
                    r.case.id,
                    r.case.category,
                    r.case.severity or "",
                    r.attempt,
                    r.status,
                    r.method,
                    lat["first_sentence_max_s"],
                    lat["ttft_max_s"],
                    lat["total_s"],
                    first,
                ]
            )
    return d, summary, md
