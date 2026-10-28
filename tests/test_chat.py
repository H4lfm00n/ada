from __future__ import annotations

from pathlib import Path

import pytest

from app import chat
from app.config import Settings
from app.pipeline import TurnResult

from .conftest import FINAL_STATS, FakeOllama


def test_demo_script_loads() -> None:
    steps = chat.load_script(chat.DEMO_SCRIPT)
    assert "bye" in steps and "/reset" in steps and len(steps) >= 5


async def test_scripted_run_prints_replies_and_latency(
    fake: FakeOllama, settings: Settings, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    script = tmp_path / "s.yaml"
    script.write_text("steps:\n  - hello\n  - what is AI?\n  - bye\n  - /reset\n")
    code = await chat.run_script(settings, script, transport=fake.transport, write_report=False)
    out = capsys.readouterr().out
    assert code == 0
    assert f"{settings.bot_name}> Hello there!" in out
    assert "first token" in out and "first sentence" in out
    assert "session reset: context cleared" in out
    assert "model replies: 2" in out
    assert "['wave']" in out
    assert "thinking: off" in out


async def test_scripted_run_fails_on_model_error(
    fake: FakeOllama, settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = {"n": 0}

    def reply(body: dict) -> list[dict]:
        calls["n"] += 1
        if calls["n"] == 1:  # warmup works
            return [{"message": {"content": "Hi."}, "done": False}, FINAL_STATS]
        return [{"error": "runner crashed"}]

    fake.chat_reply = reply
    script = tmp_path / "s.yaml"
    script.write_text("steps:\n  - hello\n")
    code = await chat.run_script(settings, script, transport=fake.transport, write_report=False)
    out = capsys.readouterr().out
    assert code == 1
    assert "line:guard_fallback" in out and "runner crashed" in out


async def test_scripted_run_reports_missing_model(
    fake: FakeOllama, settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del fake.models["qwen-test:27b"]
    script = tmp_path / "s.yaml"
    script.write_text("steps:\n  - hello\n")
    code = await chat.run_script(settings, script, transport=fake.transport, write_report=False)
    assert code == 1
    assert "not installed" in capsys.readouterr().err


def test_summary_targets(settings: Settings) -> None:
    fast = TurnResult("s", "q", ["a"], ttft_s=0.5, first_sentence_s=0.9, total_s=2)
    slow = TurnResult("s", "q", ["a"], ttft_s=2.5, first_sentence_s=3.0, total_s=5)
    line = TurnResult("s", "bye", ["Bye."], source="line:goodbye", total_s=0.01)
    s = chat.summarize([fast, line], settings)
    assert s["model_turns"] == 1 and s["meets_ttft_target"] is True
    assert chat.summarize([slow, slow], settings)["meets_ttft_target"] is False
