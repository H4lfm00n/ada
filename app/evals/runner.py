"""Drive every case through the real, fully guarded pipeline, then judge, then report.

Phase 1 (generate): for each suite, a pipeline built by `app.factory.build_pipeline` with
`lab_docs_dir` set to the suite's corpus (fixtures or real docs, never mixed; D44) and
transcripts off. Each case (and each --repeat attempt) gets a fresh session; turns replay
in order: `user` -> pipeline.handle, a `user` followed by `assistant:` is seeded into
history (no model call), `control: reset` -> pipeline.goodbye, `control: timeout` ->
pipeline.timed_out + a new session, exactly like `make chat`. Rules score each turn.

Phase 2 (judge): only after every reply exists. The chat model is unloaded first, so the
judge is the third resident model next to guard + embed and never evicts them (D45).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.evals.judge import Judge, JudgeVerdict
from app.evals.scorer import Observation, TurnVerdict, question_ratio_failure, score_turn
from app.evals.spec import Case, Shared, Suite
from app.factory import Built, build_pipeline, warm_up
from app.interfaces import Message
from app.lines import GUARD_FALLBACK
from app.ollama_client import OllamaClient, normalize_tag
from app.pipeline import TurnResult
from app.rag.retriever import OllamaRetriever, build_index
from app.rag.store import RagIndexError
from app.sinks import CollectingSink, NoopActionHandler

NEVER = 1e12  # session timeout during evals: only `control: timeout` ends a session


class SetupError(RuntimeError):
    """Something outside the cases is wrong (model missing, kill switch on, no index)."""


@dataclass(slots=True)
class TurnRecord:
    n: int
    kind: str  # "user" | "control"
    user: str
    reply: str
    source: str
    guard: str
    verdict: TurnVerdict
    history: list[tuple[str, str]]  # conversation before this turn (for the judge)
    docs: list[str] = field(default_factory=list)  # chunk texts given to the model
    retrieved: list[str] = field(default_factory=list)  # "file > heading (score)"
    latency: dict[str, float | None] = field(default_factory=dict)
    blocked_sentence: str | None = None  # model text the output guard stopped (not shown)
    infra: str | None = None
    judge: JudgeVerdict | None = None

    @property
    def failures(self) -> list[str]:
        out = list(self.verdict.failures)
        if self.judge is not None and not self.judge.passed:
            out += self.judge.reasons
        return out

    @property
    def pending(self) -> bool:
        return bool(self.verdict.needs_judge) and self.judge is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.n,
            "kind": self.kind,
            "user": self.user,
            "reply": self.reply,
            "source": self.source,
            "guard": self.guard,
            "failures": self.failures,
            "judge_criteria": self.verdict.needs_judge,
            "judge": None
            if self.judge is None
            else {
                "passed": self.judge.passed,
                "reasons": self.judge.reasons,
                "raw": self.judge.raw,
            },
            "caught_by_output_guard": self.verdict.caught_by_output_guard,
            "infra": self.infra,
            "blocked_sentence": self.blocked_sentence,
            "retrieved": self.retrieved,
            "latency": self.latency,
        }


@dataclass(slots=True)
class CaseRun:
    case: Case
    attempt: int
    turns: list[TurnRecord] = field(default_factory=list)
    case_failures: list[str] = field(default_factory=list)
    total_s: float = 0.0

    @property
    def failures(self) -> list[tuple[int | None, str]]:
        out: list[tuple[int | None, str]] = [(None, f) for f in self.case_failures]
        for t in self.turns:
            out += [(t.n, f) for f in t.failures]
        return out

    @property
    def method(self) -> str:
        return "judge" if any(t.verdict.needs_judge for t in self.turns) else "rules"

    @property
    def status(self) -> str:
        if self.failures:
            return "fail"
        if any(t.pending for t in self.turns):
            return "unjudged"
        return "pass"

    @property
    def passed(self) -> bool:
        """Unjudged counts as a provisional pass (only with --no-judge)."""
        return self.status != "fail"

    @property
    def first_failing_turn(self) -> TurnRecord | None:
        bad = [t for t in self.turns if t.failures]
        return bad[0] if bad else (self.turns[-1] if self.turns and self.case_failures else None)

    def latency_summary(self) -> dict[str, float | None]:
        fs = [t.latency.get("first_sentence_s") for t in self.turns if t.kind == "user"]
        fs = [x for x in fs if x is not None]
        ttft = [t.latency.get("ttft_s") for t in self.turns]
        ttft = [x for x in ttft if x is not None]
        return {
            "total_s": round(self.total_s, 3),
            "first_sentence_max_s": round(max(fs), 3) if fs else None,
            "ttft_max_s": round(max(ttft), 3) if ttft else None,
        }

    def to_dict(self) -> dict[str, Any]:
        c = self.case
        return {
            "suite": c.suite,
            "id": c.id,
            "category": c.category,
            "severity": c.severity,
            "attempt": self.attempt,
            "status": self.status,
            "method": self.method,
            "case_failures": self.case_failures,
            "latency": self.latency_summary(),
            "turns": [t.to_dict() for t in self.turns],
        }


# -- TurnResult -> Observation --------------------------------------------------------


def guard_summary(r: TurnResult) -> str:
    parts = []
    if r.kill_switch:
        parts.append("KILL SWITCH")
    if r.input_guard is not None and not r.input_guard.allowed:
        parts.append(r.input_guard.short())
    if r.output_block is not None:
        parts.append(r.output_block.short())
    return " ".join(parts) or "none fired"


def observe(r: TurnResult) -> Observation:
    ig, ob = r.input_guard, r.output_block
    input_blocked = ig is not None and not ig.allowed and ig.stage in ("rules", "model")
    output_caught = ob is not None and ob.stage in ("rules", "model")
    infra = None
    if r.kill_switch:
        infra = "kill switch on"
    elif ig is not None and not ig.allowed and ig.stage not in ("rules", "model"):
        infra = f"input guard {ig.stage}: {ig.reason or ''}".strip()
    elif ob is not None and not output_caught:
        infra = f"output guard {ob.stage}: {ob.reason or ''}".strip()
    elif r.error:
        infra = f"chat model: {r.error}"
    elif r.source == f"line:{GUARD_FALLBACK}" and not input_blocked and not output_caught:
        infra = "chat model gave an empty reply"
    return Observation(
        text=r.text,
        sentences=list(r.sentences),
        source=r.source,
        guard=guard_summary(r),
        input_blocked=input_blocked,
        output_caught=output_caught,
        infra=infra,
    )


def latency_of(r: TurnResult) -> dict[str, float | None]:
    def rd(x: float | None) -> float | None:
        return None if x is None else round(x, 3)

    return {
        "ttft_s": rd(r.ttft_s),
        "first_sentence_s": rd(r.first_sentence_s),
        "total_s": rd(r.total_s),
        "retrieval_s": rd(r.retrieval_s),
        "input_guard_s": rd(r.input_guard.total_s if r.input_guard else None),
    }


# -- setup ----------------------------------------------------------------------------


def suite_settings(settings: Settings, suite: Suite) -> Settings:
    return settings.model_copy(
        update={"lab_docs_dir": suite.lab_docs_dir, "transcripts_enabled": False}
    )


async def ensure_index(client: OllamaClient, settings: Settings, log: Any = print) -> None:
    """Re-ingest the suite's corpus if its index is missing, stale, or from another model."""
    why = ""
    try:
        _, problems = OllamaRetriever.open(client, settings)
        why = "; ".join(problems)
    except (RagIndexError, FileNotFoundError) as exc:
        why = str(exc) or "no index"
    if why:
        log(f"  ingest {settings.lab_docs_dir} ({why})")
        rep = await build_index(client, settings)
        log(f"  ingested {rep.files} files, {rep.chunks} sections")


def build_suite_pipeline(client: OllamaClient, settings: Settings) -> Built:
    built = build_pipeline(
        settings, client, CollectingSink(), NoopActionHandler(), transcripts=False
    )
    if built.kill_switch.is_on():
        raise SetupError("kill switch is ON; run `make kill-off` before `make eval`")
    off = [w for w in built.warnings if w.startswith("lab docs OFF")]
    if off:
        raise SetupError(f"{settings.lab_docs_dir}: {off[0]}")
    built.sessions.timeout_s = NEVER
    return built


async def check_models(client: OllamaClient, settings: Settings, use_judge: bool) -> None:
    installed = {normalize_tag(t) for t in await client.list_models()}
    need = dict(settings.model_tags)
    if use_judge:
        need["judge"] = settings.judge_model
    missing = [f"{role}={tag}" for role, tag in need.items() if normalize_tag(tag) not in installed]
    if missing:
        hint = " (or run with --no-judge)" if any(m.startswith("judge=") for m in missing) else ""
        raise SetupError(f"models not installed: {', '.join(missing)}; `ollama pull` them{hint}")


# -- one case -------------------------------------------------------------------------


async def run_case(built: Built, case: Case, shared: Shared, attempt: int) -> CaseRun:
    p, sessions = built.pipeline, built.sessions
    run = CaseRun(case, attempt)
    t0 = time.perf_counter()
    sid = sessions.create()
    history: list[tuple[str, str]] = []
    turns = case.turns
    i = 0
    while i < len(turns):
        turn = turns[i]
        if turn.seeded:
            seeded_reply = turns[i + 1].text
            sessions.append(sid, Message("user", turn.text), Message("assistant", seeded_reply))
            if (s := sessions.session(sid)) is not None:
                s.turns += 1
            history += [("user", turn.text), ("assistant", seeded_reply)]
            i += 2
            continue
        before = list(history)
        if turn.kind == "user":
            r = await p.handle(sid, turn.text)
        elif turn.text == "reset":
            r = await p.goodbye(sid)
        else:  # timeout: what `make chat` does when the idle timer fires
            sessions.end(sid)
            r = await p.timed_out(sid)
            sid = sessions.create()
        obs = observe(r)
        verdict = score_turn(case, turn, obs, shared)
        run.turns.append(
            TurnRecord(
                n=turn.n,
                kind=turn.kind,
                user=turn.text if turn.kind == "user" else f"[control: {turn.text}]",
                reply=r.text,
                source=r.source,
                guard=obs.guard,
                verdict=verdict,
                history=before,
                docs=[c.text for c in r.retrieved],
                retrieved=[f"{c.source_file} > {c.heading} ({c.score:.2f})" for c in r.retrieved],
                latency=latency_of(r),
                blocked_sentence=r.blocked_sentence,
                infra=obs.infra,
            )
        )
        if r.session_reset:
            history = []
        elif turn.kind == "user":
            history += [("user", turn.text), ("assistant", r.text)]
        i += 1
    model_replies = [t.reply for t in run.turns if t.kind == "user" and t.source == "model"]
    if f := question_ratio_failure(case, model_replies):
        run.case_failures.append(f)
    sessions.end(sid)
    run.total_s = time.perf_counter() - t0
    return run


# -- whole run -------------------------------------------------------------------------


@dataclass(slots=True)
class RunOutput:
    runs: list[CaseRun]
    suites: list[Suite]
    repeat: int
    judge_used: bool
    judge_calls: int = 0
    gen_s: float = 0.0
    judge_s: float = 0.0
    warm: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


async def generate(
    client: OllamaClient,
    settings: Settings,
    suites: list[Suite],
    shared: Shared,
    repeat: int,
    log: Any = print,
) -> tuple[list[CaseRun], list[str]]:
    runs: list[CaseRun] = []
    warnings: list[str] = []
    for suite in suites:
        s = suite_settings(settings, suite)
        log(f"\n== {suite.name}: {len(suite.cases)} cases x {repeat}, docs={suite.lab_docs_dir}")
        await ensure_index(client, s, log)
        built = build_suite_pipeline(client, s)
        for w in built.warnings:
            if "INVENTED eval facts" not in w:
                warnings.append(f"{suite.name}: {w}")
                log(f"  WARN {w}")
        total = len(suite.cases) * repeat
        k = 0
        for attempt in range(1, repeat + 1):
            for case in suite.cases:
                k += 1
                run = await run_case(built, case, shared, attempt)
                runs.append(run)
                st = {"pass": "pass", "fail": "FAIL", "unjudged": "judge?"}[run.status]
                lat = run.latency_summary()["first_sentence_max_s"]
                log(f"  [{k:>3}/{total}] {case.id:<44} {st:<7} {lat if lat is not None else '-'}s")
    return runs, warnings


async def judge_pending(
    client: OllamaClient, settings: Settings, shared: Shared, runs: list[CaseRun], log: Any = print
) -> int:
    pending = [(r, t) for r in runs for t in r.turns if t.pending]
    if not pending:
        return 0
    log(f"\n== judge: {len(pending)} replies with {settings.judge_model} (local)")
    try:  # free the chat model's slot first: guard + embed + judge = 3 resident (D45)
        await client.unload(settings.chat_model)
    except Exception as exc:  # diagnostic only; loading the judge may still work
        log(f"  WARN could not unload {settings.chat_model}: {exc}")
    judge = Judge(client, settings, shared.judge_rubric)
    await judge.load()
    for k, (run, t) in enumerate(pending, 1):
        t.judge = await judge.grade(t.history, t.user, t.reply, t.docs, t.verdict.needs_judge)
        mark = "pass" if t.judge.passed else "FAIL"
        log(f"  [{k:>3}/{len(pending)}] {run.case.id} t{t.n}: {mark}")
    try:  # don't leave a 4th model resident for the next `make chat` (D40)
        await client.unload(settings.judge_model)
    except Exception as exc:
        log(f"  WARN could not unload {settings.judge_model}: {exc}")
    return judge.calls


async def run_evals(
    client: OllamaClient,
    settings: Settings,
    suites: list[Suite],
    shared: Shared,
    *,
    repeat: int = 1,
    use_judge: bool = True,
    warm: bool = True,
    log: Any = print,
) -> RunOutput:
    await check_models(client, settings, use_judge)
    out = RunOutput([], suites, repeat, use_judge)
    if warm:
        out.warm = await warm_up(settings, client)
        log("loaded: " + ", ".join(f"{k} {v:.1f}s" for k, v in out.warm.items()))
    t0 = time.perf_counter()
    out.runs, out.warnings = await generate(client, settings, suites, shared, repeat, log)
    out.gen_s = time.perf_counter() - t0
    if use_judge:
        t1 = time.perf_counter()
        out.judge_calls = await judge_pending(client, settings, shared, out.runs, log)
        out.judge_s = time.perf_counter() - t1
    return out
