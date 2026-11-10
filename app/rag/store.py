"""The retrieval index: chunks + unit-length embeddings in one JSON file per docs folder.

Why not a vector DB (DECISIONS D18): the lab corpus is a few dozen sections, so a linear
scan in pure Python takes well under a millisecond, adds no dependency (no aarch64 wheel
risk), and has no telemetry to switch off. The file records the embed model and a hash of
every source file, so a model swap or a doc edit is detected instead of silently mixing
incompatible vectors or serving deleted facts.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.rag.ingest import DocChunk, corpus_hashes

# Bump when chunking or the file format changes, so old indexes are rebuilt.
INDEX_FORMAT = 1


class RagIndexError(RuntimeError):
    """The index is missing, unreadable, or was built for a different embed model."""


def normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        raise ValueError("zero-length embedding")
    return [x / norm for x in vec]


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@dataclass(slots=True)
class IndexedChunk:
    chunk: DocChunk
    vector: list[float]  # unit length


@dataclass(slots=True)
class Index:
    embed_model: str
    docs_dir: str  # as configured, e.g. "lab_docs"
    dims: int
    file_hashes: dict[str, str]
    entries: list[IndexedChunk] = field(default_factory=list)
    built_at: str = ""
    format: int = INDEX_FORMAT

    # -- persistence ---------------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "format": self.format,
            "embed_model": self.embed_model,
            "docs_dir": self.docs_dir,
            "dims": self.dims,
            "built_at": self.built_at or datetime.now(UTC).isoformat(timespec="seconds"),
            "file_hashes": self.file_hashes,
            "chunks": [
                {**asdict(e.chunk), "vector": [round(x, 6) for x in e.vector]} for e in self.entries
            ],
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a running kiosk never reads a half-written index

    @classmethod
    def load(cls, path: Path) -> Index:
        if not path.is_file():
            raise RagIndexError(f"no lab docs index at {path}; run `make ingest`")
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format") != INDEX_FORMAT:
                raise RagIndexError(
                    f"{path.name} has index format {data.get('format')}, expected "
                    f"{INDEX_FORMAT}; run `make ingest`"
                )
            entries = []
            for c in data["chunks"]:
                vector = c.pop("vector")
                entries.append(IndexedChunk(DocChunk(**c), vector))
            return cls(
                embed_model=data["embed_model"],
                docs_dir=data["docs_dir"],
                dims=int(data["dims"]),
                file_hashes=dict(data["file_hashes"]),
                entries=entries,
                built_at=data.get("built_at", ""),
            )
        except RagIndexError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise RagIndexError(f"{path} is unreadable ({exc}); run `make ingest`") from exc

    # -- consistency ---------------------------------------------------------------

    def check(self, embed_model: str, docs_root: Path) -> list[str]:
        """Problems that make this index unsafe or out of date. Empty list = good.

        Raises RagIndexError for an embed model mismatch: vectors from two models aren't
        comparable, so searching would return nonsense. A changed doc only warns: the
        index still serves what was true at the last ingest.
        """
        if self.embed_model != embed_model:
            raise RagIndexError(
                f"index was built with {self.embed_model}, config says {embed_model}; "
                "run `make ingest`"
            )
        current = corpus_hashes(docs_root)
        problems = []
        added = sorted(current.keys() - self.file_hashes.keys())
        removed = sorted(self.file_hashes.keys() - current.keys())
        changed = sorted(
            f for f in current.keys() & self.file_hashes.keys() if current[f] != self.file_hashes[f]
        )
        if added:
            problems.append(f"not indexed yet: {', '.join(added)}")
        if removed:
            problems.append(f"deleted but still indexed: {', '.join(removed)}")
        if changed:
            problems.append(f"edited since last ingest: {', '.join(changed)}")
        if problems:
            problems.append("run `make ingest`")
        return problems
