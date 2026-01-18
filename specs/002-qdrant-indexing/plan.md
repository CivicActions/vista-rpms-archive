# Implementation Plan: Qdrant Vector Database Indexing

**Branch**: `002-qdrant-indexing` | **Date**: 2026-01-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-qdrant-indexing/spec.md`

## Summary

Add Qdrant vector database indexing to the document extraction pipeline. Documents are indexed immediately after conversion to markdown, with configurable routing to multiple collections (default: "vista" for most files, "rpms" for IHS paths). Skip detection prevents re-indexing unchanged documents. Local development uses Docker. **Indexed data is compatible with mcp-server-qdrant for future MCP integration.**

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: qdrant-client[fastembed] (includes FastEmbed for local embeddings)  
**Embedding Model**: sentence-transformers/all-MiniLM-L6-v2 (384 dimensions, compatible with mcp-server-qdrant)  
**Storage**: Qdrant vector database (Docker for local, configurable URL for production)  
**Testing**: Manual testing with Docker-based Qdrant  
**Target Platform**: Linux/macOS server  
**Project Type**: Single project (extends existing extraction pipeline)  
**Performance Goals**: Index documents within 5 seconds of conversion; skip detection < 300ms per doc  
**Constraints**: Embedding model must run locally via ONNX (no external API calls)  
**Scale/Scope**: ~10,000 documents across 2 collections
**MCP Compatibility**: Indexed collections work with mcp-server-qdrant for semantic search

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status | Notes |
|-----------|-------------|--------|-------|
| **I. Reliability First** | Operations MUST be idempotent and resumable | ✅ PASS | Qdrant upserts are idempotent by design; skip detection prevents duplicates |
| **I. Reliability First** | Failed files MUST NOT block processing | ✅ PASS | FR-007 requires continuing after individual failures |
| **I. Reliability First** | Qdrant writes MUST use batch upserts with retry | ✅ PASS | Will implement with qdrant-client batch API |
| **II. Observability** | Progress reporting with files processed/remaining/errors | ✅ PASS | FR-011 requires indexing statistics in summary |
| **II. Observability** | Each file operation MUST log path, time, success/failure | ✅ PASS | FR-008 requires logging failures with details |
| **III. Simplicity** | Use concurrent.futures, simple CLI, minimal deps | ✅ PASS | Reuses existing pipeline threading; adds 1 dep (qdrant-client[fastembed]) |
| **IV. Lazy Processing** | Skip if cached/indexed already exists | ✅ PASS | FR-004 requires skip for already-indexed documents |

**GATE RESULT**: ✅ PASS - All constitution principles satisfied

### Post-Design Re-Check

| Principle | Design Decision | Compliant |
|-----------|-----------------|-----------|
| I. Reliability | Point ID from path hash enables upsert idempotency | ✅ |
| I. Reliability | Content hash in payload enables change detection | ✅ |
| II. Observability | IndexingResult tracks status per document | ✅ |
| III. Simplicity | Single dependency (qdrant-client[fastembed]) | ✅ |
| IV. Lazy Processing | Skip if point exists with same content_hash | ✅ |

## Project Structure

### Documentation (this feature)

```text
specs/002-qdrant-indexing/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (Qdrant schema)
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
src/
├── __init__.py
├── archive_extractor.py  # Existing
├── config.py             # Existing - add Qdrant config fields
├── extractor.py          # Existing
├── gcs_client.py         # Existing
├── index_loader.py       # Existing
├── pipeline.py           # Existing - add indexing hook
├── qdrant_client.py      # NEW - Qdrant connection/operations
├── embedder.py           # NEW - sentence-transformer embedding
├── router.py             # NEW - path-to-collection routing
└── types.py              # Existing - add IndexingResult type
```

**Structure Decision**: Single project structure - this feature extends the existing extraction pipeline with 3 new modules (qdrant_client, embedder, router) plus modifications to existing modules.

## Complexity Tracking

No violations - design follows all constitution principles.
