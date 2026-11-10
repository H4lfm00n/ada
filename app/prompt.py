"""System prompt builder: loads prompts/system_prompt.md and fills its runtime slots.

The designer owns the wording. This module only:
  * strips the YAML front matter (loader metadata, never sent to the model),
  * fills {{bot_name}}, {{current_time}}, {{retrieved_context}},
  * formats retrieved chunks the way the front matter specifies, escaping any
    "<lab_docs>" / "</lab_docs>" inside a chunk so docs can't break out of their block,
    and in visitor messages so nobody can forge a docs block.

Unknown or unfilled slots raise PromptError: a typo in the template should fail at
startup, not leak "{{bot_name}}" onto the kiosk.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from app.config import Settings
from app.interfaces import Chunk, Message

log = logging.getLogger(__name__)

SLOT_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
LAB_DOCS_TAG_RE = re.compile(r"<\s*(/?)\s*lab_docs\s*>", re.IGNORECASE)

SYSTEM_SLOTS = frozenset({"bot_name", "current_time", "retrieved_context"})


class PromptError(ValueError):
    """The prompt template is malformed or a slot can't be filled."""


def split_front_matter(text: str) -> tuple[dict, str]:
    """Return (front matter dict, body). A file without front matter is all body.

    The front matter is notes for humans, never sent to the model, so a YAML typo in it
    is logged and ignored rather than taking the kiosk down. The body is still stripped.
    """
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        log.warning("system prompt front matter is not valid YAML (ignored): %s", exc)
        meta = {}
    if not isinstance(meta, dict):
        log.warning("system prompt front matter is not a mapping (ignored)")
        meta = {}
    return meta, text[m.end() :]


def fill_slots(template: str, values: Mapping[str, str]) -> str:
    """Replace every {{slot}}. Raises PromptError if a slot has no value."""
    missing = sorted({m.group(1) for m in SLOT_RE.finditer(template)} - values.keys())
    if missing:
        raise PromptError(f"no value for slot(s): {', '.join(missing)}")
    return SLOT_RE.sub(lambda m: values[m.group(1)], template)


def escape_lab_docs_tags(text: str) -> str:
    """Neutralize <lab_docs> / </lab_docs> inside retrieved text."""
    return LAB_DOCS_TAG_RE.sub(lambda m: f"[{m.group(1)}lab_docs]", text)


def format_chunks(chunks: Iterable[Chunk]) -> str:
    """Format retrieved chunks per the template's front matter. Empty input -> ''."""
    blocks = []
    for c in chunks:
        updated = c.metadata.get("last_updated") or "unknown"
        header = f"[doc: {Path(c.source_file).name} | last updated: {updated}]"
        if c.heading:
            header += f"\n# {c.heading}"
        blocks.append(escape_lab_docs_tags(f"{header}\n{c.text.strip()}"))
    return "\n\n".join(blocks)


def format_time(now: datetime) -> str:
    """'Wednesday, October 28, 2026, 1:55 PM' (minute precision keeps the prompt stable)."""
    hour = now.strftime("%I").lstrip("0") or "12"
    return f"{now.strftime('%A, %B')} {now.day}, {now.year}, {hour}:{now.strftime('%M %p')}"


@dataclass(frozen=True, slots=True)
class SystemPrompt:
    """The designer's system prompt, front matter stripped, bot_name pre-filled."""

    template: str
    meta: dict
    source: Path

    @classmethod
    def load(cls, path: Path, bot_name: str) -> SystemPrompt:
        meta, body = split_front_matter(path.read_text(encoding="utf-8"))
        found = {m.group(1) for m in SLOT_RE.finditer(body)}
        unknown = sorted(found - SYSTEM_SLOTS)
        if unknown:
            raise PromptError(f"{path.name}: unknown slot(s): {', '.join(unknown)}")
        # bot_name is fixed per deployment: fill it once so only per-turn slots remain.
        body = SLOT_RE.sub(
            lambda m: bot_name if m.group(1) == "bot_name" else m.group(0), body
        ).strip()
        return cls(template=body, meta=meta, source=path)

    @classmethod
    def from_settings(cls, settings: Settings) -> SystemPrompt:
        return cls.load(settings.content_path(settings.system_prompt_file), settings.bot_name)

    def render(self, current_time: str, retrieved_context: str = "") -> str:
        return fill_slots(
            self.template,
            {"current_time": current_time, "retrieved_context": retrieved_context},
        )


class PromptBuilder:
    """Builds the message list for one turn: system prompt + history + new user message."""

    def __init__(self, system: SystemPrompt, timezone: str) -> None:
        self.system = system
        self._tz = ZoneInfo(timezone)

    @classmethod
    def from_settings(cls, settings: Settings) -> PromptBuilder:
        return cls(SystemPrompt.from_settings(settings), settings.timezone)

    def now_text(self, now: datetime | None = None) -> str:
        return format_time(now.astimezone(self._tz) if now else datetime.now(self._tz))

    def build(
        self,
        history: list[Message],
        user_text: str,
        chunks: Iterable[Chunk] = (),
        now: datetime | None = None,
    ) -> list[Message]:
        system = self.system.render(self.now_text(now), format_chunks(chunks))
        # A visitor typing "<lab_docs>...</lab_docs>" must not be able to forge or close
        # the docs block (red team: fake_lab_docs_tag_in_input).
        convo = [Message(m.role, escape_lab_docs_tags(m.content)) for m in history]
        return [
            Message("system", system),
            *convo,
            Message("user", escape_lab_docs_tags(user_text)),
        ]
