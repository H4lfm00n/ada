from __future__ import annotations

import pytest

from app.interfaces import Message, SessionManager
from app.sessions import InMemorySessionManager


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def mgr(clock: FakeClock) -> InMemorySessionManager:
    return InMemorySessionManager(timeout_s=120, max_history_messages=4, clock=clock)


def test_implements_interface(mgr: InMemorySessionManager) -> None:
    assert isinstance(mgr, SessionManager)


def test_create_get_append(mgr: InMemorySessionManager) -> None:
    sid = mgr.create()
    assert mgr.get(sid) == []
    mgr.append(sid, Message("user", "hi"), Message("assistant", "hello"))
    assert mgr.get(sid) == [Message("user", "hi"), Message("assistant", "hello")]
    assert mgr.get("nope") is None


def test_get_returns_a_copy(mgr: InMemorySessionManager) -> None:
    sid = mgr.create()
    mgr.get(sid).append(Message("user", "sneaky"))  # type: ignore[union-attr]
    assert mgr.get(sid) == []


def test_reset_wipes_context_but_keeps_id(mgr: InMemorySessionManager) -> None:
    sid = mgr.create()
    mgr.append(sid, Message("user", "my name is Sam"), Message("assistant", "hi"))
    s = mgr.session(sid)
    assert s is not None
    s.turns, s.recent_lines["goodbye"] = 3, "bye"
    mgr.reset(sid)
    fresh = mgr.session(sid)
    assert fresh is not None and fresh is not s
    assert fresh.history == [] and fresh.turns == 0 and fresh.recent_lines == {}


def test_timeout_expires_session(mgr: InMemorySessionManager, clock: FakeClock) -> None:
    sid = mgr.create()
    mgr.append(sid, Message("user", "hi"))
    clock.advance(119)
    assert mgr.get(sid) is not None
    clock.advance(1)
    assert mgr.get(sid) is None
    assert mgr.is_expired(sid)
    assert mgr.peek(sid) is not None  # still there until swept
    assert mgr.expire_idle() == [sid]
    assert mgr.peek(sid) is None and len(mgr) == 0


def test_activity_extends_timeout(mgr: InMemorySessionManager, clock: FakeClock) -> None:
    sid = mgr.create()
    clock.advance(100)
    mgr.touch(sid)
    clock.advance(100)
    assert mgr.get(sid) is not None
    assert mgr.expire_idle() == []


def test_busy_session_never_expires(mgr: InMemorySessionManager, clock: FakeClock) -> None:
    sid = mgr.create()
    with mgr.active(sid):
        clock.advance(500)
        assert mgr.expire_idle() == []
        assert mgr.session(sid) is not None
    clock.advance(119)
    assert mgr.session(sid) is not None  # timer restarts when the reply finishes


def test_history_cap_drops_oldest_whole_turns(mgr: InMemorySessionManager) -> None:
    sid = mgr.create()
    for i in range(5):
        mgr.append(sid, Message("user", f"q{i}"), Message("assistant", f"a{i}"))
    hist = mgr.get(sid)
    assert hist is not None and len(hist) == 4
    assert [m.content for m in hist] == ["q3", "a3", "q4", "a4"]
    assert hist[0].role == "user"


def test_append_to_dead_session_raises(mgr: InMemorySessionManager, clock: FakeClock) -> None:
    sid = mgr.create()
    clock.advance(200)
    with pytest.raises(KeyError):
        mgr.append(sid, Message("user", "late"))


def test_sessions_are_isolated(mgr: InMemorySessionManager) -> None:
    a, b = mgr.create(), mgr.create()
    assert a != b
    mgr.append(a, Message("user", "secret"))
    assert mgr.get(b) == []
