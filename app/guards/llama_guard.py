"""The guard model: Llama Guard 3 served by Ollama (config `guard_model`).

Llama Guard's Ollama template turns a conversation into its classification prompt and
judges the LAST message: a user turn is checked as a prompt, an assistant turn as a
response. It answers "safe", or "unsafe" plus hazard codes on the next line
("unsafe\\nS1,S10"). Anything else is an error, and errors block (fail safe).
"""

from __future__ import annotations

import asyncio
import contextlib
import re

from app.config import Settings
from app.interfaces import GuardResult, Message, Role
from app.ollama_client import OllamaClient

_CODE_RE = re.compile(r"\bS\d{1,2}\b")


class GuardModelError(RuntimeError):
    """The guard model's answer couldn't be parsed."""


def parse_verdict(raw: str) -> tuple[bool, list[str]]:
    """'safe' -> (True, []); 'unsafe\\nS1,S10' -> (False, ['S1', 'S10']). Else raises."""
    text = raw.strip()
    first = text.split(None, 1)[0].lower() if text else ""
    if first == "safe":
        return True, []
    if first == "unsafe":
        return False, _CODE_RE.findall(text) or ["unknown"]
    raise GuardModelError(f"unexpected guard answer {text[:40]!r}")


class LlamaGuard:
    """Guard backed by the Ollama guard model. Raises on any failure; the chain fails safe."""

    name = "guard-model"

    def __init__(self, client: OllamaClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self.last_codes: list[str] = []
        # A call cut off by the chain's timeout keeps running here, so a model that was
        # still loading finishes loading (Ollama abandons a load whose request is
        # cancelled). While it runs, new calls fail fast instead of piling up (D41).
        self._orphan: asyncio.Task[GuardResult] | None = None

    def conversation(self, text: str, role: Role, context: list[Message] | None) -> list[Message]:
        """Last N context messages (starting on a user turn) + the text being judged."""
        n = self._settings.guard_context_messages
        ctx = [m for m in (context or []) if m.role in ("user", "assistant")]
        ctx = ctx[-n:] if n > 0 else []
        while ctx and ctx[0].role != "user":
            ctx = ctx[1:]
        if role == "assistant" and (not ctx or ctx[-1].role != "user"):
            raise GuardModelError("an assistant message needs the user message it answers")
        if role == "user" and ctx and ctx[-1].role == "user":
            ctx = ctx[:-1]  # roles must alternate
        return [*ctx, Message(role, text)]

    async def check(
        self, text: str, role: Role, context: list[Message] | None = None
    ) -> GuardResult:
        if self._orphan is not None:
            if not self._orphan.done():
                raise GuardModelError("guard model still busy after a timeout (loading?)")
            self._orphan = None
        task = asyncio.ensure_future(self._classify(text, role, context))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done():
                self._orphan = task
                task.add_done_callback(_swallow)
            raise

    async def _classify(self, text: str, role: Role, context: list[Message] | None) -> GuardResult:
        out = ""
        async for d in self._client.chat_stream(
            self._settings.guard_model,
            self.conversation(text, role, context),
            think=None,  # Llama Guard has no thinking mode; don't send the field
            options={"temperature": 0, "num_predict": 12},
            keep_alive=self._settings.keep_alive,
        ):
            out += d.content
        ok, codes = parse_verdict(out)
        self.last_codes = codes
        if ok:
            return GuardResult(True)
        return GuardResult(False, ",".join(codes), f"guard model: unsafe {','.join(codes)}")


def _swallow(task: asyncio.Task[GuardResult]) -> None:
    """Orphaned calls' results and errors are unused; read them so asyncio doesn't warn."""
    with contextlib.suppress(BaseException):
        task.result()
