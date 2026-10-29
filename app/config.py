"""Runtime settings: config.yaml, overridden by ADA_* environment variables."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Settings(BaseSettings):
    """All runtime knobs. Values come from config.yaml; ADA_* env vars win."""

    model_config = SettingsConfigDict(env_prefix="ADA_", extra="forbid")

    bot_name: str = "Dum-E"

    ollama_url: str = "http://localhost:11434"
    request_timeout_s: float = Field(120.0, gt=0)
    keep_alive: str = "30m"

    chat_model: str
    guard_model: str
    embed_model: str

    think: bool = False
    temperature: float = Field(0.7, ge=0, le=2)
    num_ctx: int = Field(8192, gt=0)
    max_reply_tokens: int = Field(256, gt=0)

    session_timeout_s: float = Field(120.0, gt=0)
    max_history_messages: int = Field(20, ge=0)

    # Content files (relative to the repo root unless absolute). Swappable so evals can
    # compare prompt versions without editing the designer's files.
    system_prompt_file: str = "prompts/system_prompt.md"
    lines_file: str = "content/lines.yaml"
    timezone: str = "America/Los_Angeles"

    # Lab knowledge (RAG). The index lives at rag_index_path, one file per docs folder,
    # so the eval fixture corpus can never end up in the real index.
    lab_docs_dir: str = "lab_docs"
    rag_index_dir: str = "data/rag"
    rag_top_k: int = Field(4, ge=1, le=20)
    rag_min_score: float = Field(0.35, ge=-1, le=1)
    rag_max_context_chars: int = Field(3000, gt=0)
    rag_timeout_s: float = Field(5.0, gt=0)
    rag_query_instruction: str = ""

    target_first_token_s: float = Field(1.5, gt=0)
    target_decode_tok_s: float = Field(10.0, gt=0)

    @field_validator("ollama_url")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        # Brief section 3: no runtime calls off the machine.
        host = urlparse(v).hostname
        if host not in LOOPBACK_HOSTS:
            raise ValueError(f"ollama_url must point at this machine (got host {host!r})")
        return v.rstrip("/")

    @field_validator("chat_model", "guard_model", "embed_model")
    @classmethod
    def _non_empty_tag(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("model tag must not be empty")
        return v

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v

    def content_path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def lab_docs_path(self) -> Path:
        return self.content_path(self.lab_docs_dir)

    @property
    def rag_index_path(self) -> Path:
        """data/rag/<docs dir as a slug>.json, e.g. lab_docs.json, evals_fixtures_lab_docs.json."""
        docs = self.lab_docs_path.resolve()
        try:
            rel = docs.relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:  # a folder outside the repo: name + short hash of the full path
            rel = f"{docs.name}-{hashlib.sha1(str(docs).encode()).hexdigest()[:8]}"
        slug = re.sub(r"[^A-Za-z0-9]+", "_", rel).strip("_") or "root"
        return self.content_path(self.rag_index_dir) / f"{slug}.json"

    @property
    def model_tags(self) -> dict[str, str]:
        return {"chat": self.chat_model, "guard": self.guard_model, "embed": self.embed_model}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # env beats init: YAML values are passed as init kwargs by load_settings().
        return (env_settings, init_settings)


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from YAML (default: $ADA_CONFIG or repo config.yaml) + env overrides."""
    config_path = Path(path or os.environ.get("ADA_CONFIG") or DEFAULT_CONFIG_PATH)
    data: Any = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} must contain a YAML mapping")
    return Settings(**data)
