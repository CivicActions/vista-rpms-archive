# Qdrant Point Payload Contract

**Date**: 2025-02-25 | **Branch**: `004-docling-chunking`

Defines the JSON payload stored in each Qdrant point. Must remain backward-compatible with `mcp-server-qdrant`, which reads `document` and `metadata` top-level keys.

## Payload Schema (Document Chunks)

```json
{
  "document": "<heading context>\n\n<chunk body text>",
  "metadata": {
    "source_path": "source/www.va.gov/vdl/documents/Clinical/CPRS/file.pdf",
    "source_url": "https://www.va.gov/vdl/documents/Clinical/CPRS/file.pdf",
    "content_hash": "sha256:abc123...",
    "chunk_index": 0,
    "total_chunks": 12,
    "original_format": ".pdf",
    "file_size": 1048576,
    "cache_path": "cache/www.va.gov/vdl/documents/Clinical/CPRS/file.pdf.md",
    "chunker": "docling-hybrid",
    "headings": ["Chapter 3", "Section 3.1", "Configuration"],
    "page": 42,
    "doc_items": ["table", "paragraph"],
    "collection": "vista-vdl"
  }
}
```

## Payload Schema (Source Code Chunks)

```json
{
  "document": "ROUTINE_NAME ; Routine description\n ..source code..",
  "metadata": {
    "source_path": "source/WorldVistA/VistA-M/Packages/Kernel/Routines/XUS.m",
    "source_url": "https://github.com/WorldVistA/VistA-M/blob/HEAD/Packages/Kernel/Routines/XUS.m",
    "content_hash": "sha256:def456...",
    "chunk_index": 2,
    "total_chunks": 8,
    "original_format": ".m",
    "file_size": 4096,
    "cache_path": "cache/WorldVistA/VistA-M/Packages/Kernel/Routines/XUS.m.md",
    "chunker": "mumps-label",
    "language": "MUMPS",
    "routine_name": "EN",
    "is_header": false,
    "line_start": 15,
    "line_end": 42,
    "collection": "vista-code"
  }
}
```

## Payload Schema (Header Chunk for MUMPS Files)

```json
{
  "document": "XUS ;ISC-SF/RAM - Kernel Signon/Security Utilities ;2024-01-15\n ;;8.0;KERNEL;;...",
  "metadata": {
    "source_path": "source/WorldVistA/VistA-M/Packages/Kernel/Routines/XUS.m",
    "source_url": "https://github.com/WorldVistA/VistA-M/blob/HEAD/Packages/Kernel/Routines/XUS.m",
    "content_hash": "sha256:def456...",
    "chunk_index": 0,
    "total_chunks": 8,
    "original_format": ".m",
    "file_size": 4096,
    "cache_path": "cache/WorldVistA/VistA-M/Packages/Kernel/Routines/XUS.m.md",
    "chunker": "mumps-label",
    "language": "MUMPS",
    "routine_name": "_header",
    "is_header": true,
    "line_start": 1,
    "line_end": 14,
    "collection": "vista-code"
  }
}
```

## Field Descriptions

### Top-level (required by mcp-server-qdrant)

| Field | Type | Description |
|-------|------|-------------|
| `document` | `str` | Text for display in MCP results. For documents: contextualized chunk with heading breadcrumb. For source: raw code of the routine/chunk. |
| `metadata` | `dict` | All additional key-value pairs. Serialized to JSON in MCP `<metadata>` tag. |

### Metadata (common)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_path` | `str` | Yes | GCS source path (used as filter key for re-indexing) |
| `source_url` | `str` | Yes | Reconstructed original web URL |
| `content_hash` | `str` | Yes | `sha256:{hex}` of full source content |
| `chunk_index` | `int` | Yes | 0-based index within the document |
| `total_chunks` | `int` | Yes | Total chunks for this document |
| `original_format` | `str` | Yes | File extension (e.g., `.pdf`, `.m`) |
| `file_size` | `int` | Yes | Original file size in bytes |
| `cache_path` | `str` | Yes | GCS cache path |
| `chunker` | `str` | Yes | Chunking strategy used: `docling-hybrid`, `mumps-label`, `text-fallback` |
| `collection` | `str` | Yes | Collection name (for cross-reference) |

### Metadata (document-specific)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `headings` | `list[str]` | No | Heading hierarchy from section root to chunk |
| `page` | `int` | No | Page number if available from source format |
| `doc_items` | `list[str]` | No | Docling item types in this chunk (e.g., `table`, `paragraph`) |

### Metadata (source code-specific)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `language` | `str` | Yes | Programming language (`MUMPS`) |
| `routine_name` | `str` | Yes | Label/routine name or `_header` |
| `is_header` | `bool` | Yes | Whether this is the file header chunk |
| `line_start` | `int` | Yes | 1-based start line in original file |
| `line_end` | `int` | Yes | 1-based end line (inclusive) in original file |

## Point ID Convention

- Chunk 0 uses the base `point_id` derived from `uuid5(NAMESPACE, source_path)`
- Chunk N (N > 0) uses `uuid5(base_point_id, str(N))`
- This matches the existing convention in `qdrant_client.py`

## Backward Compatibility

- `document` field continues to hold human-readable chunk text
- `metadata` field continues to hold a flat dict of key-value pairs
- mcp-server-qdrant reads `payload["document"]` for `<content>` and `json.dumps(payload["metadata"])` for `<metadata>` — no changes needed
- New metadata fields are additive only; no existing fields are removed
