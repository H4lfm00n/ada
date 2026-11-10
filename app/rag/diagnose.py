"""`make rag-diagnose`: for each question, what was retrieved and what the bot said.

Per question (evals/real_docs_qa.yaml), on a fresh session through the real pipeline:
  * the top 3 chunks by score (file > heading, score; `*` = passed rag_min_score and
    reached the prompt), plus every chunk that reached the prompt,
  * the bot's final answer and where it came from (model or a canned line),
  * hints: expected file retrieved? expected facts in the answer? "not sure" when the docs
    do answer? anything from the `avoid` list (invented links, false promises)?

Hints are for diagnosis, not a pass/fail gate: the M4 eval runner owns scoring. The
`stage` column says where a miss happened: `retrieval` (the right doc never reached the
prompt) or `answer` (it did, and the model still missed).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.config import REPO_ROOT, Settings
from app.interfaces import Chunk
from app.lines import LineBook
from app.ollama_client import OllamaChatModel, OllamaClient
from app.pipeline import ChatPipeline, TurnResult
from app.prompt import PromptBuilder
from app.rag.retriever import Hit, open_retriever
from app.sessions import InMemorySessionManager
from app.sinks import CollectingSink, NoopActionHandler

REPORT_DIR = REPO_ROOT / "reports" / "rag"
TOP_N = 3

# The bot declining a lab fact. The prompt's fixed phrase is "not sure"; the rest catch
# paraphrases so a "docs answer it but the bot ducked" miss is visible either way.
NOT_SURE_RE = re.compile(
    r"not sure|don't know|do not know|no idea|can't say for sure|not (?:been )?published",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class QACase:
    n: int
    question: str
    expect: tuple[str, ...]  # files that hold the answer
    want: str  # "answer" (docs answer it) | "unknown" (docs say it's not published)
    facts: tuple[tuple[str, ...], ...]  # every group must match; any alternative in a group
    avoid: tuple[str, ...]  # regexes that must not match the answer
    note: str = ""


def _group(item: Any) -> tuple[str, ...]:
    if isinstance(item, str):
        return (item,)
    if isinstance(item, list) and item and all(isinstance(x, str) for x in item):
        return tuple(item)
    raise ValueError(f"fact must be a string or a list of strings, got {item!r}")


def load_cases(path: Path) -> list[QACase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = []
    for raw in data.get("cases") or []:
        want = raw.get("want", "answer")
        if want not in ("answer", "unknown"):
            raise ValueError(f"{path.name} case {raw.get('n')}: want must be answer|unknown")
        expect = raw.get("expect") or []
        cases.append(
            QACase(
                n=int(raw["n"]),
                question=str(raw["q"]),
                expect=tuple([expect] if isinstance(expect, str) else expect),
                want=want,
                facts=tuple(_group(f) for f in raw.get("facts") or []),
                avoid=tuple(raw.get("avoid") or []),
                note=str(raw.get("note") or ""),
            )
        )
    if len({c.n for c in cases}) != len(cases):
        raise ValueError(f"{path.name}: case numbers must be unique")
    return cases


def hit_file(h: Hit) -> str:
    return h.chunk_id.rsplit("#", 1)[0]


@dataclass(slots=True)
class Verdict:
    retrieved_ok: bool  # an expected file reached the prompt (or nothing expected)
    expected_rank: int | None  # 1-based rank of the best expected chunk, ignoring the cutoff
    said_not_sure: bool
    missing_facts: list[str] = field(default_factory=list)
    avoided_hits: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        out = []
        if not self.retrieved_ok:
            where = f"rank {self.expected_rank}" if self.expected_rank else "not ranked"
            out.append(f"retrieval: expected doc not in prompt ({where})")
        if self.missing_facts:
            out.append("answer: missing " + "; ".join(self.missing_facts))
        if self.avoided_hits:
            out.append("answer: contains " + "; ".join(self.avoided_hits))
        return out

    @property
    def stage(self) -> str:
        if not self.retrieved_ok:
            return "retrieval"
        return "answer" if self.problems else "ok"


def mentions(answer: str, fact: str) -> bool:
    """Case-insensitive. Short words ("no", "yes", "7") must be whole words, so "no"
    doesn't match "not" or "know"; longer facts match as substrings ("robotic dog" ->
    "robotic dogs")."""
    if len(fact) <= 3:
        return re.search(rf"\b{re.escape(fact)}\b", answer, re.IGNORECASE) is not None
    return fact.lower() in answer.lower()


def judge(case: QACase, answer: str, in_prompt: list[Chunk], hits: list[Hit]) -> Verdict:
    files_in_prompt = {c.source_file for c in in_prompt}
    retrieved_ok = not case.expect or bool(files_in_prompt & set(case.expect))
    rank = next((i for i, h in enumerate(hits, 1) if hit_file(h) in case.expect), None)
    missing = [" | ".join(g) for g in case.facts if not any(mentions(answer, a) for a in g)]
    avoided = [p for p in case.avoid if re.search(p, answer, re.IGNORECASE)]
    said = bool(NOT_SURE_RE.search(answer))
    if case.want == "answer" and said and not missing:
        missing.append('(answered, but also said "not sure")')
    elif case.want == "answer" and said:
        missing.append('(said "not sure" though the docs answer it)')
    return Verdict(retrieved_ok, rank, said, missing, avoided)


def build_pipeline(
    settings: Settings, client: OllamaClient
) -> tuple[ChatPipeline, InMemorySessionManager, Any, OllamaChatModel, list[str]]:
    sink = CollectingSink()
    sessions = InMemorySessionManager.from_settings(settings)
    model = OllamaChatModel(client, settings)
    retriever, warnings = open_retriever(client, settings)
    pipeline = ChatPipeline(
        model=model,
        prompt=PromptBuilder.from_settings(settings),
        lines=LineBook.from_settings(settings),
        sessions=sessions,
        sink=sink,
        actions=NoopActionHandler(),
        reply_timeout_s=settings.request_timeout_s,
        retriever=retriever,
        rag_top_k=settings.rag_top_k,
        retrieval_timeout_s=settings.rag_timeout_s,
    )
    return pipeline, sessions, retriever, model, warnings


def print_case(case: QACase, r: TurnResult, hits: list[Hit], v: Verdict) -> None:
    passed = {c.metadata.get("id") for c in r.retrieved}
    want = "docs answer it" if case.want == "answer" else "docs say: not published"
    print(f"\n[{case.n:02d}] {case.question}")
    print(f"     expect: {'|'.join(case.expect) or '(no doc)'} ({want})")
    for i, h in enumerate(hits[:TOP_N], 1):
        mark = "*" if h.chunk_id in passed else " "
        print(f"     top{i} {mark}{h.score:.3f}  {hit_file(h)} > {h.heading}")
    extra = [
        c for c in r.retrieved if c.metadata.get("id") not in {h.chunk_id for h in hits[:TOP_N]}
    ]
    for c in extra:
        print(f"     also  *{c.score:.3f}  {c.source_file} > {c.heading}")
    print(
        f"     in prompt: {len(r.retrieved)} chunk(s)   source: {r.source}"
        + (f"   RETRIEVAL ERROR {r.retrieval_error}" if r.retrieval_error else "")
        + (f"   ERROR {r.error}" if r.error else "")
    )
    print(f"     answer: {r.text}")
    print(f"     -> {v.stage.upper()}" + ("" if not v.problems else ": " + " / ".join(v.problems)))


async def run_diagnose(
    settings: Settings, path: Path, *, only: set[int] | None = None, label: str = ""
) -> int:
    cases = [c for c in load_cases(path) if not only or c.n in only]
    rows: list[dict[str, Any]] = []
    async with OllamaClient.from_settings(settings) as client:
        pipeline, sessions, retriever, model, warnings = build_pipeline(settings, client)
        for w in warnings:
            print(f"WARN {w}")
        print(
            f"docs={settings.lab_docs_dir} embed={settings.embed_model} chat={settings.chat_model}"
            f" cutoff={settings.rag_min_score} k={settings.rag_top_k}"
            f" prompt={settings.system_prompt_file}" + (f" label={label}" if label else "")
        )
        t0 = time.perf_counter()
        async for _ in model.stream(pipeline.prompt.build([], "Hi!")):
            pass  # warm up: model load + prompt cache, so case 1 isn't skewed
        print(f"warmup {time.perf_counter() - t0:.1f}s")

        for case in cases:
            sid = sessions.create()  # fresh visitor per question
            r = await pipeline.handle(sid, case.question)
            sessions.end(sid)
            hits = list(getattr(retriever, "last_hits", []))
            v = judge(case, r.text, r.retrieved, hits)
            print_case(case, r, hits, v)
            rows.append(
                {
                    "n": case.n,
                    "q": case.question,
                    "expect": list(case.expect),
                    "want": case.want,
                    "top": [
                        {"file": hit_file(h), "heading": h.heading, "score": round(h.score, 4)}
                        for h in hits[:TOP_N]
                    ],
                    "in_prompt": [c.metadata.get("id") for c in r.retrieved],
                    "expected_rank": v.expected_rank,
                    "source": r.source,
                    "answer": r.text,
                    "stage": v.stage,
                    "problems": v.problems,
                    "ttft_s": r.ttft_s,
                    "retrieval_s": r.retrieval_s,
                }
            )

    print("\n--- summary ---")
    print(f"{'#':>3} {'stage':<9} {'rank':>4} {'top1':>6}  question")
    for row in rows:
        top1 = f"{row['top'][0]['score']:.3f}" if row["top"] else "  -  "
        rank = row["expected_rank"] or "-"
        print(f"{row['n']:>3} {row['stage']:<9} {rank:>4} {top1:>6}  {row['q'][:60]}")
    counts = {s: sum(1 for r in rows if r["stage"] == s) for s in ("ok", "retrieval", "answer")}
    print(
        f"ok {counts['ok']}/{len(rows)}   retrieval misses {counts['retrieval']}"
        f"   answer misses {counts['answer']}   (hints only; M4 scores for real)"
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"-{label}" if label else ""
    out = REPORT_DIR / f"diagnose{tag}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    meta = {
        "label": label,
        "chat_model": settings.chat_model,
        "embed_model": settings.embed_model,
        "cutoff": settings.rag_min_score,
        "k": settings.rag_top_k,
        "prompt": settings.system_prompt_file,
    }
    out.write_text(json.dumps({"meta": meta, "rows": rows}, indent=2), encoding="utf-8")
    try:
        shown = out.relative_to(REPO_ROOT)
    except ValueError:
        shown = out
    print(f"report: {shown}")
    return 0
