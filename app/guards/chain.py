"""Rules first, then the guard model, with a timeout. Every failure blocks (fail safe)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

from app.guards.policy import GuardPolicy, Side
from app.interfaces import Guard, Message, Role

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GuardDecision:
    """What a guard chain decided about one piece of text. Goes into the transcript."""

    side: Side
    allowed: bool
    stage: str  # "rules" | "model" | "error" | "timeout" | "pass"
    code: str | None = None  # rule code ("injection") or hazard code ("S11")
    line: str | None = None  # lines.yaml category shown instead, when blocked
    reason: str | None = None
    rules_s: float = 0.0
    model_s: float | None = None

    @property
    def total_s(self) -> float:
        return self.rules_s + (self.model_s or 0.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def short(self) -> str:
        if self.allowed:
            return f"{self.side}:ok({self.total_s * 1000:.0f}ms)"
        return f"{self.side}:BLOCK[{self.stage}:{self.code}->{self.line}]"


class GuardChain:
    """One side of the guard: rule guard, then model guard. Never raises."""

    def __init__(
        self,
        side: Side,
        rules: Guard,
        model: Guard,
        policy: GuardPolicy,
        timeout_s: float,
        clock: Any = time.perf_counter,
    ) -> None:
        self.side = side
        self.rules = rules
        self.model = model
        self.policy = policy
        self.timeout_s = timeout_s
        self._clock = clock

    @property
    def role(self) -> Role:
        return "user" if self.side == "input" else "assistant"

    def _blocked(self, stage: str, code: str, reason: str | None, **t: Any) -> GuardDecision:
        line = self.policy.line_for(self.side, code if stage in ("rules", "model") else stage)
        return GuardDecision(self.side, False, stage, code, line, reason, **t)

    async def check(self, text: str, context: list[Message] | None = None) -> GuardDecision:
        t0 = self._clock()
        try:
            r = await self.rules.check(text, self.role, context=context)
        except Exception as exc:  # a broken rule must not let text through
            log.exception("%s rules failed", self.side)
            return self._blocked("error", "error", f"rules: {type(exc).__name__}: {exc}")
        rules_s = self._clock() - t0
        if not r.allowed:
            return self._blocked("rules", r.category or "unknown", r.reason, rules_s=rules_s)

        t1 = self._clock()
        try:
            async with asyncio.timeout(self.timeout_s):
                m = await self.model.check(text, self.role, context=context)
        except TimeoutError:
            log.warning("%s guard model timed out after %.1fs", self.side, self.timeout_s)
            return self._blocked(
                "timeout", "timeout", f"no answer in {self.timeout_s}s",
                rules_s=rules_s, model_s=self._clock() - t1,
            )  # fmt: skip
        except Exception as exc:
            log.warning("%s guard model failed: %s", self.side, exc)
            return self._blocked(
                "error", "error", f"{type(exc).__name__}: {exc}",
                rules_s=rules_s, model_s=self._clock() - t1,
            )  # fmt: skip
        model_s = self._clock() - t1
        if not m.allowed:
            codes = [c for c in (m.category or "unknown").split(",") if c]
            code = self.policy.pick_code(codes)
            return self._blocked("model", code, m.reason, rules_s=rules_s, model_s=model_s)
        return GuardDecision(self.side, True, "pass", rules_s=rules_s, model_s=model_s)
