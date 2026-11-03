"""Split a markdown document into retrievable chunks that keep their heading context.

Sections are the natural unit of meaning here, so chunks follow headings; long sections are packed
paragraph by paragraph, and an over-long paragraph is split on sentence ends. Each chunk is
embedded together with its heading path, so a chunk about "Budget" under "Campaign health" is
found by questions that mention either.
"""

import re
from dataclasses import dataclass

MAX_CHARS = 900
_HEADING = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    heading: str
    content: str

    @property
    def embedding_text(self) -> str:
        return f"{self.heading}\n{self.content}" if self.heading else self.content


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        if current and len(current) + 1 + len(sentence) > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    # A single sentence can still be too long (no punctuation): hard-wrap it.
    out: list[str] = []
    for piece in pieces:
        out += [piece[i : i + max_chars] for i in range(0, len(piece), max_chars)] or [piece]
    return out


def _pack(paragraphs: list[str], max_chars: int) -> list[str]:
    packed: list[str] = []
    current = ""
    for paragraph in paragraphs:
        for piece in _split_long(paragraph, max_chars):
            if current and len(current) + 2 + len(piece) > max_chars:
                packed.append(current)
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        packed.append(current)
    return packed


def chunk_markdown(text: str, *, max_chars: int = MAX_CHARS) -> list[Chunk]:
    if max_chars < 100:
        raise ValueError("max_chars is too small to hold a meaningful chunk")
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] = []
    heading_path = ""

    def flush() -> None:
        body = "\n".join(current).strip()
        if body:
            sections.append(
                (heading_path, [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()])
            )
        current.clear()

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            heading_path = " > ".join(t for _, t in stack)
        else:
            current.append(line)
    flush()

    chunks: list[Chunk] = []
    for heading, paragraphs in sections:
        for content in _pack(paragraphs, max_chars):
            chunks.append(Chunk(len(chunks), heading, content))
    return chunks
