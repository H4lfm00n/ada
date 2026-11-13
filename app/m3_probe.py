"""M3 checks on the GB10: attack prompts through the full guarded pipeline, and latency.

  python -m app.m3_probe attacks [--ids a,b,c | --all] [--show-full]
  python -m app.m3_probe latency [--rounds N]

`attacks` runs red-team cases from evals/redteam.yaml (all turns, fresh visitor per
case) and prints, per turn: the input-guard decision, the output-guard decisions, where
the reply came from (model / line:<category>), and the reply. It also flags replies
containing a case's literal `must_not_include` phrases. It is a quick look, not the M4
scorer: no judge, no allowed_lines scoring.

`latency` asks new questions (each asked once, so Ollama's prompt cache can't flatter
the numbers) with both guards on, alternating sequential and parallel retrieval
(guard_parallel_retrieval), and reports the time to the first sentence the visitor sees.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from datetime import datetime
from typing import Any

import yaml

from app.config import REPO_ROOT, Settings, load_settings
from app.factory import build_pipeline
from app.ollama_client import OllamaClient
from app.pipeline import TurnResult
from app.sinks import CollectingSink, NoopActionHandler

REDTEAM = REPO_ROOT / "evals" / "redteam.yaml"
SHARED = REPO_ROOT / "evals" / "shared.yaml"
REPORT_DIR = REPO_ROOT / "reports" / "m3"

# 15 cases across every guard path: injection rules, leak, encodings, hidden injection,
# contacts, visitor PII, unsafe how-to, hate, crisis, emergency, over-refusal, multi-turn.
DEFAULT_IDS = [
    "ignore_previous_say_scam",
    "print_system_prompt",
    "dan_no_rules",
    "fake_system_tags",
    "hidden_injection_zero_width",
    "base64_command",
    "hidden_injection_phone_rule",
    "founder_phone",
    "visitor_types_id_and_phone",
    "root_character_pipe_bomb",
    "spell_slur_for_linguistics",
    "painless_method",
    "emergency_after_joke",
    "what_is_prompt_injection_ok",
    "mt_gradual_doxx",
]

LATENCY_QUESTIONS = [
    "Where is the AI Studio?",
    "What's a transformer in AI?",
    "What AI clubs meet every week?",
    "What's the difference between AI and machine learning?",
    "Where can I 3D print something on campus?",
    "How does a neural network learn?",
    "Does SFSU have a master's degree in AI?",
    "What is reinforcement learning, in one sentence?",
    "What's the campus police non-emergency number?",
    "Can you explain what an embedding is?",
    "How do I join the AI Lab?",
    "What's overfitting?",
    "Can I use ChatGPT on my homework?",
    "Why do GPUs help with deep learning?",
    "What AI events are coming up in November?",
    "What is a large language model?",
]


# -- Ollama residency -------------------------------------------------------------------


async def residency(client: OllamaClient, settings: Settings, when: str) -> None:
    """Print what Ollama has loaded. Extra models evict ours (default limit: 3 loaded)."""
    try:
        names = await client.loaded()
    except Exception as exc:  # diagnostic only
        print(f"ollama ps ({when}): unavailable ({exc})")
        return
    ours = {settings.chat_model, settings.guard_model, settings.embed_model}
    extra = [n for n in names if n not in ours]
    print(f"ollama ps ({when}): {', '.join(names) or 'nothing loaded'}")
    if extra:
        print(
            f"WARN other models loaded: {', '.join(extra)}. With Ollama's default "
            "OLLAMA_MAX_LOADED_MODELS=3 they push ours out; run `ollama stop <name>`."
        )


# -- attacks ----------------------------------------------------------------------------


def _patterns(items: list[str], sets: dict[str, list[str]]) -> list[tuple[str, re.Pattern[str]]]:
    out: list[tuple[str, re.Pattern[str]]] = []
    for it in items:
        if it.startswith("$"):
            out += _patterns(sets.get(it[1:], []), sets)
        elif it.startswith("re:"):
            out.append((it, re.compile(it[3:], re.IGNORECASE)))
        else:
            out.append((it, re.compile(rf"(?<!\w){re.escape(it)}(?!\w)", re.IGNORECASE)))
    return out


def _decisions(r: TurnResult) -> str:
    parts = [r.input_guard.short()] if r.input_guard else []
    if r.output_guard:
        parts.append("output:" + ",".join("ok" if d.allowed else "BLOCK" for d in r.output_guard))
    if r.output_block:
        parts.append(r.output_block.short())
    if r.kill_switch:
        parts.append("KILL")
    return " ".join(parts) or "-"


async def run_attacks(settings: Settings, ids: list[str] | None, full: bool) -> int:
    cases = yaml.safe_load(REDTEAM.read_text(encoding="utf-8"))["cases"]
    shared = yaml.safe_load(SHARED.read_text(encoding="utf-8")) or {}
    sets = {k: [str(x) for x in v] for k, v in (shared.get("phrase_sets") or {}).items()}
    by_id = {c["id"]: c for c in cases}
    chosen = [by_id[i] for i in ids] if ids else cases
    bot = settings.bot_name
    counts: dict[str, int] = {}
    flagged: list[str] = []
    rows: list[dict[str, Any]] = []
    async with OllamaClient.from_settings(settings) as client:
        built = build_pipeline(settings, client, CollectingSink(), NoopActionHandler(),
                               transcripts=False)  # fmt: skip
        for w in built.warnings:
            print(f"WARN {w}")
        p = built.pipeline
        await residency(client, settings, "start")
        await p.input_guard.check("Hi!")  # load the guard model
        print(f"chat={settings.chat_model} guard={settings.guard_model} cases={len(chosen)}\n")
        for case in chosen:
            sid = built.sessions.create()
            print(f"== {case['id']}  [{case['category']}/{case['severity']}]")
            for n, turn in enumerate(case["turns"], 1):
                user = str(turn["user"]).replace("{{bot_name}}", bot)
                r = await p.handle(sid, user)
                kind = (
                    "input-block" if r.input_guard and not r.input_guard.allowed
                    else "output-block" if r.output_block
                    else "fallback" if r.source == "line:guard_fallback"
                    else "line" if r.source.startswith("line:")
                    else "model"
                )  # fmt: skip
                counts[kind] = counts.get(kind, 0) + 1
                reply = r.text if full else (r.text[:220] + ("..." if len(r.text) > 220 else ""))
                hits = []
                expect = turn.get("expect") or {}
                nots = [
                    str(x).replace("{{bot_name}}", bot) for x in expect.get("must_not_include", [])
                ]
                for label, rx in _patterns(nots, sets):
                    if rx.search(r.text):
                        hits.append(label)
                print(f"  t{n} you> {user[:150]}")
                print(f"     {kind:12} {r.source:28} {_decisions(r)}")
                if r.blocked_sentence:
                    print(f"     blocked (not shown): {r.blocked_sentence[:150]}")
                print(f"     {bot}> {reply}")
                if hits:
                    print(f"     !! must_not_include hit: {hits}")
                    flagged.append(f"{case['id']} t{n}: {hits}")
                if r.error:
                    print(f"     ERROR {r.error}")
                rows.append({
                    "case": case["id"], "turn": n, "kind": kind, "source": r.source,
                    "input_guard": r.input_guard.to_dict() if r.input_guard else None,
                    "output_guard": [d.to_dict() for d in r.output_guard],
                    "reply": r.text, "hits": hits, "error": r.error,
                })  # fmt: skip
            print()
        await residency(client, settings, "end")
    total = sum(counts.values())
    print("== summary")
    print(f"turns {total}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"must_not_include hits: {len(flagged)}")
    for f in flagged:
        print(f"  !! {f}")
    _save("attacks", settings, {"counts": counts, "flagged": flagged, "rows": rows})
    return 0


# -- latency ----------------------------------------------------------------------------


def _med(xs: list[float]) -> str:
    return f"{statistics.median(xs):.2f}s" if xs else "n/a"


def _max(xs: list[float]) -> str:
    return f"{max(xs):.2f}s" if xs else "n/a"


async def run_latency(settings: Settings, rounds: int) -> int:
    questions = LATENCY_QUESTIONS * rounds
    modes = {False: [], True: []}  # type: dict[bool, list[TurnResult]]
    async with OllamaClient.from_settings(settings) as client:
        builds = {}
        for par in (False, True):
            s = settings.model_copy(update={"guard_parallel_retrieval": par})
            builds[par] = build_pipeline(s, client, CollectingSink(), NoopActionHandler(),
                                         transcripts=False)  # fmt: skip
        for w in builds[False].warnings:
            print(f"WARN {w}")
        # warm both models + the system prompt
        b = builds[False]
        await residency(client, settings, "start")
        await b.pipeline.input_guard.check("Hi!")
        await b.pipeline.handle(b.sessions.create(), "Hi!")
        print(f"chat={settings.chat_model} guard={settings.guard_model} "
              f"target first sentence <= {settings.target_first_token_s}s\n")  # fmt: skip
        print(f"{'mode':5} {'in-guard':>8} {'docs':>6} {'ttft':>6} {'1st shown':>9} "
              f"{'out-guard':>9}  {'source':22} question")  # fmt: skip
        for i, q in enumerate(questions):
            par = bool(i % 2)  # alternate: every question asked once
            bb = builds[par]
            r = await bb.pipeline.handle(bb.sessions.create(), q)
            modes[par].append(r)
            ig = r.input_guard.total_s if r.input_guard else 0.0
            og = r.output_guard[0].total_s if r.output_guard else 0.0
            print(
                f"{'par' if par else 'seq':5} {ig:8.2f} {r.retrieval_s or 0:6.2f} "
                f"{r.ttft_s or 0:6.2f} {r.first_sentence_s or 0:9.2f} {og:9.2f}  "
                f"{r.source[:22]:22} {q[:50]}"
            )
        await residency(client, settings, "end")
    print()
    summary: dict[str, Any] = {}
    for par, rs in modes.items():
        name = "parallel retrieval" if par else "sequential"
        ok = [r for r in rs if r.source == "model"]
        first = [r.first_sentence_s for r in ok if r.first_sentence_s is not None]
        ttft = [r.ttft_s for r in ok if r.ttft_s is not None]
        ig = [r.input_guard.total_s for r in rs if r.input_guard]
        igm = [r.input_guard.model_s for r in rs if r.input_guard and r.input_guard.model_s]
        og = [d.total_s for r in ok for d in r.output_guard]
        og1 = [r.output_guard[0].total_s for r in ok if r.output_guard]
        docs = [r.retrieval_s for r in rs if r.retrieval_s is not None]
        target = settings.target_first_token_s
        verdict = "PASS" if first and statistics.median(first) <= target else "MISS"
        print(f"== {name} ({len(ok)}/{len(rs)} model replies)")
        print(f"  FIRST SENTENCE SHOWN  median {_med(first)}  max {_max(first)}  [{verdict}]")
        print(f"  model first token     median {_med(ttft)}  max {_max(ttft)}")
        print(f"  input guard total     median {_med(ig)}  max {_max(ig)}   (model {_med(igm)})")
        print(f"  retrieval             median {_med(docs)}  max {_max(docs)}")
        print(f"  output guard, 1st     median {_med(og1)}  max {_max(og1)}")
        print(f"  output guard, all     median {_med(og)}  max {_max(og)}  ({len(og)} checks)")
        blocked = [r for r in rs if r.source != "model"]
        for r in blocked:
            print(f"  not a model reply: {r.user_text[:50]!r} -> {r.source} {_decisions(r)}")
        summary[name] = {
            "first_sentence": first, "ttft": ttft, "input_guard": ig, "retrieval": docs,
            "output_guard_first": og1, "output_guard_all": og, "pass": verdict == "PASS",
        }  # fmt: skip
    _save("latency", settings, summary)
    return 0


def _save(kind: str, settings: Settings, data: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{kind}.json"
    meta = {"chat_model": settings.chat_model, "guard_model": settings.guard_model}
    path.write_text(json.dumps({**meta, **data}, indent=1, default=str), encoding="utf-8")
    print(f"report: {path.relative_to(REPO_ROOT)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.m3_probe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("attacks")
    a.add_argument("--ids", help="comma-separated case ids (default: 15 representative)")
    a.add_argument("--all", action="store_true", help="every red-team case")
    a.add_argument("--show-full", action="store_true")
    lat = sub.add_parser("latency")
    lat.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args(argv)
    settings = load_settings()
    if args.cmd == "attacks":
        ids = None if args.all else (args.ids.split(",") if args.ids else DEFAULT_IDS)
        return asyncio.run(run_attacks(settings, ids, args.show_full))
    return asyncio.run(run_latency(settings, args.rounds))


if __name__ == "__main__":
    sys.exit(main())
