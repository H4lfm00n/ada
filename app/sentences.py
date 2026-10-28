"""Incremental sentence splitter for streamed model output.

Replies are emitted sentence by sentence: that's the unit the output guard checks (M3)
and what TTS will speak (M6). Feed text deltas as they arrive; get back sentences as
soon as they are known to be complete.

A sentence ends at . ! ? (or a run of them, plus closing quotes/brackets) when it is
followed by whitespace and then something that can start a new sentence. That means a
boundary is confirmed one character late, which costs a single token of latency and
avoids splitting "3.5", "sfsu.edu", "e.g. this", or "Dr. Lee".
"""

from __future__ import annotations

import re

TERMINATORS = ".!?"
# Straight and curly quotes/brackets (curly ones written as escapes for readability).
CLOSERS = "\"')]}\u201d\u2019\u00bb"
OPENERS = "\"'([\u201c\u2018\u00ab"
# Lowercased, without the trailing dot. Only checked when the terminator is a ".".
ABBREVIATIONS = frozenset(
    {"dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "e.g", "i.e", "approx", "dept"}
)
# A boundary needs: terminators, optional closers, whitespace, then a sentence start.
BOUNDARY_RE = re.compile(rf"[{re.escape(TERMINATORS)}]+[{re.escape(CLOSERS)}]*(\s+)(?=\S)")
WORD_BEFORE_RE = re.compile(r"(\S+)$")


class SentenceSplitter:
    def __init__(self, max_chars: int = 400) -> None:
        self._buf = ""
        self._scan_from = 0
        self.max_chars = max_chars

    def feed(self, delta: str) -> list[str]:
        """Add a text delta; return any sentences completed by it."""
        self._buf += delta
        out: list[str] = []
        while True:
            sentence = self._next_sentence()
            if sentence is None:
                break
            if sentence:
                out.append(sentence)
        while len(self._buf) > self.max_chars:
            out.append(self._force_split())
        return out

    def flush(self) -> list[str]:
        """End of stream: return whatever is left as a final sentence."""
        rest = self._buf.strip()
        self._buf, self._scan_from = "", 0
        return [rest] if rest else []

    # -- internals ---------------------------------------------------------------

    def _next_sentence(self) -> str | None:
        # Paragraph breaks always end a sentence (lists, headings the model shouldn't make).
        nl = self._buf.find("\n", self._scan_from)
        for m in BOUNDARY_RE.finditer(self._buf, self._scan_from):
            if nl != -1 and nl < m.start():
                break
            if self._is_real_boundary(m):
                return self._cut(m.start(1))
            self._scan_from = m.end(1)
        if nl != -1 and self._buf[nl + 1 :].strip():
            return self._cut(nl)
        return None

    def _is_real_boundary(self, m: re.Match[str]) -> bool:
        nxt = self._buf[m.end(1)]
        if not (nxt.isupper() or nxt.isdigit() or nxt in OPENERS):
            return False  # "e.g. the", "approx. ten" -> keep going
        if self._buf[m.start()] != ".":
            return True
        before = self._buf[: m.start()]
        word = WORD_BEFORE_RE.search(before)
        return not (word and word.group(1).lower().lstrip("(\"'") in ABBREVIATIONS)

    def _cut(self, at: int) -> str:
        sentence, self._buf = self._buf[:at].strip(), self._buf[at:].lstrip()
        self._scan_from = 0
        return sentence

    def _force_split(self) -> str:
        """Runaway sentence (no punctuation): break at the last space so TTS isn't starved."""
        cut = self._buf.rfind(" ", 0, self.max_chars)
        if cut <= 0:
            cut = self.max_chars
        return self._cut(cut)


def split_sentences(text: str) -> list[str]:
    """Split a complete text (convenience for tests and canned lines)."""
    s = SentenceSplitter()
    return [*s.feed(text), *s.flush()]
