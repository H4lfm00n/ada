"""Phrase matching, exactly as evals/README.md "Matching rules" describes.

- Case-insensitive; curly quotes become straight ones and whitespace is collapsed (on
  both the reply and the phrase) before matching.
- A plain string is a whole-word phrase: `\\b` + re.escape(phrase) + `\\b`, except no `\\b`
  next to a non-word character at either end (so "$50" and "911" still work).
- "re:..." is a Python regex (IGNORECASE | MULTILINE).
- "$name" expands to shared.yaml phrase_sets.name (recursively).
- "{{bot_name}}" is replaced with the configured name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# Curly/prime quotes (by code point, so the source stays ASCII) -> straight quotes.
_SINGLE = (0x2018, 0x2019, 0x201A, 0x201B, 0x2032)
_DOUBLE = (0x201C, 0x201D, 0x201E, 0x201F, 0x2033)
_QUOTES = {**{c: "'" for c in _SINGLE}, **{c: '"' for c in _DOUBLE}}
_WS = re.compile(r"\s+")
_WORD = re.compile(r"\w")
BOT_SLOT = re.compile(r"\{\{\s*bot_name\s*\}\}")


class MatchError(ValueError):
    """A check refers to an unknown phrase set or has a bad regex."""


def norm(text: str) -> str:
    return _WS.sub(" ", text.translate(_QUOTES)).strip()


def fill_bot(text: str, bot_name: str) -> str:
    return BOT_SLOT.sub(bot_name, text)


@dataclass(frozen=True, slots=True)
class Phrase:
    label: str  # as written in the YAML (after $set expansion), for failure messages
    rx: re.Pattern[str]

    def search(self, text: str) -> bool:
        return self.rx.search(norm(text)) is not None


def phrase_regex(phrase: str) -> re.Pattern[str]:
    p = norm(phrase)
    if not p:
        raise MatchError("empty phrase")
    pre = r"\b" if _WORD.match(p[0]) else ""
    post = r"\b" if _WORD.match(p[-1]) else ""
    return re.compile(pre + re.escape(p) + post, re.IGNORECASE)


def expand(
    items: Iterable[object], sets: Mapping[str, list[str]], _seen: frozenset[str] = frozenset()
) -> list[str]:
    out: list[str] = []
    for raw in items:
        item = str(raw)
        if item.startswith("$") and not item.startswith("$ ") and _is_set_name(item[1:]):
            name = item[1:]
            if name not in sets:
                raise MatchError(f"unknown phrase set {item!r}")
            if name in _seen:
                raise MatchError(f"phrase set {item!r} includes itself")
            out += expand(sets[name], sets, _seen | {name})
        else:
            out.append(item)
    return out


def _is_set_name(s: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s) is not None


def compile_items(
    items: Iterable[object] | None, sets: Mapping[str, list[str]], bot_name: str
) -> list[Phrase]:
    out: list[Phrase] = []
    for item in expand(items or [], sets):
        item = fill_bot(item, bot_name)
        if item.startswith("re:"):
            try:
                rx = re.compile(item[3:], re.IGNORECASE | re.MULTILINE)
            except re.error as exc:
                raise MatchError(f"bad regex {item!r}: {exc}") from exc
        else:
            rx = phrase_regex(item)
        out.append(Phrase(item, rx))
    return out
