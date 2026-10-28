from __future__ import annotations

import pytest

from app import bench
from app.config import Settings
from app.ollama_client import OllamaClient, OllamaError

from .conftest import FINAL_STATS, FakeOllama


def test_bench_file_has_five_questions() -> None:
    data = bench.load_bench()
    assert len(data["questions"]) == 5
    assert data["system"].strip()


async def test_run_bench_measures_and_verifies_thinking_off(
    fake: FakeOllama, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        report = await bench.run_bench(settings, client, runs=1, overhead=True)

    assert len(report["runs"]) == 5
    s = report["summary"]
    assert s["decode_tok_s_median"] == pytest.approx(20.0)
    assert s["thinking_leaked"] is False
    assert s["meets_ttft_target"] is True
    assert s["meets_decode_target"] is True
    assert {"guard_median_s", "embed_median_s", "est_pipeline_ttft_s"} <= report["overhead"].keys()
    chat_calls = [
        r for r in fake.requests if r["path"] == "/api/chat" and r["model"] == settings.chat_model
    ]
    assert chat_calls and all(r["think"] is False for r in chat_calls)
    assert "thinking: off (verified)" in capsys.readouterr().out


async def test_run_bench_flags_thinking_leak(fake: FakeOllama, settings: Settings) -> None:
    fake.chat_reply = lambda body: [
        {"message": {"content": "<think>hmm</think> Hi"}, "done": False},
        FINAL_STATS,
    ]
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        report = await bench.run_bench(settings, client, runs=1, overhead=False)
    assert report["summary"]["thinking_leaked"] is True


async def test_run_bench_requires_chat_model(fake: FakeOllama, settings: Settings) -> None:
    del fake.models["qwen-test:27b"]
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        with pytest.raises(OllamaError, match="not installed"):
            await bench.run_bench(settings, client, runs=1, overhead=False)


def test_summary_detects_target_miss(settings: Settings) -> None:
    slow = bench.RunResult("q", 3.0, 9.0, 40, 5.0, 50, 500.0, 0, False, "x")
    s = bench.summarize([slow], settings)
    assert s["meets_ttft_target"] is False
    assert s["meets_decode_target"] is False


async def test_bench_uses_real_system_prompt_by_default(
    fake: FakeOllama, settings: Settings
) -> None:
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        report = await bench.run_bench(settings, client, runs=1, overhead=False)
    system = fake.requests[-1]["messages"][0]
    assert system["role"] == "system"
    assert "robot helper for the AI Lab at SFSU" in system["content"]
    assert "{{" not in system["content"] and "owner: designer" not in system["content"]
    assert report["settings"]["system_prompt"] == settings.system_prompt_file


async def test_bench_neutral_prompt_flag(fake: FakeOllama, settings: Settings) -> None:
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        await bench.run_bench(settings, client, runs=1, overhead=False, neutral_prompt=True)
    assert fake.requests[-1]["messages"][0]["content"] == bench.load_bench()["system"]
