"""Test doubles for M3: a scriptable guard model, plus real GuardChains around it.

Tests use the REAL rules (content/guard_policy.yaml) and the real GuardChain; only the
guard model is faked. There is deliberately no allow-everything guard in app code.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.guards import GuardChain
from app.guards.contacts import ContactAllowlist
from app.guards.policy import GuardPolicy
from app.guards.rules import InputRules, OutputRules
from app.interfaces import GuardResult, Message, Role
from app.killswitch import KillSwitch
from app.lines import LineBook

Verdict = GuardResult | Exception | Callable[[str, Role], GuardResult]


class FakeGuardModel:
    """Guard-model stand-in. Default: everything safe. Records every call in order."""

    def __init__(self, verdict: Verdict | None = None, delay_s: float = 0.0) -> None:
        self.verdict = verdict
        self.delay_s = delay_s
        self.calls: list[tuple[Role, str, list[Message]]] = []
        self.log: list[str] | None = None  # shared event log, if a test wants ordering

    async def check(
        self, text: str, role: Role, context: list[Message] | None = None
    ) -> GuardResult:
        self.calls.append((role, text, list(context or [])))
        if self.log is not None:
            self.log.append(f"guard:{role}:{text}")
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        v = self.verdict
        if v is None:
            return GuardResult(True)
        if isinstance(v, Exception):
            raise v
        if callable(v):
            return v(text, role)
        return v

    def texts(self, role: Role) -> list[str]:
        return [t for r, t, _ in self.calls if r == role]


def policy(settings: Settings, lines: LineBook) -> GuardPolicy:
    p = GuardPolicy.load(settings.content_path(settings.guard_policy_file))
    p.check_lines(set(lines.categories))
    return p


def allowlist(lines: LineBook, extra: list[str] | None = None) -> ContactAllowlist:
    texts = [*lines.all_lines(), "Email ai@sfsu.edu or call University Police at 415-338-2222."]
    return ContactAllowlist.from_texts(texts + (extra or []))


def chains(
    settings: Settings,
    lines: LineBook,
    input_model: FakeGuardModel | None = None,
    output_model: FakeGuardModel | None = None,
    timeout_s: float = 1.0,
) -> tuple[GuardChain, GuardChain, FakeGuardModel, FakeGuardModel]:
    pol = policy(settings, lines)
    allow = allowlist(lines)
    im = input_model or FakeGuardModel()
    om = output_model or FakeGuardModel()
    return (
        GuardChain("input", InputRules(pol, allow), im, pol, timeout_s),
        GuardChain("output", OutputRules(pol, allow), om, pol, timeout_s),
        im,
        om,
    )


def kill_switch(path: Path | None = None) -> KillSwitch:
    root = path or Path(tempfile.mkdtemp(prefix="ada-kill-"))
    return KillSwitch(root / "kill_switch.json")
