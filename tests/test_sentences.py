from __future__ import annotations

import pytest

from app.sentences import SentenceSplitter, split_sentences


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Hi there! I'm a robot. Ask me anything?",
            ["Hi there!", "I'm a robot.", "Ask me anything?"],
        ),
        ("Pi is about 3.14 and that's fine.", ["Pi is about 3.14 and that's fine."]),
        ("Check sfsu.edu for that. Or ask.", ["Check sfsu.edu for that.", "Or ask."]),
        ("Use e.g. a CNN. Then train it.", ["Use e.g. a CNN.", "Then train it."]),
        ("Ask Dr. Lee about it. Done.", ["Ask Dr. Lee about it.", "Done."]),
        ('She said "it works." Then left.', ['She said "it works."', "Then left."]),
        ("Wait... What happened?", ["Wait...", "What happened?"]),
        ("Really?! Wow.", ["Really?!", "Wow."]),
        ("One thing\nAnother thing", ["One thing", "Another thing"]),
        ("ends without punctuation", ["ends without punctuation"]),
        ("Version 2. 5 tips follow.", ["Version 2.", "5 tips follow."]),
        ("   ", []),
    ],
)
def test_split(text: str, expected: list[str]) -> None:
    assert split_sentences(text) == expected


@pytest.mark.parametrize("size", [1, 2, 3, 5, 8])
def test_streaming_in_pieces_matches_whole(size: int) -> None:
    text = (
        "Totally okay, that's what I'm here for! I'm Dum-E, the lab's robot helper. "
        "You could ask me about e.g. transformers, or what 3.5 means.\nOr just say hi."
    )
    splitter = SentenceSplitter()
    out: list[str] = []
    for i in range(0, len(text), size):
        out += splitter.feed(text[i : i + size])
    out += splitter.flush()
    assert out == split_sentences(text)
    assert len(out) == 4


def test_sentence_is_released_as_soon_as_next_one_starts() -> None:
    s = SentenceSplitter()
    assert s.feed("Hello there.") == []  # could still be "there.com"
    assert s.feed(" ") == []
    assert s.feed("N") == ["Hello there."]
    assert s.flush() == ["N"]


def test_runaway_text_is_force_split() -> None:
    s = SentenceSplitter(max_chars=50)
    out = s.feed("word " * 30)
    assert out and all(len(x) <= 50 for x in out)
    rest = s.flush()
    assert " ".join(out + rest).split() == ["word"] * 30


def test_flush_resets_state() -> None:
    s = SentenceSplitter()
    s.feed("Half a sent")
    assert s.flush() == ["Half a sent"]
    assert s.flush() == []
    assert s.feed("New one. Next") == ["New one."]
