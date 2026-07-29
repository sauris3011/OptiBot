"""Layer 3 — retrieval over the policy corpus.

Markdown policies are chunked on heading boundaries (so a chunk is a coherent
rule rather than an arbitrary window), embedded once at startup, then retrieved
by cosine similarity and re-ranked before injection.

The vector store is a numpy matrix rather than a separate database. The corpus
is ~50 chunks; an external vector DB would add a dependency and a failure mode
without changing a single retrieval result at this scale.
"""

from __future__ import annotations

import re
import threading

import numpy as np

from app.config import POLICY_DIR, settings
from app.services.embeddings import embed, embed_one

_index_lock = threading.Lock()
_chunks: list[dict] = []
_matrix: np.ndarray | None = None

# Rough token estimate; good enough for chunk sizing.
_CHARS_PER_TOKEN = 4


def _split_markdown(text: str, source: str) -> list[dict]:
    """Split on ## headings, then subdivide anything over the chunk budget."""
    max_chars = settings.rag_chunk_tokens * _CHARS_PER_TOKEN
    overlap_chars = settings.rag_chunk_overlap * _CHARS_PER_TOKEN

    doc_title = ""
    title_match = re.match(r"^#\s+(.+)$", text.strip(), re.MULTILINE)
    if title_match:
        doc_title = title_match.group(1).strip()

    sections: list[tuple[str, str]] = []
    parts = re.split(r"^##\s+(.+)$", text, flags=re.MULTILINE)
    if len(parts) == 1:
        sections.append((doc_title, text.strip()))
    else:
        # parts = [preamble, heading1, body1, heading2, body2, ...]
        preamble = parts[0].strip()
        if preamble and not preamble.lstrip().startswith("#"):
            sections.append((doc_title, preamble))
        for heading, body in zip(parts[1::2], parts[2::2]):
            sections.append((heading.strip(), body.strip()))

    out: list[dict] = []
    for heading, body in sections:
        if not body:
            continue
        # Prefixing the heading keeps the chunk self-describing once it is
        # detached from its document.
        full = f"{doc_title} — {heading}\n{body}" if heading else body
        if len(full) <= max_chars:
            out.append({"source": source, "heading": heading, "text": full})
            continue
        start = 0
        while start < len(full):
            piece = full[start : start + max_chars]
            out.append({"source": source, "heading": heading, "text": piece.strip()})
            if start + max_chars >= len(full):
                break
            start += max_chars - overlap_chars
    return out


def build_index(force: bool = False) -> int:
    """Chunk, embed, and cache the policy corpus. Idempotent."""
    global _chunks, _matrix
    with _index_lock:
        if _matrix is not None and not force:
            return len(_chunks)

        if not POLICY_DIR.exists():
            raise FileNotFoundError(f"Policy directory not found: {POLICY_DIR}")

        collected: list[dict] = []
        for path in sorted(POLICY_DIR.glob("*.md")):
            collected.extend(
                _split_markdown(path.read_text(encoding="utf-8"), path.name)
            )

        if not collected:
            raise RuntimeError(f"No policy chunks produced from {POLICY_DIR}")

        _chunks = collected
        _matrix = embed([c["text"] for c in collected])
        return len(_chunks)


def _rerank(query: str, candidates: list[dict]) -> list[dict]:
    """Cheap lexical re-rank on top of the vector recall.

    Pure cosine over a small corpus tends to return three chunks from the same
    document. Boosting exact term overlap and penalising duplicate sources
    lifts the chunk that actually answers the question into first place.
    """
    q_terms = {t for t in re.findall(r"[a-z]{4,}", query.lower())}
    seen_sources: set[str] = set()
    for cand in candidates:
        text_terms = set(re.findall(r"[a-z]{4,}", cand["text"].lower()))
        overlap = len(q_terms & text_terms) / max(len(q_terms), 1)
        penalty = 0.05 if cand["source"] in seen_sources else 0.0
        seen_sources.add(cand["source"])
        cand["rerank_score"] = round(cand["score"] + 0.25 * overlap - penalty, 4)
    return sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)


def retrieve(query: str, top_k: int | None = None) -> list[dict]:
    """Return the top-k policy chunks above the relevance floor."""
    if _matrix is None:
        build_index()
    assert _matrix is not None

    k = top_k or settings.rag_top_k
    q_vec = embed_one(query)
    scores = _matrix @ q_vec  # both sides are L2-normalised

    # Pull a wider candidate set so the re-ranker has something to work with.
    pool = min(len(_chunks), max(k * 3, 8))
    top_idx = np.argpartition(-scores, pool - 1)[:pool]

    candidates = [
        {
            "source": _chunks[i]["source"],
            "heading": _chunks[i]["heading"],
            "text": _chunks[i]["text"],
            "score": round(float(scores[i]), 4),
        }
        for i in top_idx
        if float(scores[i]) >= settings.rag_min_score
    ]
    if not candidates:
        return []
    return _rerank(query, candidates)[:k]


def index_stats() -> dict:
    if _matrix is None:
        build_index()
    assert _matrix is not None
    sources: dict[str, int] = {}
    for chunk in _chunks:
        sources[chunk["source"]] = sources.get(chunk["source"], 0) + 1
    return {
        "chunks": len(_chunks),
        "dimensions": int(_matrix.shape[1]),
        "sources": sources,
    }
