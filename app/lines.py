"""Canned lines from content/lines.yaml (designer-owned).

Lines bypass the chat model: goodbye on reset/timeout, fallbacks when something fails,
and (from M3) guard deflections. Picking rules come from the file's header:
random line, never the same as the last one used for that category in this session,
{{bot_name}} filled from config.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.config import Settings

SLOT_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
LINE_SLOTS = frozenset({"bot_name"})

# Categories the code relies on today. M3 will add the guard categories.
GOODBYE = "goodbye"
GUARD_FALLBACK = "guard_fallback"
IDLE_GREETINGS = "idle_greetings"
DONT_KNOW = "dont_know"
OUT_OF_ORDER = "out_of_order"
REQUIRED_CATEGORIES = frozenset({GOODBYE, GUARD_FALLBACK, IDLE_GREETINGS, DONT_KNOW, OUT_OF_ORDER})


class LinesError(ValueError):
    """lines.yaml is malformed or a category is missing."""


@dataclass(frozen=True, slots=True)
class Category:
    name: str
    lines: tuple[str, ...]
    jokes: bool
    used_when: str


class LineBook:
    def __init__(
        self, categories: dict[str, Category], bot_name: str, rng: random.Random | None = None
    ) -> None:
        self._cats = categories
        self._bot_name = bot_name
        self._rng = rng or random.Random()

    @classmethod
    def load(cls, path: Path, bot_name: str, rng: random.Random | None = None) -> LineBook:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = data.get("categories")
        if not isinstance(raw, dict) or not raw:
            raise LinesError(f"{path.name}: no 'categories' mapping")
        cats: dict[str, Category] = {}
        for name, body in raw.items():
            lines = (body or {}).get("lines") or []
            if not lines or not all(isinstance(x, str) and x.strip() for x in lines):
                raise LinesError(f"{path.name}: category {name!r} needs a non-empty 'lines' list")
            for line in lines:
                unknown = {m.group(1) for m in SLOT_RE.finditer(line)} - LINE_SLOTS
                if unknown:
                    raise LinesError(f"{path.name}: {name!r} uses unknown slot(s) {unknown}")
            cats[name] = Category(
                name=name,
                lines=tuple(x.strip() for x in lines),
                jokes=bool(body.get("jokes", True)),
                used_when=str(body.get("used_when", "")),
            )
        missing = sorted(REQUIRED_CATEGORIES - cats.keys())
        if missing:
            raise LinesError(f"{path.name}: missing required categories: {', '.join(missing)}")
        return cls(cats, bot_name, rng)

    @classmethod
    def from_settings(cls, settings: Settings, rng: random.Random | None = None) -> LineBook:
        return cls.load(settings.content_path(settings.lines_file), settings.bot_name, rng)

    @property
    def categories(self) -> list[str]:
        return list(self._cats)

    def category(self, name: str) -> Category:
        try:
            return self._cats[name]
        except KeyError:
            raise LinesError(f"unknown line category {name!r}") from None

    def pick(self, name: str, recent: dict[str, str] | None = None) -> str:
        """Random line from `name`, avoiding recent[name]; records the pick in `recent`.

        `recent` is per-session state (Session.recent_lines), so repeats are avoided
        per visitor, as the designer specified.
        """
        cat = self.category(name)
        choices = list(cat.lines)
        if recent is not None and len(choices) > 1 and recent.get(name) in choices:
            choices.remove(recent[name])
        raw = self._rng.choice(choices)
        if recent is not None:
            recent[name] = raw
        return SLOT_RE.sub(lambda _m: self._bot_name, raw)
