"""Text normalization shared by the guard rules (and what the chat model then sees).

Attackers hide trigger words with zero-width characters, full-width letters, or odd
spacing. Every rule runs on normalized text, and the pipeline passes the same normalized
text on to the guard model and the chat model, so all three see the same message.
"""

from __future__ import annotations

import re
import unicodedata

_SPACE_RE = re.compile(r"\s+")
_LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """NFKC, drop invisible format characters (zero-width etc.), collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return _SPACE_RE.sub(" ", text).strip()


def fold(text: str) -> str:
    """Lowercase + leetspeak folding + punctuation to spaces: for blocklist matching only."""
    return " ".join(_NON_WORD_RE.sub(" ", normalize(text).lower().translate(_LEET)).split())
