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
import statistics
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
from app.rag.retriever import Hit, cutoff_str, open_retriever
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
    expect: tuple[str, ...]  # "file.md" or "file.md > Heading start" that hold the answer
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


class _NoDupLoader(yaml.SafeLoader):
    """A repeated key (two `avoid:` lines) silently keeps only the last one. Refuse it."""


def _no_dup_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    keys = [loader.construct_object(k) for k, _ in node.value]
    dups = sorted({str(k) for k in keys if keys.count(k) > 1})
    if dups:
        raise ValueError(f"duplicate key(s) {dups} near line {node.start_mark.line + 1}")
    return yaml.SafeLoader.construct_mapping(loader, node)


_NoDupLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_mapping)


def load_cases(path: Path) -> list[QACase]:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_NoDupLoader) or {}
    always = tuple(str(p) for p in data.get("always_avoid") or [])
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
                avoid=tuple(raw.get("avoid") or []) + always,
                note=str(raw.get("note") or ""),
            )
        )
    if len({c.n for c in cases}) != len(cases):
        raise ValueError(f"{path.name}: case numbers must be unique")
    return cases


def hit_file(h: Hit) -> str:
    return h.chunk_id.rsplit("#", 1)[0]


def matches(expect: str, source_file: str, heading: str) -> bool:
    """ "faq.md" matches any section of faq.md; "faq.md > How can I" matches by heading start."""
    file, _, head = expect.partition(" > ")
    if file.strip() != source_file:
        return False
    return not head or heading.lower().startswith(head.strip().lower())


def expected(case: QACase, source_file: str, heading: str) -> bool:
    return any(matches(e, source_file, heading) for e in case.expect)


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
    retrieved_ok = not case.expect or any(
        expected(case, c.source_file, c.heading) for c in in_prompt
    )
    rank = next((i for i, h in enumerate(hits, 1) if expected(case, hit_file(h), h.heading)), None)
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


def print_retrieval(case: QACase, r: TurnResult, hits: list[Hit]) -> None:
    passed = {c.metadata.get("id") for c in r.retrieved}
    want = "docs answer it" if case.want == "answer" else "docs say: not published"
    print(f"\n[{case.n:02d}] {case.question}")
    print(f"     expect: {' | '.join(case.expect) or '(no doc)'} ({want})")
    for i, h in enumerate(hits[:TOP_N], 1):
        mark = "*" if h.chunk_id in passed else " "
        print(f"     top{i} {mark}{h.score:.3f}  {hit_file(h)} > {h.heading}")
    shown = {h.chunk_id for h in hits[:TOP_N]}
    for c in r.retrieved:
        if c.metadata.get("id") not in shown:
            print(f"     also  *{c.score:.3f}  {c.source_file} > {c.heading}")
    err = f"   RETRIEVAL ERROR {r.retrieval_error}" if r.retrieval_error else ""
    print(f"     in prompt: {len(r.retrieved)} chunk(s){err}")


def print_answer(r: TurnResult, v: Verdict, run: int, repeat: int) -> None:
    tag = f"answer{run + 1}" if repeat > 1 else "answer"
    ttft = f"{r.ttft_s:.2f}s" if r.ttft_s is not None else "-"
    src = "" if r.source == "model" else f" [{r.source}]"
    err = f" ERROR {r.error}" if r.error else ""
    print(f"     {tag} ({ttft}){src}{err}: {r.text}")
    print(
        f"       -> {v.stage.upper()}" + ("" if not v.problems else ": " + " / ".join(v.problems))
    )


def print_latency(answers: list[dict[str, Any]], target_s: float) -> None:
    """First token for a NEW question vs a repeat of the same one.

    A repeat reuses Ollama's prompt cache for the whole prompt (same docs, same question),
    so it says nothing about a real visitor. Only first asks count against the target.
    """

    def ttfts(first: bool) -> list[float]:
        return sorted(
            x["ttft_s"] for x in answers if x.get("first_ask") is first and x["ttft_s"] is not None
        )

    first, repeats = ttfts(True), ttfts(False)
    if first:
        verdict = "PASS" if statistics.median(first) <= target_s else "MISS"
        print(
            f"first token, new question: median {statistics.median(first):.2f}s"
            f"  max {first[-1]:.2f}s  (target <= {target_s}s) [{verdict}]"
        )
    if repeats:
        print(
            f"first token, cached repeat: median {statistics.median(repeats):.2f}s (not a target)"
        )
    pre = [
        (x["prefill_tokens"], x["prefill_s"])
        for x in answers
        if x.get("first_ask") and x.get("prefill_tokens") and x.get("prefill_s")
    ]
    if pre:
        # Ollama's prompt_eval_count includes cached tokens: it is the prompt size, not
        # the work done. prompt_eval_duration is the work. (`make prefill-probe` splits them.)
        toks = statistics.median(t for t, _ in pre)
        secs = statistics.median(d for _, d in pre)
        print(f"new question: prompt median {toks:.0f} tokens, prefill median {secs:.2f}s")


STAGE_ORDER = ("ok", "answer", "retrieval")


def worst(stages: Any) -> str:
    return max(stages, key=STAGE_ORDER.index, default="ok")


async def run_diagnose(
    settings: Settings,
    path: Path,
    *,
    only: set[int] | None = None,
    label: str = "",
    repeat: int = 1,
) -> int:
    repeat = max(1, repeat)
    cases = [c for c in load_cases(path) if not only or c.n in only]
    rows: list[dict[str, Any]] = []
    async with OllamaClient.from_settings(settings) as client:
        pipeline, sessions, retriever, model, warnings = build_pipeline(settings, client)
        for w in warnings:
            print(f"WARN {w}")
        print(
            f"docs={settings.lab_docs_dir} embed={settings.embed_model} chat={settings.chat_model}"
            f" cutoff={cutoff_str(settings)}"
            f" k={settings.rag_top_k} temp={settings.temperature} repeat={repeat}"
            f" prompt={settings.system_prompt_file}" + (f" label={label}" if label else "")
        )
        t0 = time.perf_counter()
        async for _ in model.stream(pipeline.prompt.build([], "Hi!")):
            pass  # warm up: model load + prompt cache, so case 1 isn't skewed
        print(f"warmup {time.perf_counter() - t0:.1f}s")

        for case in cases:
            runs = []
            for run in range(repeat):
                sid = sessions.create()  # fresh visitor per question and per repeat
                r = await pipeline.handle(sid, case.question)
                sessions.end(sid)
                hits = list(getattr(retriever, "last_hits", []))
                v = judge(case, r.text, r.retrieved, hits)
                if run == 0:
                    print_retrieval(case, r, hits)
                print_answer(r, v, run, repeat)
                runs.append(
                    {
                        "answer": r.text,
                        "source": r.source,
                        "stage": v.stage,
                        "problems": v.problems,
                        "ttft_s": r.ttft_s,
                        "retrieval_s": r.retrieval_s,
                        "first_ask": run == 0,
                        # prompt size incl. cached tokens; prefill_s is the work done
                        "prefill_tokens": r.stats.prompt_eval_count if r.stats else None,
                        "prefill_s": r.stats.prompt_eval_duration / 1e9 if r.stats else None,
                    }
                )
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
                    "ok_runs": sum(1 for x in runs if x["stage"] == "ok"),
                    "stage": worst(x["stage"] for x in runs),
                    "runs": runs,
                }
            )

    print("\n--- summary ---")
    print(f"{'#':>3} {'stage':<9} {'ok':>5} {'rank':>4} {'top1':>6}  question")
    for row in rows:
        top1 = f"{row['top'][0]['score']:.3f}" if row["top"] else "  -  "
        rank = row["expected_rank"] or "-"
        ok = f"{row['ok_runs']}/{repeat}"
        print(f"{row['n']:>3} {row['stage']:<9} {ok:>5} {rank:>4} {top1:>6}  {row['q'][:58]}")
    counts = {s: sum(1 for r in rows if r["stage"] == s) for s in ("ok", "retrieval", "answer")}
    answers = [x for r in rows for x in r["runs"]]
    ok_answers = sum(1 for x in answers if x["stage"] == "ok")
    print(
        f"questions ok on every run {counts['ok']}/{len(rows)}"
        f"   answers ok {ok_answers}/{len(answers)}"
        f"   retrieval misses {counts['retrieval']}   answer misses {counts['answer']}"
    )
    print_latency(answers, settings.target_first_token_s)
    print("(hints only; M4 scores for real)")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"-{label}" if label else ""
    out = REPORT_DIR / f"diagnose{tag}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    meta = {
        "label": label,
        "chat_model": settings.chat_model,
        "embed_model": settings.embed_model,
        "cutoff": settings.rag_min_score,
        "extra_cutoff": settings.rag_extra_min_score,
        "temperature": settings.temperature,
        "repeat": repeat,
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
