"""Lab docs CLI.

  python -m app.rag ingest            parse lab_docs_dir, embed, write the index
  python -m app.rag search "question" show what the assistant would retrieve
  python -m app.rag probe             score the golden lab questions, to calibrate rag_min_score

Every command uses config.yaml (lab_docs_dir, embed_model, ...), so point it at the eval
corpus with ADA_LAB_DOCS_DIR=evals/fixtures/lab_docs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.config import REPO_ROOT, Settings, load_settings
from app.ollama_client import OllamaClient, OllamaError
from app.rag.retriever import OllamaRetriever, build_index
from app.rag.store import RagIndexError

GOLDEN = REPO_ROOT / "evals" / "golden.yaml"
REPORT_DIR = REPO_ROOT / "reports" / "rag"


async def cmd_ingest(settings: Settings) -> int:
    print(f"docs: {settings.lab_docs_dir}   embed: {settings.embed_model}")
    t0 = time.perf_counter()
    async with OllamaClient.from_settings(settings) as client:
        report = await build_index(client, settings)
    print(
        f"indexed {report.chunks} sections from {report.files} files"
        f" ({report.skipped_sections} left out for TODO) in {time.perf_counter() - t0:.1f}s"
    )
    print(f"index: {report.index_path.relative_to(REPO_ROOT)}   dims: {report.dims or 'n/a'}")
    for w in report.warnings:
        print(f"  WARN {w}")
    if not report.chunks:
        print("  WARN no usable sections: every lab question will get 'not sure'")
    return 0


async def cmd_search(settings: Settings, query: str, k: int) -> int:
    async with OllamaClient.from_settings(settings) as client:
        retriever, problems = OllamaRetriever.open(client, settings)
        for p in problems:
            print(f"WARN {p}")
        chunks = await retriever.search(query, k)
        print(f"cutoff {settings.rag_min_score:.2f} -> {len(chunks)} chunk(s) reach the prompt")
        for h in retriever.last_hits[: max(k, 5)]:
            mark = "*" if any(c.metadata["id"] == h.chunk_id for c in chunks) else " "
            print(f" {mark} {h.score:.3f}  {h.chunk_id:<24} {h.heading}")
    return 0


def golden_queries(path: Path = GOLDEN) -> list[tuple[str, str, str]]:
    """(case id, kind, first user message): kind is 'answer' (grounding) or 'dont_know'."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    for case in data["cases"]:
        if "grounding" in (case.get("tags") or []):
            kind = "answer"
        elif case.get("category") == "dont_know":
            kind = "dont_know"
        else:
            continue
        first = next((t["user"] for t in case["turns"] if "user" in t), None)
        if first:
            out.append((case["id"], kind, first))
    return out


async def cmd_probe(settings: Settings) -> int:
    rows: list[dict[str, Any]] = []
    async with OllamaClient.from_settings(settings) as client:
        retriever, problems = OllamaRetriever.open(client, settings)
        for p in problems:
            print(f"WARN {p}")
        for case_id, kind, q in golden_queries():
            t0 = time.perf_counter()
            chunks = await retriever.search(q, settings.rag_top_k)
            ms = (time.perf_counter() - t0) * 1000
            hits = retriever.last_hits
            rows.append(
                {
                    "id": case_id,
                    "kind": kind,
                    "query": q,
                    "ms": round(ms, 1),
                    "passed_cutoff": len(chunks),
                    "top": [
                        {"id": h.chunk_id, "heading": h.heading, "score": round(h.score, 4)}
                        for h in hits[:3]
                    ],
                }
            )

    print(
        f"docs={settings.lab_docs_dir} embed={settings.embed_model} "
        f"cutoff={settings.rag_min_score} k={settings.rag_top_k}\n"
    )
    print(f"{'case':<30} {'kind':<9} {'top1':>6} {'top2':>6} {'#in':>3}  top heading")
    for r in rows:
        t = r["top"]
        s1 = f"{t[0]['score']:.3f}" if t else "  -  "
        s2 = f"{t[1]['score']:.3f}" if len(t) > 1 else "  -  "
        head = t[0]["heading"][:40] if t else ""
        print(f"{r['id'][:30]:<30} {r['kind']:<9} {s1:>6} {s2:>6} {r['passed_cutoff']:>3}  {head}")

    answer_top = [r["top"][0]["score"] for r in rows if r["kind"] == "answer" and r["top"]]
    dk_top = [r["top"][0]["score"] for r in rows if r["kind"] == "dont_know" and r["top"]]
    missed = [r["id"] for r in rows if r["kind"] == "answer" and not r["passed_cutoff"]]
    lat = [r["ms"] for r in rows]
    summary = {
        "cutoff": settings.rag_min_score,
        "answerable_top1_min": min(answer_top, default=None),
        "answerable_top1_median": statistics.median(answer_top) if answer_top else None,
        "dont_know_top1_max": max(dk_top, default=None),
        "dont_know_top1_median": statistics.median(dk_top) if dk_top else None,
        "answerable_with_no_docs": missed,
        "search_ms_median": statistics.median(lat) if lat else None,
        "search_ms_max": max(lat, default=None),
    }
    print("\n--- summary ---")
    for key, val in summary.items():
        print(f"{key}: {round(val, 4) if isinstance(val, float) else val}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"probe-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(f"report: {out.relative_to(REPO_ROOT)}")
    return 1 if missed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="lab docs: ingest, search, probe")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=None)
    sub.add_parser("probe")
    args = parser.parse_args()
    settings = load_settings()
    try:
        if args.cmd == "ingest":
            code = asyncio.run(cmd_ingest(settings))
        elif args.cmd == "search":
            code = asyncio.run(cmd_search(settings, args.query, args.k or settings.rag_top_k))
        else:
            code = asyncio.run(cmd_probe(settings))
    except (OllamaError, RagIndexError, FileNotFoundError) as exc:
        print(f"RAG ERROR: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
