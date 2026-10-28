"""In-memory sessions: one per visitor, wiped on reset or inactivity timeout.

Brief section 5.6: new visitor = fresh context. Nothing here touches disk; transcript
logging (M3) is a separate, explicit sink.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.config import Settings
from app.interfaces import Message

Clock = Callable[[], float]


@dataclass(slots=True)
class Session:
    id: str
    created_at: float
    last_active: float
    history: list[Message] = field(default_factory=list)
    recent_lines: dict[str, str] = field(default_factory=dict)  # category -> last line used
    turns: int = 0
    busy: int = 0  # replies in flight; a busy session never times out


class InMemorySessionManager:
    """Implements the SessionManager interface, plus helpers the pipeline needs."""

    def __init__(
        self, timeout_s: float, max_history_messages: int = 20, clock: Clock = time.monotonic
    ) -> None:
        self.timeout_s = timeout_s
        self.max_history_messages = max_history_messages
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    @classmethod
    def from_settings(
        cls, settings: Settings, clock: Clock = time.monotonic
    ) -> InMemorySessionManager:
        return cls(settings.session_timeout_s, settings.max_history_messages, clock)

    # -- SessionManager interface -------------------------------------------------

    def create(self) -> str:
        now = self._clock()
        sid = uuid.uuid4().hex[:12]
        self._sessions[sid] = Session(id=sid, created_at=now, last_active=now)
        return sid

    def get(self, session_id: str) -> list[Message] | None:
        """History of a live session (a copy), or None if unknown or timed out."""
        s = self.session(session_id)
        return list(s.history) if s else None

    def reset(self, session_id: str) -> None:
        """Wipe the session's context. The id stays valid for the next visitor."""
        s = self._sessions.get(session_id)
        if s is None:
            return
        now = self._clock()
        self._sessions[session_id] = Session(id=session_id, created_at=now, last_active=now)

    # -- extras --------------------------------------------------------------------

    def session(self, session_id: str) -> Session | None:
        s = self._sessions.get(session_id)
        if s is None or self._expired(s):
            return None
        return s

    def peek(self, session_id: str) -> Session | None:
        """The session even if it has timed out (for deciding whether to say goodbye)."""
        return self._sessions.get(session_id)

    def end(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def is_expired(self, session_id: str) -> bool:
        s = self._sessions.get(session_id)
        return s is not None and self._expired(s)

    def expire_idle(self) -> list[str]:
        """Drop timed-out sessions; return their ids so callers can say goodbye."""
        gone = [sid for sid, s in self._sessions.items() if self._expired(s)]
        for sid in gone:
            del self._sessions[sid]
        return gone

    def append(self, session_id: str, *messages: Message) -> None:
        s = self._require(session_id)
        s.history.extend(messages)
        if self.max_history_messages == 0:
            s.history.clear()
        elif len(s.history) > self.max_history_messages:
            # Drop oldest whole turns so history never starts with an assistant message.
            drop = len(s.history) - self.max_history_messages
            while drop < len(s.history) and s.history[drop].role != "user":
                drop += 1
            del s.history[:drop]
        s.last_active = self._clock()

    def touch(self, session_id: str) -> None:
        self._require(session_id).last_active = self._clock()

    @contextmanager
    def active(self, session_id: str) -> Iterator[Session]:
        """Mark a session busy for the duration of a reply (never expires mid-reply)."""
        s = self._require(session_id)
        s.busy += 1
        s.last_active = self._clock()
        try:
            yield s
        finally:
            s.busy -= 1
            s.last_active = self._clock()

    def __len__(self) -> int:
        return len(self._sessions)

    def _expired(self, s: Session) -> bool:
        return s.busy == 0 and self._clock() - s.last_active >= self.timeout_s

    def _require(self, session_id: str) -> Session:
        s = self.session(session_id)
        if s is None:
            raise KeyError(f"no live session {session_id!r}")
        return s
