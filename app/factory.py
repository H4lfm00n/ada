"""Build the real pipeline once, the same way for every entry point.

`make chat`, `make chat-demo`, `make rag-diagnose`, the M3 probe, and (M5) the kiosk
server all call `build_pipeline`, so none of them can end up without guards, the kill
switch, or transcripts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.config import Settings
from app.guards import Guards, build_guards
from app.interfaces import ActionHandler, OutputSink
from app.killswitch import KillSwitch
from app.lines import LineBook
from app.ollama_client import OllamaChatModel, OllamaClient
from app.pipeline import ChatPipeline
from app.prompt import PromptBuilder
from app.rag.retriever import open_retriever
from app.sessions import InMemorySessionManager
from app.transcripts import TranscriptLogger, make_redactor


@dataclass(slots=True)
class Built:
    pipeline: ChatPipeline
    sessions: InMemorySessionManager
    model: OllamaChatModel
    guards: Guards
    kill_switch: KillSwitch
    transcripts: TranscriptLogger | None
    warnings: list[str] = field(default_factory=list)


def build_transcripts(settings: Settings, guards: Guards) -> TranscriptLogger:
    return TranscriptLogger(
        settings.content_path(settings.transcripts_dir),
        settings.transcript_retention_days,
        redact=make_redactor(guards.allowlist, guards.policy.student_id_re),
    )


def build_pipeline(
    settings: Settings,
    client: OllamaClient,
    sink: OutputSink,
    actions: ActionHandler,
    *,
    transcripts: bool | None = None,
) -> Built:
    """The full pipeline. `transcripts=None` follows config `transcripts_enabled`."""
    lines = LineBook.from_settings(settings)
    sessions = InMemorySessionManager.from_settings(settings)
    model = OllamaChatModel(client, settings)
    retriever, warnings = open_retriever(client, settings)
    guards = build_guards(settings, client, lines)
    warnings = [*warnings, *guards.warnings]
    kill = KillSwitch.from_settings(settings)
    logger = None
    if settings.transcripts_enabled if transcripts is None else transcripts:
        logger = build_transcripts(settings, guards)
        logger.prune()
    pipeline = ChatPipeline(
        model=model,
        prompt=PromptBuilder.from_settings(settings),
        lines=lines,
        sessions=sessions,
        sink=sink,
        actions=actions,
        input_guard=guards.input,
        output_guard=guards.output,
        kill_switch=kill,
        transcripts=logger,
        parallel_retrieval=settings.guard_parallel_retrieval,
        reply_timeout_s=settings.request_timeout_s,
        retriever=retriever,
        rag_top_k=settings.rag_top_k,
        retrieval_timeout_s=settings.rag_timeout_s,
    )
    if kill.is_on():
        st = kill.state()
        warnings.append(f"KILL SWITCH IS ON ({st.source}): {st.reason or 'no reason given'}")
    return Built(pipeline, sessions, model, guards, kill, logger, warnings)


async def warm_up(settings: Settings, client: OllamaClient) -> dict[str, float]:
    """Load guard, embed, and chat models before the first visitor. Seconds per model.

    No guard timeout applies here: a cold Llama Guard load takes longer than
    `guard_timeout_s`, and a guard call that times out mid-load would leave it unloaded.
    Raises OllamaError if a model can't be loaded (the caller decides what to do).
    """
    times: dict[str, float] = {}
    for role, model in (("guard", settings.guard_model), ("chat", settings.chat_model)):
        t0 = time.perf_counter()
        await client.load(model, settings.keep_alive)
        times[role] = time.perf_counter() - t0
    t0 = time.perf_counter()
    await client.embed(settings.embed_model, ["warm up"], keep_alive=settings.keep_alive)
    times["embed"] = time.perf_counter() - t0
    return times
