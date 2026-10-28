"""`make chat`: talk to the assistant in the terminal, through the full M1 pipeline.

Usage:
  python -m app.chat                      interactive
  python -m app.chat --script FILE        run a scripted conversation, print a latency report
Options:
  --no-warmup   skip the warmup request (measures a cold model / cold prompt cache)
  --quiet       hide the per-reply timing line (interactive mode)

Interactive commands: /reset (new visitor), /stats (toggle timing line), /quit.
Replies appear sentence by sentence, the same unit the kiosk and TTS will get.
A goodbye prints by itself if you stay idle past session_timeout_s.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.config import REPO_ROOT, Settings, load_settings
from app.lines import IDLE_GREETINGS, LineBook
from app.ollama_client import OllamaChatModel, OllamaClient, OllamaError, normalize_tag
from app.pipeline import ChatPipeline, TurnResult
from app.prompt import PromptBuilder
from app.sessions import InMemorySessionManager
from app.sinks import ConsoleSink, NoopActionHandler

DEMO_SCRIPT = REPO_ROOT / "evals" / "chat_demo.yaml"
REPORT_DIR = REPO_ROOT / "reports" / "chat"
_TTY = sys.stdout.isatty()
DIM, RESET = ("\033[2m", "\033[0m") if _TTY else ("", "")


@dataclass(slots=True)
class App:
    settings: Settings
    pipeline: ChatPipeline
    sessions: InMemorySessionManager
    sink: ConsoleSink
    actions: NoopActionHandler
    model: OllamaChatModel


def build_app(settings: Settings, client: OllamaClient) -> App:
    name = settings.bot_name
    sink = ConsoleSink(prefix=f"{name}> ")
    sessions = InMemorySessionManager.from_settings(settings)
    actions = NoopActionHandler()
    model = OllamaChatModel(client, settings)
    pipeline = ChatPipeline(
        model=model,
        prompt=PromptBuilder.from_settings(settings),
        lines=LineBook.from_settings(settings),
        sessions=sessions,
        sink=sink,
        actions=actions,
        reply_timeout_s=settings.request_timeout_s,
    )
    return App(settings, pipeline, sessions, sink, actions, model)


def timing_line(r: TurnResult) -> str:
    parts = []
    if r.source != "model":
        parts.append(r.source)
    if r.ttft_s is not None:
        parts.append(f"first token {r.ttft_s:.2f}s")
    if r.first_sentence_s is not None:
        parts.append(f"first sentence {r.first_sentence_s:.2f}s")
    parts.append(f"total {r.total_s:.2f}s")
    if r.stats and r.source == "model":
        s = r.stats
        parts.append(f"{s.eval_count} tok @ {s.decode_tok_s:.1f} tok/s")
        parts.append(f"prompt {s.prompt_eval_count} tok in {s.prompt_eval_duration / 1e9:.2f}s")
    if r.error:
        parts.append(f"ERROR {r.error}")
    return "  [" + " · ".join(parts) + "]"


def summarize(results: list[TurnResult], settings: Settings) -> dict[str, Any]:
    model_turns = [r for r in results if r.source == "model" and r.ttft_s is not None]
    ttft = [r.ttft_s for r in model_turns if r.ttft_s is not None]
    tfs = [r.first_sentence_s for r in model_turns if r.first_sentence_s is not None]
    rates = [r.stats.decode_tok_s for r in model_turns if r.stats and r.stats.decode_tok_s]
    prompt_toks = [r.stats.prompt_eval_count for r in model_turns if r.stats]

    def med(xs: list[float]) -> float | None:
        return statistics.median(xs) if xs else None

    return {
        "model_turns": len(model_turns),
        "errors": sum(1 for r in results if r.error),
        "ttft_median_s": med(ttft),
        "ttft_max_s": max(ttft) if ttft else None,
        "first_sentence_median_s": med(tfs),
        "first_sentence_max_s": max(tfs) if tfs else None,
        "decode_tok_s_median": med(rates),
        "prompt_tokens_max": max(prompt_toks) if prompt_toks else None,
        "target_first_token_s": settings.target_first_token_s,
        "meets_ttft_target": bool(ttft) and med(ttft) <= settings.target_first_token_s,
    }


def print_summary(summary: dict[str, Any], thinking_chars: int) -> None:
    def f(v: float | None, unit: str = "s") -> str:
        return "n/a" if v is None else f"{v:.2f}{unit}"

    print("\n--- latency over model replies ---")
    print(f"model replies: {summary['model_turns']}   errors: {summary['errors']}")
    verdict = "PASS" if summary["meets_ttft_target"] else "MISS"
    print(
        f"first token    median {f(summary['ttft_median_s'])}  max {f(summary['ttft_max_s'])}"
        f"  target <= {summary['target_first_token_s']}s  [{verdict}]"
    )
    print(
        f"first sentence median {f(summary['first_sentence_median_s'])}"
        f"  max {f(summary['first_sentence_max_s'])}"
    )
    rate = summary["decode_tok_s_median"]
    print(f"decode         median {'n/a' if rate is None else f'{rate:.1f} tok/s'}")
    print(f"largest prompt {summary['prompt_tokens_max']} tokens")
    print(f"thinking: {'LEAKED (' + str(thinking_chars) + ' chars)' if thinking_chars else 'off'}")


async def check_model(app: App, client: OllamaClient) -> None:
    installed = {normalize_tag(t) for t in await client.list_models()}
    if normalize_tag(app.settings.chat_model) not in installed:
        raise OllamaError(f"chat model {app.settings.chat_model} is not installed")


async def warmup(app: App) -> float:
    """Load the model and prime Ollama's prompt cache with the system prompt."""
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    messages = app.pipeline.prompt.build([], "Hi!")
    async for _ in app.model.stream(messages):
        pass
    return loop.time() - t0


# -- scripted mode ---------------------------------------------------------------------


def load_script(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    steps = data.get("steps")
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        raise ValueError(f"{path}: 'steps' must be a list of strings")
    return steps


async def run_script(
    settings: Settings,
    script: Path,
    *,
    do_warmup: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
    write_report: bool = True,
) -> int:
    steps = load_script(script)
    async with OllamaClient.from_settings(settings, transport) as client:
        app = build_app(settings, client)
        try:
            await check_model(app, client)
            print(f"chat={settings.chat_model} | think={settings.think} | bot={settings.bot_name}")
            if do_warmup:
                print(f"warmup (model load + prompt cache): {await warmup(app):.2f}s")
        except OllamaError as exc:
            print(f"CHAT ERROR: {exc}", file=sys.stderr)
            return 1

        sid = app.sessions.create()
        results: list[TurnResult] = []
        thinking = 0
        for step in steps:
            print(f"\nyou> {step}")
            if step.strip() == "/reset":
                r = await app.pipeline.goodbye(sid)
            else:
                r = await app.pipeline.handle(sid, step)
                thinking += app.model.last_thinking_chars if r.source == "model" else 0
            app.sink.end_reply(sid)
            if r.session_reset:
                print(f"{DIM}  (session reset: context cleared){RESET}")
            print(f"{DIM}{timing_line(r)}{RESET}")
            results.append(r)

    summary = summarize(results, settings)
    print_summary(summary, thinking)
    print(f"actions sent to ActionHandler: {[a for _, a in app.actions.actions]}")
    if write_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        path = REPORT_DIR / f"chat-{stamp}.json"
        report = {
            "timestamp": stamp,
            "chat_model": settings.chat_model,
            "summary": summary,
            "turns": [
                {
                    "user": r.user_text,
                    "reply": r.text,
                    "sentences": r.sentences,
                    "source": r.source,
                    "ttft_s": r.ttft_s,
                    "first_sentence_s": r.first_sentence_s,
                    "total_s": r.total_s,
                    "error": r.error,
                }
                for r in results
            ],
        }
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"report: {path}")
    return 1 if summary["errors"] or thinking else 0


# -- interactive mode ------------------------------------------------------------------


def _start_reader(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str | None]) -> None:
    """Read stdin on a daemon thread so the event loop can notice idle timeouts."""

    def read() -> None:
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                loop.call_soon_threadsafe(queue.put_nowait, None)
                return
            loop.call_soon_threadsafe(queue.put_nowait, line)

    threading.Thread(target=read, daemon=True, name="stdin-reader").start()


async def run_interactive(settings: Settings, *, do_warmup: bool, show_stats: bool) -> int:
    async with OllamaClient.from_settings(settings) as client:
        app = build_app(settings, client)
        try:
            await check_model(app, client)
            if do_warmup:
                print(f"{DIM}warming up {settings.chat_model}...{RESET}", flush=True)
                print(f"{DIM}ready in {await warmup(app):.1f}s{RESET}")
        except OllamaError as exc:
            print(f"CHAT ERROR: {exc}", file=sys.stderr)
            return 1

        print(
            f"{DIM}{settings.bot_name} | {settings.chat_model} | session timeout"
            f" {settings.session_timeout_s:.0f}s | /reset /stats /quit{RESET}"
        )
        lines = app.pipeline.lines
        idle_recent: dict[str, str] = {}  # kiosk-level, so greetings don't repeat
        print(f"\n{settings.bot_name}> {lines.pick(IDLE_GREETINGS, idle_recent)}")

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        _start_reader(loop, queue)
        sid = app.sessions.create()
        results: list[TurnResult] = []
        thinking = 0
        print("you> ", end="", flush=True)

        while True:
            try:
                line = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                session = app.sessions.peek(sid)
                if session and session.turns and app.sessions.is_expired(sid):
                    print()
                    app.sessions.end(sid)
                    await app.pipeline.timed_out(sid)
                    app.sink.end_reply(sid)
                    print(f"{DIM}  (idle timeout: context cleared){RESET}")
                    sid = app.sessions.create()
                    print(f"\n{settings.bot_name}> {lines.pick(IDLE_GREETINGS, idle_recent)}")
                    print("you> ", end="", flush=True)
                continue

            if line is None or line.strip() in {"/quit", "/exit"}:
                print()
                break
            cmd = line.strip()
            if not cmd:
                print("you> ", end="", flush=True)
                continue
            if cmd == "/stats":
                show_stats = not show_stats
                print(f"{DIM}timing line {'on' if show_stats else 'off'}{RESET}")
                print("you> ", end="", flush=True)
                continue

            if app.sessions.session(sid) is None:  # expired between checks
                app.sessions.end(sid)
                sid = app.sessions.create()
            if cmd == "/reset":
                r = await app.pipeline.goodbye(sid)
            else:
                r = await app.pipeline.handle(sid, line)
                thinking += app.model.last_thinking_chars if r.source == "model" else 0
            app.sink.end_reply(sid)
            if r.session_reset:
                print(f"{DIM}  (session reset: context cleared){RESET}")
            if show_stats:
                print(f"{DIM}{timing_line(r)}{RESET}")
            results.append(r)
            print("you> ", end="", flush=True)

    if any(r.source == "model" for r in results):
        print_summary(summarize(results, settings), thinking)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--script", type=Path, help="run a scripted conversation (YAML)")
    parser.add_argument("--demo", action="store_true", help=f"run {DEMO_SCRIPT.name}")
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="hide per-reply timing")
    args = parser.parse_args()
    settings = load_settings()
    script = DEMO_SCRIPT if args.demo else args.script
    try:
        if script:
            code = asyncio.run(run_script(settings, script, do_warmup=not args.no_warmup))
        else:
            code = asyncio.run(
                run_interactive(settings, do_warmup=not args.no_warmup, show_stats=not args.quiet)
            )
    except KeyboardInterrupt:
        print()
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
