"""Chunking module for documents and source code.

Provides three chunking strategies:
- chunk_document(): Structure-aware chunking via Docling HybridChunker
- chunk_source_code(): MUMPS label-boundary chunking
- chunk_text_fallback(): Token-window splitting at line boundaries
"""

import logging
import re

from docling_core.transforms.chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from docling_core.types import DoclingDocument

from .types import ChunkResult

logger = logging.getLogger(__name__)

# Embedding model name and token limit
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_MAX_TOKENS = 512

# MUMPS label regex: labels start in column 1, optional % prefix,
# then letter + alphanumeric, optional formal parameters.
_MUMPS_LABEL_RE = re.compile(
    r"^(%?[A-Za-z][A-Za-z0-9]*)(?:\(([^)]*)\))?(?=[\s;(]|$)",
    re.MULTILINE,
)

# Lazy-loaded tokenizer singleton (thread-safe after first creation)
_tokenizer: HuggingFaceTokenizer | None = None


def _get_tokenizer() -> HuggingFaceTokenizer:
    """Get or create the shared HuggingFace tokenizer instance."""
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = HuggingFaceTokenizer.from_pretrained(
            model_name=_MODEL_NAME,
            max_tokens=_MAX_TOKENS,
        )
    return _tokenizer


def _count_tokens(text: str) -> int:
    """Count tokens for a given text using the embedding model's tokenizer."""
    tokenizer = _get_tokenizer()
    return tokenizer.count_tokens(text)


# ---------------------------------------------------------------------------
# chunk_text_fallback
# ---------------------------------------------------------------------------

def chunk_text_fallback(
    text: str,
    source_path: str,
    source_url: str,
    content_hash: str,
) -> list[ChunkResult]:
    """Token-window splitting fallback for when structural chunking fails.

    Splits at line boundaries, respecting the embedding model's token limit.

    Args:
        text: Raw text content.
        source_path: GCS source path.
        source_url: Reconstructed original web URL.
        content_hash: SHA256 hash of full content.

    Returns:
        List of ChunkResult, one per token-window chunk.
    """
    if not text.strip():
        return []

    lines = text.split("\n")
    chunks: list[str] = []
    current_lines: list[str] = []
    current_tokens = 0

    for line in lines:
        line_tokens = _count_tokens(line)
        # If a single line exceeds the limit, force-add it as its own chunk
        if line_tokens >= _MAX_TOKENS:
            if current_lines:
                chunks.append("\n".join(current_lines))
                current_lines = []
                current_tokens = 0
            chunks.append(line)
            continue

        if current_tokens + line_tokens + 1 > _MAX_TOKENS and current_lines:
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_tokens = 0

        current_lines.append(line)
        current_tokens += line_tokens + 1  # +1 for newline token

    if current_lines:
        chunks.append("\n".join(current_lines))

    total = len(chunks)
    results = []
    for idx, chunk_text in enumerate(chunks):
        results.append(
            ChunkResult(
                text=chunk_text,
                embedding_text=chunk_text,
                chunk_index=idx,
                total_chunks=total,
                source_path=source_path,
                source_url=source_url,
                content_hash=content_hash,
                metadata={"chunker": "text-fallback"},
            )
        )
    return results


# ---------------------------------------------------------------------------
# chunk_document
# ---------------------------------------------------------------------------

def chunk_document(
    doc: DoclingDocument,
    source_path: str,
    source_url: str,
    content_hash: str,
) -> list[ChunkResult]:
    """Chunk a DoclingDocument using Docling's HybridChunker.

    Uses structure-aware chunking that respects heading/section boundaries
    and merges small peer chunks. Falls back to chunk_text_fallback on
    error or zero chunks.

    Args:
        doc: DoclingDocument object (from extraction or cache).
        source_path: GCS source path.
        source_url: Reconstructed original web URL.
        content_hash: SHA256 hash of full content.

    Returns:
        List of ChunkResult with heading context in embedding_text.
    """
    try:
        tokenizer = _get_tokenizer()
        chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)

        raw_chunks = list(chunker.chunk(doc))

        if not raw_chunks:
            logger.warning(
                f"[{source_path}] HybridChunker produced 0 chunks, "
                "falling back to text-fallback"
            )
            markdown = doc.export_to_markdown()
            return chunk_text_fallback(markdown, source_path, source_url, content_hash)

        total = len(raw_chunks)
        results: list[ChunkResult] = []

        for idx, chunk in enumerate(raw_chunks):
            # Display text: the raw chunk text
            display_text = chunker.contextualize(chunk)
            # Embedding text: contextualized with heading breadcrumb
            embedding_text = display_text

            # Extract metadata from Docling chunk
            meta_dict = chunk.meta.export_json_dict() if chunk.meta else {}
            headings = meta_dict.get("headings")
            doc_items = meta_dict.get("doc_items")

            # Try to extract page number from doc_items
            page = None
            if doc_items:
                for item in doc_items:
                    prov = item.get("prov") if isinstance(item, dict) else None
                    if prov and isinstance(prov, list):
                        for p in prov:
                            if isinstance(p, dict) and "page_no" in p:
                                page = p["page_no"]
                                break
                    if page is not None:
                        break

            metadata = {
                "chunker": "docling-hybrid",
                "headings": headings,
                "page": page,
                "doc_items": [
                    item.get("self_ref", str(item)) if isinstance(item, dict) else str(item)
                    for item in (doc_items or [])
                ],
            }

            results.append(
                ChunkResult(
                    text=display_text,
                    embedding_text=embedding_text,
                    chunk_index=idx,
                    total_chunks=total,
                    source_path=source_path,
                    source_url=source_url,
                    content_hash=content_hash,
                    metadata=metadata,
                )
            )

        logger.info(f"[{source_path}] Chunked into {total} chunks via HybridChunker")
        return results

    except Exception as e:
        logger.warning(
            f"[{source_path}] HybridChunker failed: {e}, "
            "falling back to text-fallback"
        )
        try:
            markdown = doc.export_to_markdown()
        except Exception:
            markdown = ""
        return chunk_text_fallback(markdown, source_path, source_url, content_hash)


# ---------------------------------------------------------------------------
# chunk_source_code
# ---------------------------------------------------------------------------

def chunk_source_code(
    text: str,
    source_path: str,
    source_url: str,
    content_hash: str,
) -> list[ChunkResult]:
    """Chunk MUMPS source code at label/routine boundaries.

    File-level header content (before the first label) is emitted as its
    own separate chunk. Each label + its indented body forms one chunk.
    Oversized routines are split at blank-line or comment boundaries.
    Falls back to chunk_text_fallback if no labels are found.

    Args:
        text: Raw source code text (UTF-8 decoded).
        source_path: GCS source path.
        source_url: Reconstructed original web URL.
        content_hash: SHA256 hash of full content.

    Returns:
        List of ChunkResult with line ranges in metadata.
    """
    if not text.strip():
        return []

    lines = text.split("\n")
    matches = list(_MUMPS_LABEL_RE.finditer(text))

    if not matches:
        logger.debug(f"[{source_path}] No MUMPS labels found, using text-fallback")
        return chunk_text_fallback(text, source_path, source_url, content_hash)

    # Build (label_name, start_line_0based) pairs from regex matches
    label_positions: list[tuple[str, int]] = []
    for m in matches:
        # Convert character offset to line number (0-based)
        line_no = text[:m.start()].count("\n")
        label_positions.append((m.group(1), line_no))

    # Build sections: list of (name, start_line, end_line) — 0-based inclusive
    sections: list[tuple[str, int, int]] = []

    # Header chunk: lines before first label
    first_label_line = label_positions[0][1]
    if first_label_line > 0:
        sections.append(("_header", 0, first_label_line - 1))

    # Label chunks
    for i, (name, start) in enumerate(label_positions):
        if i + 1 < len(label_positions):
            end = label_positions[i + 1][1] - 1
        else:
            end = len(lines) - 1
        # Trim trailing empty lines
        while end > start and not lines[end].strip():
            end -= 1
        sections.append((name, start, end))

    # Build ChunkResults, splitting oversized sections
    raw_chunks: list[tuple[str, str, int, int, bool]] = []  # (name, text, start, end, is_header)

    for name, start, end in sections:
        section_text = "\n".join(lines[start : end + 1])
        is_header = name == "_header"

        if _count_tokens(section_text) <= _MAX_TOKENS:
            raw_chunks.append((name, section_text, start, end, is_header))
        else:
            # Split oversized section at blank-line or comment boundaries
            sub_chunks = _split_oversized_section(lines, start, end, name)
            for sub_text, sub_start, sub_end in sub_chunks:
                raw_chunks.append((name, sub_text, sub_start, sub_end, is_header))

    total = len(raw_chunks)
    results: list[ChunkResult] = []

    for idx, (routine_name, chunk_text, line_start, line_end, is_header) in enumerate(raw_chunks):
        metadata = {
            "chunker": "mumps-label",
            "language": "MUMPS",
            "routine_name": routine_name,
            "is_header": is_header,
            "line_start": line_start + 1,  # Convert to 1-based
            "line_end": line_end + 1,  # Convert to 1-based
        }
        results.append(
            ChunkResult(
                text=chunk_text,
                embedding_text=chunk_text,
                chunk_index=idx,
                total_chunks=total,
                source_path=source_path,
                source_url=source_url,
                content_hash=content_hash,
                metadata=metadata,
            )
        )

    logger.info(f"[{source_path}] Chunked into {total} MUMPS chunks")
    return results


def _split_oversized_section(
    lines: list[str],
    start: int,
    end: int,
    label_name: str,
) -> list[tuple[str, int, int]]:
    """Split an oversized MUMPS section at blank-line or comment boundaries.

    Returns list of (text, start_line_0based, end_line_0based).
    """
    result: list[tuple[str, int, int]] = []
    current_start = start
    current_lines: list[str] = []
    current_tokens = 0

    for i in range(start, end + 1):
        line = lines[i]
        line_tokens = _count_tokens(line)

        # Check if adding this line would exceed limit
        if current_tokens + line_tokens + 1 > _MAX_TOKENS and current_lines:
            # Try to break at a blank line or comment boundary
            text = "\n".join(current_lines)
            result.append((text, current_start, i - 1))
            current_start = i
            current_lines = [line]
            current_tokens = line_tokens
        else:
            current_lines.append(line)
            current_tokens += line_tokens + 1

    if current_lines:
        text = "\n".join(current_lines)
        result.append((text, current_start, end))

    return result
