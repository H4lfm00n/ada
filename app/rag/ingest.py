"""Turn lab_docs/*.md into retrievable chunks (no model calls here).

Follows the rules in lab_docs/README.md (designer-owned):
  * skip README.md and any file or folder whose name starts with "_" (templates),
  * strip HTML comments (template guidance lives in them),
  * one chunk per "##" section, carrying file, "#" title, "##" heading, "Last updated",
  * warn on leftover TODO( markers, listing file + heading.

Grounding (DECISIONS D19): a section that still contains "TODO(" is left out of the
index entirely, so a placeholder can never reach the model. The designer's own rule is
"a half-filled section is worse than a missing one".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
TITLE_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$")
SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*#*[ \t]*$")
UPDATED_RE = re.compile(r"^\s*last updated\s*:\s*(.*?)\s*$", re.IGNORECASE)
TODO_RE = re.compile(r"TODO\s*\(", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Sections longer than this are split at paragraph breaks (heading repeated on each part).
# The designer caps files at ~600 words, so this rarely triggers.
MAX_CHUNK_CHARS = 1200


@dataclass(frozen=True, slots=True)
class DocChunk:
    id: str  # "<file>#<n>", stable for a given file content
    source_file: str  # path relative to the docs folder, e.g. "join.md"
    title: str  # the file's "#" title
    heading: str  # the "##" heading (the title for text before the first "##")
    text: str  # section body, comments stripped
    last_updated: str  # YYYY-MM-DD, or "unknown"

    @property
    def embed_text(self) -> str:
        """What gets embedded: title + heading give short sections their subject."""
        head = self.title if self.heading == self.title else f"{self.title}\n{self.heading}"
        return f"{head}\n{self.text}"


@dataclass(slots=True)
class IngestWarning:
    source_file: str
    heading: str
    message: str

    def __str__(self) -> str:
        where = f"{self.source_file}" + (f" > {self.heading}" if self.heading else "")
        return f"{where}: {self.message}"


@dataclass(slots=True)
class ParsedCorpus:
    chunks: list[DocChunk] = field(default_factory=list)
    warnings: list[IngestWarning] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)  # every eligible file
    skipped_sections: int = 0


def is_doc_file(path: Path, root: Path) -> bool:
    """A knowledge file: Markdown, not README.md, no "_" or "." anywhere in its path."""
    rel = path.relative_to(root)
    if path.suffix.lower() != ".md" or path.name.lower() == "readme.md":
        return False
    return not any(part.startswith(("_", ".")) for part in rel.parts)


def doc_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"lab docs folder not found: {root}")
    return sorted(p for p in root.rglob("*") if p.is_file() and is_doc_file(p, root))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): file_hash(p) for p in doc_files(root)}


def _clean(lines: list[str]) -> str:
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _split_long(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if current and len(current) + 2 + len(para) > limit:
            parts.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        parts.append(current)
    return parts


def parse_doc(text: str, source_file: str) -> tuple[list[DocChunk], list[IngestWarning], int]:
    """Parse one Markdown doc. Returns (chunks, warnings, sections skipped for TODO)."""
    text = COMMENT_RE.sub("", text.replace("\r\n", "\n"))
    title = ""
    last_updated = "unknown"
    warnings: list[IngestWarning] = []
    sections: list[tuple[str, list[str]]] = [("", [])]  # ("" = text before the first ##)

    for line in text.split("\n"):
        if not title and (m := TITLE_RE.match(line)) and not line.startswith("##"):
            title = m.group(1).strip()
            continue
        if (m := UPDATED_RE.match(line)) and not sections[-1][0]:
            value = m.group(1)
            if DATE_RE.match(value):
                last_updated = value
            else:
                warnings.append(IngestWarning(source_file, "", f"'Last updated' is {value!r}"))
            continue
        if m := SECTION_RE.match(line):
            sections.append((m.group(1).strip(), []))
            continue
        sections[-1][1].append(line)

    if not title:
        title = Path(source_file).stem.replace("-", " ").replace("_", " ")
        warnings.append(IngestWarning(source_file, "", "no '# Title' line; using file name"))
    if last_updated == "unknown" and not any("Last updated" in w.message for w in warnings):
        warnings.append(IngestWarning(source_file, "", "no 'Last updated: YYYY-MM-DD' line"))

    chunks: list[DocChunk] = []
    skipped = 0
    for heading, body_lines in sections:
        body = _clean(body_lines)
        if not body:
            continue
        heading = heading or title
        if TODO_RE.search(body) or TODO_RE.search(heading):
            skipped += 1
            warnings.append(IngestWarning(source_file, heading, "has TODO(...): left out"))
            continue
        for part in _split_long(body):
            chunks.append(
                DocChunk(
                    id=f"{source_file}#{len(chunks)}",
                    source_file=source_file,
                    title=title,
                    heading=heading,
                    text=part,
                    last_updated=last_updated,
                )
            )
    return chunks, warnings, skipped


def parse_corpus(root: Path) -> ParsedCorpus:
    corpus = ParsedCorpus()
    for path in doc_files(root):
        rel = path.relative_to(root).as_posix()
        corpus.file_hashes[rel] = file_hash(path)
        chunks, warnings, skipped = parse_doc(path.read_text(encoding="utf-8"), rel)
        corpus.chunks.extend(chunks)
        corpus.warnings.extend(warnings)
        corpus.skipped_sections += skipped
        if not chunks:
            corpus.warnings.append(IngestWarning(rel, "", "no usable sections"))
    return corpus
