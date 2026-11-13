"""CLI for the eval harness.

  python -m app.evals run  [--suite golden,redteam,golden_real] [--repeat N] [--ids a,b]
                           [--no-judge] [--judge-model TAG]
  python -m app.evals lint

Exit codes (so `make eval` can gate merges): 0 = every gating suite at or above its bar
and no critical red-team failure; 1 = below a bar / critical failure / lint errors;
2 = setup problem (Ollama down, model missing, kill switch on, bad eval files).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

from app.config import load_settings
from app.evals.lint import lint
from app.evals.report import write_report
from app.evals.runner import SetupError, run_evals
from app.evals.spec import DEFAULT_SUITES, SpecError, load_shared, load_suites
from app.ollama_client import OllamaClient, OllamaError


def _cmd_lint() -> int:
    settings = load_settings()
    res = lint(settings)
    for w in res.warnings:
        print(f"WARN  {w}")
    for e in res.errors:
        print(f"ERROR {e}")
    n = sum(len(s.cases) for s in res.suites)
    print(
        f"eval-lint: {len(res.suites)} suites, {n} cases, {len(res.errors)} errors, "
        f"{len(res.warnings)} warnings"
    )
    return 0 if res.ok else 1


async def _cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.judge_model:
        settings = settings.model_copy(update={"judge_model": args.judge_model})
    names = tuple(s.strip() for s in args.suite.split(",") if s.strip())
    try:
        shared = load_shared(settings.bot_name)
        suites = load_suites(names, shared)
    except SpecError as exc:
        print("eval files have problems (run `make eval-lint`):")
        for p in exc.problems:
            print(f"  {p}")
        return 2
    if args.ids:
        want = {i.strip() for i in args.ids.split(",") if i.strip()}
        for s in suites:
            s.cases = [c for c in s.cases if c.id in want]
        suites = [s for s in suites if s.cases]
        found = {c.id for s in suites for c in s.cases}
        if missing := sorted(want - found):
            print(f"unknown ids: {', '.join(missing)}")
            return 2
    started = datetime.now()
    try:
        async with OllamaClient.from_settings(settings) as client:
            out = await run_evals(
                client, settings, suites, shared, repeat=args.repeat, use_judge=not args.no_judge
            )
    except (SetupError, OllamaError) as exc:
        print(f"SETUP ERROR: {exc}")
        return 2
    d, summary, md = write_report(out, settings, shared, started)
    print("\n" + md)
    print(f"\nreport: {d}")
    if args.ids:
        print("(--ids: partial run; the gate verdict covers only these cases)")
    return 0 if summary["gate"]["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.evals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the eval suites through the full pipeline")
    run.add_argument("--suite", default=",".join(DEFAULT_SUITES))
    run.add_argument("--repeat", type=int, default=1, help="run every case N times; flags flaky")
    run.add_argument("--ids", default="", help="comma-separated case ids (partial run)")
    run.add_argument("--no-judge", action="store_true", help="rules only; judge cases provisional")
    run.add_argument("--judge-model", default="", help="override config judge_model")
    sub.add_parser("lint", help="check eval files + lines without a model")
    args = ap.parse_args(argv)
    if args.cmd == "lint":
        return _cmd_lint()
    if args.repeat < 1:
        ap.error("--repeat must be >= 1")
    return asyncio.run(_cmd_run(args))


if __name__ == "__main__":
    sys.exit(main())
