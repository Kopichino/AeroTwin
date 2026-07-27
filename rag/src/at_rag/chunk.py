"""Structure-aware chunking for the knowledge corpus (Doc 07 section 7.9).

Splitting on a fixed token count is the wrong default for technical
documentation. A maintenance task cut in half mid-procedure retrieves as two
fragments that each look plausible and neither of which is actionable, and the
model has no way to know a step is missing.

This chunker splits on **markdown headings first**, so a chunk is a section, and
only falls back to length-based splitting when a single section is too long. Each
chunk carries its heading path, which does two things: it gives the embedding
model context it would otherwise lack, and it gives the UI something meaningful
to cite.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Target chunk size in characters. Roughly 400 tokens for English prose.
TARGET_CHARS = 1600

#: Overlap when a section must be split. Carries the tail of the previous chunk
#: so a sentence spanning the boundary is retrievable from either side.
OVERLAP_CHARS = 200

#: Sections shorter than this are merged into the following one, so a bare
#: heading does not become a chunk of its own.
MIN_CHARS = 120

HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

#: ATA task codes such as 72-31-00-200-802. Extracted so a query naming a task
#: can match it exactly, which dense retrieval alone does poorly.
TASK_CODE = re.compile(r"\b\d{2}-\d{2}-\d{2}(?:-\d{3}-\d{3})?\b")


@dataclass(frozen=True, slots=True)
class Document:
    """A parsed source document with its provenance metadata."""

    doc_id: str
    title: str
    source_type: str
    publisher: str
    path: str
    body: str
    license: str = ""
    revision: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable unit of text."""

    chunk_id: str
    doc_id: str
    title: str
    source_type: str
    section_path: str
    content: str
    index: int
    task_codes: tuple[str, ...] = ()

    @property
    def char_count(self) -> int:
        return len(self.content)

    def to_metadata(self) -> dict[str, Any]:
        """Flat metadata for the vector store.

        Chroma rejects nested structures, so lists are joined into strings.
        """
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "source_type": self.source_type,
            "section_path": self.section_path,
            "index": self.index,
            "task_codes": ",".join(self.task_codes),
        }


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML-ish frontmatter from the body.

    Parsed by hand rather than with PyYAML: the frontmatter is a flat key-value
    block, and this avoids a dependency plus the arbitrary-object risk that
    comes with a full YAML loader on corpus files.
    """
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, text[match.end() :]


def load_document(path: Path) -> Document:
    """Read one markdown file into a Document."""
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)

    return Document(
        doc_id=meta.get("doc_id") or path.stem.upper(),
        title=meta.get("title", path.stem),
        source_type=meta.get("source_type", "INTERNAL"),
        publisher=meta.get("publisher", "unknown"),
        path=str(path),
        body=body.strip(),
        license=meta.get("license", ""),
        revision=meta.get("revision", ""),
        metadata=meta,
    )


def load_corpus(root: Path) -> list[Document]:
    """Load every markdown document beneath ``root``, sorted for determinism."""
    return [load_document(path) for path in sorted(root.rglob("*.md"))]


def _split_long_section(text: str) -> list[str]:
    """Break an oversized section on paragraph boundaries, with overlap."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    parts: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > TARGET_CHARS and current:
            parts.append(current)
            # Carry the tail forward so a boundary-spanning sentence stays
            # retrievable from the following chunk too.
            current = (current[-OVERLAP_CHARS:] + "\n\n" + paragraph).strip()
        else:
            current = candidate

    if current:
        parts.append(current)
    return parts


def chunk_document(document: Document) -> list[Chunk]:
    """Split one document into retrievable chunks along its heading structure."""
    matches = list(HEADING.finditer(document.body))
    if not matches:
        return _emit(document, [("", document.body)])

    sections: list[tuple[str, str]] = []
    # A heading stack tracks the full path, so "72-31-00-200-802 — HPC borescope
    # inspection" carries "Chapter 72 — Engine > 72-31-00 — High-pressure
    # compressor" with it.
    stack: list[tuple[int, str]] = []

    preamble = document.body[: matches[0].start()].strip()
    if preamble:
        sections.append(("", preamble))

    for position, match in enumerate(matches):
        level = len(match.group(1))
        heading = match.group(2).strip()

        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))

        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(document.body)
        body = document.body[start:end].strip()

        sections.append((" > ".join(title for _, title in stack), body))

    # Merge sections too short to stand alone. A short section is folded into
    # the *previous* chunk where one exists, and otherwise carried forward into
    # the next -- an earlier version only looked backwards, which left a short
    # opening section stranded as an 8-character chunk that retrieved as noise.
    merged: list[tuple[str, str]] = []
    carried: tuple[str, str] | None = None

    for path, body in sections:
        if not body:
            continue

        if carried is not None:
            carried_path, carried_body = carried
            body = f"{carried_body}\n\n## {path}\n{body}"
            path = carried_path or path
            carried = None

        if len(body) < MIN_CHARS:
            if merged:
                previous_path, previous_body = merged[-1]
                merged[-1] = (previous_path, f"{previous_body}\n\n## {path}\n{body}")
            else:
                carried = (path, body)
            continue

        merged.append((path, body))

    # A document consisting solely of short sections still has to produce one.
    if carried is not None:
        merged.append(carried)

    return _emit(document, merged)


def _emit(document: Document, sections: list[tuple[str, str]]) -> list[Chunk]:
    chunks: list[Chunk] = []

    for path, body in sections:
        pieces = _split_long_section(body) if len(body) > TARGET_CHARS else [body]
        for piece in pieces:
            # The heading path is prepended to the embedded text. Without it, a
            # chunk reading "1. Retrieve the last 50 cycles..." embeds with no
            # indication that it belongs to an HPC trend check.
            content = f"{path}\n\n{piece}".strip() if path else piece
            index = len(chunks)
            digest = hashlib.sha256(
                f"{document.doc_id}:{index}:{content[:80]}".encode()
            ).hexdigest()[:16]

            chunks.append(
                Chunk(
                    chunk_id=f"{document.doc_id}-{index:03d}-{digest[:8]}",
                    doc_id=document.doc_id,
                    title=document.title,
                    source_type=document.source_type,
                    section_path=path or document.title,
                    content=content,
                    index=index,
                    # Codes are extracted from the heading path as well as the
                    # body. A task's own identifier lives in its heading, so
                    # body-only extraction left every procedure tagged with the
                    # codes it *references* but not the one it *is* -- searching
                    # for 72-31-00-700-804 returned the borescope task instead
                    # of the water wash it names.
                    task_codes=tuple(sorted(set(TASK_CODE.findall(f"{path}\n{piece}")))),
                )
            )

    return chunks


def chunk_corpus(root: Path) -> tuple[list[Document], list[Chunk]]:
    """Load and chunk an entire corpus directory."""
    documents = load_corpus(root)
    chunks = [chunk for document in documents for chunk in chunk_document(document)]
    return documents, chunks
