"""`make bench`: first-token latency and decode speed for the chat model (thinking off).

Usage: python -m app.bench [--runs N] [--strict] [--no-overhead]
  Override the model under test with ADA_CHAT_MODEL=<tag>.

Method
  * One warmup request loads the model (reported as cold load, excluded from stats).
  * TTFT = client wall clock from request start to the first non-empty reply text.
  * Decode tok/s = Ollama's own eval_count / eval_duration (server-side, no HTTP noise).
  * Guard + embed overhead: timed separately, since in the real pipeline they run
    before the chat model and add to what a visitor waits for.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.config import REPO_ROOT, Settings, load_settings
from app.interfaces import Message
from app.ollama_client import OllamaClient, OllamaError, normalize_tag

BENCH_FILE = REPO_ROOT / "evals" / "bench.yaml"
REPORT_DIR = REPO_ROOT / "reports" / "bench"


@dataclass(slots=True)
class RunResult:
    question: str
    ttft_s: float
    total_s: float
    reply_tokens: int
    decode_tok_s: float
    prompt_tokens: int
    prefill_tok_s: float
    thinking_chars: int
    think_tag_in_reply: bool
    reply: str


def load_bench(path: Path = BENCH_FILE) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data.get("questions"):
        raise ValueError(f"{path} has no questions")
    return data


async def time_chat(
    client: OllamaClient, settings: Settings, system: str, question: str
) -> RunResult:
    messages = [Message("system", system), Message("user", question)]
    options = {
        "temperature": settings.temperature,
        "num_ctx": settings.num_ctx,
        "num_predict": settings.max_reply_tokens,
    }
    reply, thinking_chars, ttft, stats = "", 0, None, None
    t0 = time.perf_counter()
    async for d in client.chat_stream(
        settings.chat_model,
        messages,
        think=settings.think,
        options=options,
        keep_alive=settings.keep_alive,
    ):
        thinking_chars += len(d.thinking)
        if d.content:
            if ttft is None and d.content.strip():
                ttft = time.perf_counter() - t0
            reply += d.content
        if d.done:
            stats = d.stats
    total = time.perf_counter() - t0
    if stats is None:
        raise OllamaError("stream ended without a final stats message")
    return RunResult(
        question=question,
        ttft_s=ttft if ttft is not None else total,
        total_s=total,
        reply_tokens=stats.eval_count,
        decode_tok_s=stats.decode_tok_s,
        prompt_tokens=stats.prompt_eval_count,
        prefill_tok_s=stats.prefill_tok_s,
        thinking_chars=thinking_chars,
        think_tag_in_reply="<think>" in reply,
        reply=reply.strip(),
    )


async def time_guard(client: OllamaClient, settings: Settings, text: str) -> float:
    t0 = time.perf_counter()
    async for _ in client.chat_stream(
        settings.guard_model,
        [Message("user", text)],
        options={"num_predict": 10, "temperature": 0},
        keep_alive=settings.keep_alive,
    ):
        pass
    return time.perf_counter() - t0


async def time_embed(client: OllamaClient, settings: Settings, text: str) -> float:
    t0 = time.perf_counter()
    await client.embed(settings.embed_model, [text], settings.keep_alive)
    return time.perf_counter() - t0


def summarize(results: list[RunResult], settings: Settings) -> dict[str, Any]:
    ttfts = [r.ttft_s for r in results]
    rates = [r.decode_tok_s for r in results if r.decode_tok_s > 0]
    med_ttft = statistics.median(ttfts)
    med_rate = statistics.median(rates) if rates else 0.0
    return {
        "ttft_median_s": med_ttft,
        "ttft_max_s": max(ttfts),
        "decode_tok_s_median": med_rate,
        "decode_tok_s_min": min(rates) if rates else 0.0,
        "prefill_tok_s_median": statistics.median([r.prefill_tok_s for r in results]),
        "thinking_leaked": any(r.thinking_chars or r.think_tag_in_reply for r in results),
        "meets_ttft_target": med_ttft <= settings.target_first_token_s,
        "meets_decode_target": med_rate >= settings.target_decode_tok_s,
    }


def _ok(flag: bool) -> str:
    return "PASS" if flag else "MISS"


async def run_bench(
    settings: Settings, client: OllamaClient, runs: int, overhead: bool
) -> dict[str, Any]:
    bench = load_bench()
    installed = {normalize_tag(t) for t in await client.list_models()}
    if normalize_tag(settings.chat_model) not in installed:
        raise OllamaError(f"chat model {settings.chat_model} not installed")
    version = await client.version()

    print(
        f"Ollama v{version} | chat={settings.chat_model} | think={settings.think}"
        f" | num_ctx={settings.num_ctx} | max_reply_tokens={settings.max_reply_tokens}"
    )

    warm = await time_chat(client, settings, bench["system"], "Say hello.")
    print(f"warmup (incl. model load): {warm.total_s:.2f}s\n")

    results: list[RunResult] = []
    header = (
        f"{'#':>2}  {'TTFT s':>6}  {'total s':>7}  {'tok':>4}  {'tok/s':>6}  {'think':>5}  question"
    )
    print(header)
    print("-" * len(header))
    for _ in range(runs):
        for q in bench["questions"]:
            r = await time_chat(client, settings, bench["system"], q)
            results.append(r)
            think = "LEAK" if (r.thinking_chars or r.think_tag_in_reply) else "off"
            print(
                f"{len(results):>2}  {r.ttft_s:6.2f}  {r.total_s:7.2f}  {r.reply_tokens:4d}"
                f"  {r.decode_tok_s:6.1f}  {think:>5}  {q}"
            )

    summary = summarize(results, settings)
    print(
        f"\nTTFT median {summary['ttft_median_s']:.2f}s (max {summary['ttft_max_s']:.2f}s)"
        f"  target <= {settings.target_first_token_s}s  [{_ok(summary['meets_ttft_target'])}]"
    )
    print(
        f"decode median {summary['decode_tok_s_median']:.1f} tok/s"
        f" (min {summary['decode_tok_s_min']:.1f})"
        f"  target >= {settings.target_decode_tok_s}  [{_ok(summary['meets_decode_target'])}]"
    )
    print(f"prefill median {summary['prefill_tok_s_median']:.0f} tok/s")
    leaked = summary["thinking_leaked"]
    print(f"thinking: {'LEAKED, think=false is not honored' if leaked else 'off (verified)'}")

    overhead_report: dict[str, Any] = {}
    if overhead:
        probe = bench.get("probe", bench["questions"][0])
        for role, fn in (("guard", time_guard), ("embed", time_embed)):
            tag = settings.model_tags[role]
            if normalize_tag(tag) not in installed:
                print(f"{role} overhead: skipped ({tag} not installed)")
                continue
            try:
                await fn(client, settings, probe)  # warm/load
                times = [await fn(client, settings, probe) for _ in range(3)]
            except OllamaError as exc:
                print(f"{role} overhead: error: {exc}")
                continue
            overhead_report[f"{role}_median_s"] = statistics.median(times)
            print(f"{role} overhead ({tag}): median {statistics.median(times):.3f}s")
        if overhead_report:
            est = summary["ttft_median_s"] + sum(overhead_report.values())
            overhead_report["est_pipeline_ttft_s"] = est
            print(
                f"est. visitor wait to first word (guard + embed + chat TTFT): {est:.2f}s"
                f"  [{_ok(est <= settings.target_first_token_s)}]"
            )

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "ollama_version": version,
        "settings": {
            "chat_model": settings.chat_model,
            "guard_model": settings.guard_model,
            "embed_model": settings.embed_model,
            "think": settings.think,
            "num_ctx": settings.num_ctx,
            "max_reply_tokens": settings.max_reply_tokens,
            "temperature": settings.temperature,
        },
        "warmup_s": warm.total_s,
        "summary": summary,
        "overhead": overhead_report,
        "runs": [asdict(r) for r in results],
    }


def write_report(report: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report["timestamp"].replace(":", "").replace("-", "")
    path = REPORT_DIR / f"bench-{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


async def _amain(runs: int, strict: bool, overhead: bool) -> int:
    settings = load_settings()
    async with OllamaClient.from_settings(settings) as client:
        try:
            report = await run_bench(settings, client, runs, overhead)
        except OllamaError as exc:
            print(f"BENCH ERROR: {exc}", file=sys.stderr)
            return 1
    print(f"\nreport: {write_report(report)}")
    s = report["summary"]
    missed = not (s["meets_ttft_target"] and s["meets_decode_target"]) or s["thinking_leaked"]
    return 1 if (strict and missed) else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=1, help="passes over the question set")
    parser.add_argument("--strict", action="store_true", help="exit 1 if a target is missed")
    parser.add_argument("--no-overhead", action="store_true", help="skip guard/embed timing")
    args = parser.parse_args()
    sys.exit(asyncio.run(_amain(max(1, args.runs), args.strict, not args.no_overhead)))


if __name__ == "__main__":
    main()
