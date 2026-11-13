"""Cheap rule guards: run first, in microseconds, before the guard model.

InputRules:  too long, blocklisted word, prompt-injection pattern, visitor's own PII.
OutputRules: blocklisted word, prompt canary (leak), phone/email/link not in the docs.

Both implement the Guard interface. They return GuardResult.category = a rule code
("injection", "canary", ...) that GuardPolicy maps to a lines.yaml category.
"""

from __future__ import annotations

from app.guards.contacts import ContactAllowlist, find_contacts
from app.guards.normalize import fold, normalize
from app.guards.policy import GuardPolicy
from app.interfaces import GuardResult, Message, Role

ALLOW = GuardResult(True)


def _blocklisted(policy: GuardPolicy, text: str) -> str | None:
    folded = f" {fold(text)} "
    for word in policy.blocklist:
        if f" {word} " in folded:
            return word
    return None


class InputRules:
    """Rules for the visitor's message (before any model sees it)."""

    name = "input-rules"

    def __init__(self, policy: GuardPolicy, allowlist: ContactAllowlist) -> None:
        self.policy = policy
        self.allowlist = allowlist

    async def check(
        self, text: str, role: Role = "user", context: list[Message] | None = None
    ) -> GuardResult:
        p = self.policy
        if len(text) > p.max_input_chars:
            return GuardResult(False, "too_long", f"{len(text)} chars > {p.max_input_chars}")
        norm = normalize(text)
        if word := _blocklisted(p, norm):
            return GuardResult(False, "blocklist", f"blocklisted term ({len(word)} chars)")
        for rx in p.injection_patterns:
            if m := rx.search(norm):
                return GuardResult(False, "injection", f"pattern matched: {m.group(0)[:60]!r}")
        if p.block_visitor_pii:
            typed = [c for c in find_contacts(norm) if c.kind in ("phone", "email")]
            unlisted = [c for c in typed if not self.allowlist.allows(c)]
            if unlisted:
                return GuardResult(False, "visitor_pii", f"visitor typed a {unlisted[0].kind}")
            if p.student_id_re and p.student_id_re.search(norm):
                return GuardResult(False, "visitor_pii", "visitor typed a student ID")
        return ALLOW


class OutputRules:
    """Rules for each reply sentence (before it is shown)."""

    name = "output-rules"

    def __init__(self, policy: GuardPolicy, allowlist: ContactAllowlist) -> None:
        self.policy = policy
        self.allowlist = allowlist

    async def check(
        self, text: str, role: Role = "assistant", context: list[Message] | None = None
    ) -> GuardResult:
        p = self.policy
        norm = normalize(text)
        if word := _blocklisted(p, norm):
            return GuardResult(False, "blocklist", f"blocklisted term ({len(word)} chars)")
        low = norm.lower()
        for c in p.canaries:
            if c.lower() in low:
                return GuardResult(False, "canary", f"system prompt phrase: {c!r}")
        if p.block_unlisted_contacts:
            bad = self.allowlist.blocked(norm)
            if bad:
                # The contact itself stays out of the reason (and so out of the transcript).
                return GuardResult(False, "contact", f"{bad[0].kind} not in lab docs")
        return ALLOW
