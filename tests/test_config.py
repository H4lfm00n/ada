from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, load_settings


def test_repo_config_loads() -> None:
    s = load_settings()
    assert s.chat_model and s.guard_model and s.embed_model
    assert s.think is False, "thinking must be off for user-facing replies"
    assert s.ollama_url == "http://localhost:11434"
    assert s.target_first_token_s == 1.5


def test_env_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADA_CHAT_MODEL", "gemma4:12b")
    monkeypatch.setenv("ADA_SESSION_TIMEOUT_S", "45")
    s = load_settings()
    assert s.chat_model == "gemma4:12b"
    assert s.session_timeout_s == 45


def test_alternate_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("chat_model: a:1\nguard_model: b:1\nembed_model: c:1\n")
    monkeypatch.setenv("ADA_CONFIG", str(cfg))
    assert load_settings().chat_model == "a:1"


def test_unknown_key_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("chat_model: a\nguard_model: b\nembed_model: c\nchat_modle: typo\n")
    with pytest.raises(ValidationError):
        load_settings(cfg)


@pytest.mark.parametrize("url", ["http://10.0.0.5:11434", "https://api.example.com"])
def test_ollama_url_must_be_local(url: str) -> None:
    with pytest.raises(ValidationError, match="this machine"):
        Settings(chat_model="a", guard_model="b", embed_model="c", ollama_url=url)


def test_empty_model_tag_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(chat_model="  ", guard_model="b", embed_model="c")


def test_unknown_timezone_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Settings(chat_model="a", guard_model="b", embed_model="c", timezone="Mars/Olympus")


def test_content_paths_resolve_from_repo_root(tmp_path: Path) -> None:
    s = load_settings()
    assert s.content_path(s.system_prompt_file).is_file()
    assert s.content_path(s.lines_file).is_file()
    assert s.content_path(str(tmp_path)) == tmp_path
