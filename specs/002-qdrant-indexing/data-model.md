# Data Model: Qdrant Vector Database Indexing

**Feature**: 002-qdrant-indexing  
**Date**: 2026-01-17

## Entities

### QdrantPoint

Represents a document indexed in Qdrant.

| Field | Type | Description |
|-------|------|-------------|
| id | int | Deterministic hash of source_path (MD5 truncated to 64-bit) |
| vector | list[float] | 384-dimensional embedding from all-MiniLM-L6-v2 |
| payload | dict | Document metadata (see below) |

### Payload Schema

Stored alongside each vector in Qdrant.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| source_path | str | Yes | GCS blob path (e.g., "www.va.gov/vdl/documents/...") |
| content_hash | str | Yes | SHA256 hash of markdown content (first 32 chars) |
| indexed_at | str | Yes | ISO 8601 timestamp of indexing |
| file_size | int | Yes | Original file size in bytes |
| original_format | str | Yes | Original file extension (.pdf, .html, etc.) |
| cache_path | str | Yes | GCS path to cached markdown |

### Collection

Qdrant collection configuration.

| Property | Value |
|----------|-------|
| Vector Size | 384 |
| Distance Metric | Cosine |
| Collections | "vista" (default), "rpms" (IHS documents) |

### RoutingRule

Configuration for routing documents to collections.

| Field | Type | Description |
|-------|------|-------------|
| pattern | str | Substring to match in source_path |
| collection | str | Target collection name |

### IndexingResult

Outcome of indexing a single document.

| Field | Type | Description |
|-------|------|-------------|
| source_path | str | Document source path |
| collection | str | Target collection |
| status | str | "indexed", "skipped", "failed" |
| error | str | None | Error message if failed |

## Relationships

```
IndexEntry (from extraction) 
    │
    ▼ (after conversion)
ExtractionResult.cache_path → read markdown content
    │
    ▼ (embed + route)
QdrantPoint → stored in Collection ("vista" or "rpms")
```

## State Transitions

```
Document States:
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Not in     │────▶│  Indexed    │────▶│  Updated    │
│  Qdrant     │     │  (current)  │     │  (re-index) │
└─────────────┘     └─────────────┘     └─────────────┘
       ▲                   │
       │                   ▼
       │            ┌─────────────┐
       └────────────│  Skipped    │
                    │  (cached)   │
                    └─────────────┘

Transition Logic:
- Not in Qdrant → Indexed: New document, generate embedding, upsert
- Indexed → Skipped: Same content_hash, skip indexing
- Indexed → Updated: Different content_hash, regenerate embedding, upsert
```

## Validation Rules

1. **source_path**: Must be non-empty, valid GCS path format
2. **content_hash**: Must be 32-character hex string
3. **vector**: Must be exactly 384 floats, normalized
4. **collection**: Must be one of configured collection names
