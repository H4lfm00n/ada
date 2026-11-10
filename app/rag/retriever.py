"""Retriever implementations (interface: app.interfaces.Retriever).

OllamaRetriever embeds the question with the configured embed model and scans the index.
Grounding guarantees that live here (brief 5.3, DECISIONS D19-D21):
  * nothing is returned unless the best chunk scores >= rag_min_score (the gate); then
    further chunks down to rag_extra_min_score may join it (D27),
  * total returned text is capped at rag_max_context_chars (the best chunk is always kept),
  * TODO sections were already dropped at ingest.
Errors propagate; the pipeline turns them into "no docs" (the model then says "not sure").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.interfaces import Chunk
from app.ollama_client import OllamaClient
from app.rag.ingest import DroppedSection, SkippedFile, parse_corpus
from app.rag.store import Index, IndexedChunk, RagIndexError, dot, normalize

log = logging.getLogger(__name__)

EMBED_BATCH = 32


def query_text(query: str, instruction: str) -> str:
    """Qwen3-Embedding format: instruction on the query side only."""
    return f"Instruct: {instruction}\nQuery: {query}" if instruction else query


@dataclass(frozen=True, slots=True)
class Hit:
    """A scored candidate, kept for diagnostics (probe, logs) even below the cutoff."""

    chunk_id: str
    heading: str
    score: float


def to_chunk(e: IndexedChunk, score: float) -> Chunk:
    c = e.chunk
    return Chunk(
        text=c.text,
        source_file=c.source_file,
        heading=c.heading,
        score=score,
        metadata={"id": c.id, "title": c.title, "last_updated": c.last_updated},
    )


def select(
    scored: list[tuple[float, IndexedChunk]],
    k: int,
    min_score: float,
    max_chars: int,
    extra_min_score: float | None = None,
) -> list[Chunk]:
    """Top-k, best first, within the character budget, with a two-tier cutoff (D27).

    The best chunk must reach `min_score` (the gate: is anything here relevant at all?).
    Once it does, more chunks may join down to `extra_min_score` (supporting facts, e.g.
    the second half of a "same hours?" comparison). None = same as min_score.
    """
    floor = min_score if extra_min_score is None else min(extra_min_score, min_score)
    ranked = sorted(scored, key=lambda t: t[0], reverse=True)
    if not ranked or ranked[0][0] < min_score:
        return []
    out: list[Chunk] = []
    used = 0
    seen: set[str] = set()
    for score, e in ranked:
        if score < floor or len(out) >= k:
            break
        text = e.chunk.text.strip()
        if text in seen:
            continue
        if out and used + len(text) > max_chars:
            continue  # a smaller lower-ranked chunk may still fit
        seen.add(text)
        used += len(text)
        out.append(to_chunk(e, score))
    return out


def cutoff_str(settings: Settings) -> str:
    """'0.5' or '0.5/0.4' (gate / floor for extra chunks, D27)."""
    extra = settings.rag_extra_min_score
    if extra is None or extra >= settings.rag_min_score:
        return f"{settings.rag_min_score}"
    return f"{settings.rag_min_score}/{extra}"


class OllamaRetriever:
    def __init__(self, client: OllamaClient, index: Index, settings: Settings) -> None:
        self._client = client
        self.index = index
        self._settings = settings
        self.last_hits: list[Hit] = []  # every candidate from the last search, best first

    @classmethod
    def open(cls, client: OllamaClient, settings: Settings) -> tuple[OllamaRetriever, list[str]]:
        """Load and check the index. Returns (retriever, warnings). Raises RagIndexError."""
        index = Index.load(settings.rag_index_path)
        warnings = index.check(settings.embed_model, settings.lab_docs_path)
        return cls(client, index, settings), warnings

    async def embed_query(self, query: str) -> list[float]:
        vecs = await self._client.embed(
            self._settings.embed_model,
            [query_text(query, self._settings.rag_query_instruction)],
            keep_alive=self._settings.keep_alive,
        )
        if not vecs:
            raise RagIndexError("embed model returned no vector")
        if len(vecs[0]) != self.index.dims:
            raise RagIndexError(
                f"query vector has {len(vecs[0])} dims, index has {self.index.dims}; "
                "run `make ingest`"
            )
        return normalize(vecs[0])

    async def search(self, query: str, k: int) -> list[Chunk]:
        self.last_hits = []
        if not self.index.entries or not query.strip():
            return []
        q = await self.embed_query(query)
        scored = [(dot(q, e.vector), e) for e in self.index.entries]
        scored.sort(key=lambda t: t[0], reverse=True)
        self.last_hits = [Hit(e.chunk.id, e.chunk.heading, s) for s, e in scored]
        st = self._settings
        return select(scored, k, st.rag_min_score, st.rag_max_context_chars, st.rag_extra_min_score)


class EmptyRetriever:
    """Used when there is no usable index: every lab question gets "not sure"."""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        self.last_hits: list[Hit] = []

    async def search(self, query: str, k: int) -> list[Chunk]:
        return []


@dataclass(slots=True)
class IngestReport:
    files: int
    chunks: int
    dropped: list[DroppedSection]
    warnings: list[str]
    index_path: Path
    dims: int
    skipped: list[SkippedFile] = field(default_factory=list)
    # file -> headings of its indexed sections, in order (a long section repeats per part)
    sections: dict[str, list[str]] = field(default_factory=dict)


async def build_index(client: OllamaClient, settings: Settings) -> IngestReport:
    """Parse lab_docs_dir, embed every chunk, write the index atomically."""
    root = settings.lab_docs_path
    corpus = parse_corpus(root)
    entries: list[IndexedChunk] = []
    dims = 0
    for i in range(0, len(corpus.chunks), EMBED_BATCH):
        batch = corpus.chunks[i : i + EMBED_BATCH]
        vecs = await client.embed(
            settings.embed_model, [c.embed_text for c in batch], keep_alive=settings.keep_alive
        )
        if len(vecs) != len(batch):
            raise RagIndexError(f"asked for {len(batch)} embeddings, got {len(vecs)}")
        for chunk, vec in zip(batch, vecs, strict=True):
            if dims and len(vec) != dims:
                raise RagIndexError("embed model returned vectors of different sizes")
            dims = len(vec)
            entries.append(IndexedChunk(chunk, normalize(vec)))
    index = Index(
        embed_model=settings.embed_model,
        docs_dir=settings.lab_docs_dir,
        dims=dims,
        file_hashes=corpus.file_hashes,
        entries=entries,
    )
    path = settings.rag_index_path
    index.save(path)
    sections: dict[str, list[str]] = {f: [] for f in corpus.file_hashes}
    for c in corpus.chunks:
        sections[c.source_file].append(c.heading)
    return IngestReport(
        files=len(corpus.file_hashes),
        chunks=len(entries),
        dropped=corpus.dropped,
        warnings=[str(w) for w in corpus.warnings],
        index_path=path,
        dims=dims,
        skipped=corpus.skipped,
        sections=sections,
    )


def open_retriever(
    client: OllamaClient, settings: Settings
) -> tuple[OllamaRetriever | EmptyRetriever, list[str]]:
    """The retriever the app should use, plus warnings to show the operator.

    Never raises: with no usable index the assistant still runs, and every lab question
    gets "not sure" (fail safe). The warnings say why, loudly, in `make chat`/`make health`.
    """
    warnings: list[str] = []
    if "fixtures" in Path(settings.lab_docs_dir).parts:
        warnings.append(
            f"lab_docs_dir is {settings.lab_docs_dir}: INVENTED eval facts, not the real lab"
        )
    try:
        retriever, problems = OllamaRetriever.open(client, settings)
    except (RagIndexError, FileNotFoundError) as exc:
        return EmptyRetriever(str(exc)), [*warnings, f"lab docs OFF: {exc}"]
    warnings += [f"lab docs index: {p}" for p in problems]
    if not retriever.index.entries:
        warnings.append(f"lab docs index is empty (no filled-in docs in {settings.lab_docs_dir})")
    return retriever, warnings
