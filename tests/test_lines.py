from __future__ import annotations

import itertools
import random
from pathlib import Path

import pytest

from app.config import Settings
from app.lines import GOODBYE, GUARD_FALLBACK, REQUIRED_CATEGORIES, LineBook, LinesError


@pytest.fixture
def book(settings: Settings) -> LineBook:
    return LineBook.from_settings(settings, rng=random.Random(0))


def test_repo_lines_load_with_required_categories(book: LineBook) -> None:
    assert set(book.categories) >= REQUIRED_CATEGORIES
    assert book.category("emergency").jokes is False


def test_every_line_renders_without_slots(settings: Settings) -> None:
    book = LineBook.from_settings(settings.model_copy(update={"bot_name": "Sprocket"}))
    for name in book.categories:
        for _ in range(30):
            line = book.pick(name)
            assert "{{" not in line and line.strip()
    assert any("Sprocket" in book.pick("idle_greetings") for _ in range(100))


def test_dont_know_lines_keep_eval_phrases(book: LineBook) -> None:
    for line in book.category("dont_know").lines:
        assert "not sure" in line and "lab member" in line


def test_no_immediate_repeat_within_a_session(book: LineBook) -> None:
    recent: dict[str, str] = {}
    picks = [book.pick(GUARD_FALLBACK, recent) for _ in range(200)]
    assert all(a != b for a, b in itertools.pairwise(picks))
    assert recent[GUARD_FALLBACK] in book.category(GUARD_FALLBACK).lines
    assert len(set(picks)) == len(book.category(GUARD_FALLBACK).lines)


def test_repeat_memory_is_per_category(book: LineBook) -> None:
    recent: dict[str, str] = {}
    book.pick(GOODBYE, recent)
    book.pick(GUARD_FALLBACK, recent)
    assert set(recent) == {GOODBYE, GUARD_FALLBACK}


def test_unknown_category_raises(book: LineBook) -> None:
    with pytest.raises(LinesError, match="unknown line category"):
        book.pick("does_not_exist")


def _yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "lines.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_required_category_fails(tmp_path: Path) -> None:
    with pytest.raises(LinesError, match="missing required"):
        LineBook.load(_yaml(tmp_path, "categories:\n  goodbye:\n    lines: [bye]\n"), "X")


def test_unknown_slot_fails(tmp_path: Path) -> None:
    cats = "".join(f"  {c}:\n    lines: [ok]\n" for c in REQUIRED_CATEGORIES - {GOODBYE})
    body = f"categories:\n{cats}  goodbye:\n    lines: ['bye {{{{visitor}}}}']\n"
    with pytest.raises(LinesError, match="unknown slot"):
        LineBook.load(_yaml(tmp_path, body), "X")


def test_empty_category_fails(tmp_path: Path) -> None:
    with pytest.raises(LinesError, match="non-empty"):
        LineBook.load(_yaml(tmp_path, "categories:\n  goodbye:\n    lines: []\n"), "X")
