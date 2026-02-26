# Data Model: Optimal Docling Chunking for Qdrant

**Date**: 2025-02-25 | **Branch**: `004-docling-chunking`

## Entities

### ChunkResult

Represents a single chunk produced by either the Docling HybridChunker (for documents) or the MUMPS code chunker (for source files). This is the intermediate data structure passed from chunking to indexing.

| Field | Type | Description |
|-------|------|-------------|
| `text` | `str` | The chunk text content for storage in Qdrant `document` field |
| `embedding_text` | `str` | The contextualized text for embedding (includes heading context for documents, label context for code) |
| `chunk_index` | `int` | 0-based position of this chunk within the document |
| `total_chunks` | `int` | Total number of chunks produced from this document |
| `source_path` | `str` | GCS source path (e.g., `source/www.va.gov/vdl/doc.pdf`) |
| `source_url` | `str` | Reconstructed original web URL |
| `content_hash` | `str` | SHA256 hash of the full document content (for skip detection) |
| `metadata` | `dict` | Additional metadata (varies by chunk type — see below) |

### Document Chunk Metadata (office documents via Docling)

Stored in `ChunkResult.metadata` for office document chunks:

| Field | Type | Description |
|-------|------|-------------|
| `headings` | `list[str] \| None` | Heading hierarchy from Docling (e.g., `["Chapter 1", "Section 1.2"]`) |
| `page` | `int \| None` | Page number (when available from Docling doc_items) |
| `doc_items` | `list[dict]` | Serialized Docling doc_items for this chunk |
| `origin` | `dict \| None` | Docling DocumentOrigin metadata |

### Source Code Chunk Metadata (MUMPS files)

Stored in `ChunkResult.metadata` for MUMPS source code chunks:

| Field | Type | Description |
|-------|------|-------------|
| `language` | `str` | Always `"mumps"` |
| `routine_name` | `str \| None` | MUMPS label/routine name (e.g., `"EN"`, `"INIT"`), or `None` for file header chunks |
| `is_header` | `bool` | `True` if this chunk is the file-level header (before the first label) |
| `line_start` | `int` | 1-based starting line number in the source file |
| `line_end` | `int` | 1-based ending line number (inclusive) |

## Qdrant Point Payload Schema

The Qdrant payload structure is backward-compatible with mcp-server-qdrant. New fields are added to the existing `metadata` dict.

```json
{
  "document": "<chunk text>",
  "metadata": {
    "source_path": "source/www.va.gov/vdl/doc.pdf",
    "source_url": "https://www.va.gov/vdl/doc.pdf",
    "content_hash": "a1b2c3d4e5f6...",
    "indexed_at": "2025-02-25T12:00:00+00:00",
    "file_size": 1048576,
    "original_format": ".pdf",
    "cache_path": "cache/www.va.gov/vdl/doc.pdf.md",
    "chunk_index": 0,
    "total_chunks": 15,
    "headings": ["Chapter 1", "Section 1.2"],
    "page": 5,
    "line_start": null,
    "line_end": null,
    "routine_name": null,
    "language": null
  },
  "content_hash": "a1b2c3d4e5f6..."
}
```

**Source code variant** (MUMPS):
```json
{
  "document": "<chunk text>",
  "metadata": {
    "source_path": "source/WorldVistA/VistA-M/Packages/Kernel/Routines/XUS.m",
    "source_url": "https://github.com/WorldVistA/VistA-M/blob/HEAD/Packages/Kernel/Routines/XUS.m",
    "content_hash": "b2c3d4e5f6a1...",
    "indexed_at": "2025-02-25T12:00:00+00:00",
    "file_size": 8192,
    "original_format": ".m",
    "cache_path": null,
    "chunk_index": 2,
    "total_chunks": 8,
    "headings": null,
    "page": null,
    "line_start": 25,
    "line_end": 48,
    "routine_name": "EN",
    "language": "mumps"
  },
  "content_hash": "b2c3d4e5f6a1..."
}
```

## GCS Cache Layout

```
gs://{bucket}/cache/{path}.md          # Markdown export (existing, retained)
gs://{bucket}/cache/{path}.docling.json # NEW: Serialized DoclingDocument JSON
```

Example:
```
gs://vista-rpms-archive/cache/www.va.gov/vdl/doc.pdf.md
gs://vista-rpms-archive/cache/www.va.gov/vdl/doc.pdf.docling.json
```

Note: Source code files (`.m`) have no cache entry — they are chunked directly from the source text.

## State Transitions

### Document Processing Flow

```
GCS Blob → Download → Classify
  ├─ Office Document → Extract (Docling) → DoclingDocument
  │   ├─ Cache DoclingDocument JSON to GCS (new)
  │   ├─ Cache Markdown to GCS (existing)
  │   └─ HybridChunker.chunk(doc) → [ChunkResult] → Embed → Upsert
  ├─ Source Code → Decode UTF-8
  │   └─ MumpsChunker.chunk(text) → [ChunkResult] → Embed → Upsert
  └─ Binary/Archive → (existing handling, unchanged)
```

### Re-indexing Flow (--force)

```
For each document:
  1. Filter-delete: query Qdrant by metadata.source_path → delete all matching points
  2. Process document through normal chunking pipeline
  3. Upsert all new chunks
```

## Validation Rules

- `chunk_index` MUST be 0-based and sequential within a document.
- `total_chunks` MUST equal the actual number of chunks produced.
- `content_hash` MUST be computed over the full document content (not individual chunks).
- `source_url` MUST be a valid URL (starts with `https://`).
- No chunk's `embedding_text` may exceed 512 tokens (enforced by the tokenizer).
- `line_start` and `line_end` MUST be 1-based and `line_start <= line_end`.
