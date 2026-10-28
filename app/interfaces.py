"""Stable interfaces between pipeline stages.

These are the hooks voice (M6) and the robot arm (M7) will plug into, so keep the
signatures stable. Implemented so far: ChatModel (app/ollama_client.py), SessionManager
(app/sessions.py), OutputSink + ActionHandler stub (app/sinks.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant"]

# Named actions sent to ActionHandler. The robot arm (M7) maps these to motions.
ACTION_WAVE = "wave"  # a new visitor started talking


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class GuardResult:
    allowed: bool
    category: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Chunk:
    """A retrieved piece of a lab doc. Always carries its source for grounding."""

    text: str
    source_file: str
    heading: str
    score: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ChatModel(Protocol):
    def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        """Yield reply text deltas (never model 'thinking' text)."""
        ...


@runtime_checkable
class Guard(Protocol):
    async def check(self, text: str, role: Role) -> GuardResult: ...  # implemented in M3


@runtime_checkable
class Retriever(Protocol):
    async def search(self, query: str, k: int) -> list[Chunk]: ...  # implemented in M2


@runtime_checkable
class OutputSink(Protocol):
    async def on_sentence(self, session_id: str, sentence: str) -> None:
        """Receives each completed reply sentence (UI now, TTS later)."""
        ...


@runtime_checkable
class ActionHandler(Protocol):
    async def on_action(self, session_id: str, action: str) -> None:
        """Receives named actions like 'wave' (no-op now, robot later)."""
        ...


@runtime_checkable
class SessionManager(Protocol):
    def create(self) -> str: ...
    def get(self, session_id: str) -> list[Message] | None: ...
    def reset(self, session_id: str) -> None: ...
