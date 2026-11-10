"""`make prefill-probe`: why does a lab question take ~2.5 s to its first token?

Chat without docs starts in ~0.9 s; a new lab question (system prompt + ~600-750 tokens of
docs) takes ~2.5 s. Two explanations predict different numbers:

  A. Prefix reuse works, prefill is just slow: Ollama reuses the cached ~2,100-token static
     system prompt and only processes the docs + question. Swapping docs then costs about
     (docs tokens / prefill rate).
  B. Prefix reuse breaks when the docs change: Ollama re-processes the whole prompt.
     Swapping docs then costs about (all tokens / prefill rate).

Each call asks for 1 output token, so the time is prefill. Ollama's prompt_eval_count
includes cached tokens, so the token counts are totals, and prompt_eval_duration is the
work actually done.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.config import Settings, load_settings
from app.interfaces import Message
from app.ollama_client import OllamaClient, OllamaError
from app.prompt import PromptBuilder
from app.rag.retriever import open_retriever

Q_A = "Where do meetings take place?"
Q_B = "Can I start an overnight job remotely?"


@dataclass(slots=True)
class Probe:
    name: str
    tokens: int
    prefill_s: float
    wall_s: float


async def one_call(
    client: OllamaClient, settings: Settings, name: str, msgs: list[Message]
) -> Probe:
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    stats = None
    async for d in client.chat_stream(
        settings.chat_model,
        msgs,
        think=settings.think,
        options={"temperature": 0, "num_ctx": settings.num_ctx, "num_predict": 1},
        keep_alive=settings.keep_alive,
    ):
        if d.done:
            stats = d.stats
    wall = loop.time() - t0
    if stats is None:
        raise OllamaError(f"{name}: no stats in the final message")
    return Probe(name, stats.prompt_eval_count, stats.prompt_eval_duration / 1e9, wall)


def verdict(swap: Probe, nodocs: Probe, rate: float) -> str:
    tail = max(swap.tokens - nodocs.tokens, 1) + 20  # the docs + question, roughly
    if_reuse = tail / rate
    if_full = swap.tokens / rate
    mid = (if_reuse + if_full) / 2
    which = (
        "A: prefix reuse works; the docs block itself is the cost"
        if swap.prefill_s < mid
        else "B: prefix reuse BREAKS when the docs change; the whole prompt is re-processed"
    )
    return (
        f"swap took {swap.prefill_s:.2f}s. Predicted: {if_reuse:.2f}s if reuse works"
        f" (~{tail} new tokens), {if_full:.2f}s if not ({swap.tokens} tokens).\n"
        f"VERDICT {which}"
    )


async def run(settings: Settings) -> int:
    now = datetime(2026, 11, 10, 15, 0)  # fixed, so {{current_time}} can't change mid-probe
    async with OllamaClient.from_settings(settings) as client:
        retriever, warnings = open_retriever(client, settings)
        for w in warnings:
            print(f"WARN {w}")
        docs_a = await retriever.search(Q_A, settings.rag_top_k)
        docs_b = await retriever.search(Q_B, settings.rag_top_k)
        if not docs_a or not docs_b:
            print("probe needs docs for both questions; run `make ingest` first")
            return 1
        pb = PromptBuilder.from_settings(settings)
        a = pb.build([], Q_A, docs_a, now=now)
        b = pb.build([], Q_B, docs_b, now=now)
        nodocs_b = pb.build([], Q_B, [], now=now)
        cold = [Message("system", f"probe {uuid.uuid4().hex}\n{a[0].content}"), a[1]]

        steps = [
            ("cold: whole prompt, nothing cached", cold),
            ("prime: docs A + question A", a),
            ("SWAP: docs B + question B", b),
            ("repeat: docs B + question B again", b),
            ("no docs + question B", nodocs_b),
        ]
        probes = [await one_call(client, settings, n, m) for n, m in steps]

    print(f"chat={settings.chat_model} num_ctx={settings.num_ctx} k={settings.rag_top_k}")
    print(f"{'step':<38} {'tokens':>7} {'prefill':>8} {'wall':>7}")
    for p in probes:
        print(f"{p.name:<38} {p.tokens:>7} {p.prefill_s:>7.2f}s {p.wall_s:>6.2f}s")
    cold_p, _, swap, _, nodocs = probes
    rate = cold_p.tokens / cold_p.prefill_s if cold_p.prefill_s else 0.0
    print(f"\nprefill rate, nothing cached: {rate:.0f} tok/s")
    if rate:
        print(verdict(swap, nodocs, rate))
    return 0


def main() -> None:
    try:
        sys.exit(asyncio.run(run(load_settings())))
    except OllamaError as exc:
        print(f"PROBE ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
