# Implementation Plan: Optimal Docling Chunking for Qdrant

**Branch**: `004-docling-chunking` | **Date**: 2025-02-25 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-docling-chunking/spec.md`

## Summary

Replace the current naive character-based chunking (4MB chunks with 2KB overlap) with Docling's `HybridChunker` for structure-aware, token-aligned chunking of office documents, and a custom MUMPS code chunker for source files. Add rich metadata (source URLs, page/line references, heading hierarchy) to Qdrant payloads. Cache serialized `DoclingDocument` JSON alongside markdown for chunker reuse without re-conversion.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: docling>=2.0.0, docling-core (HybridChunker), qdrant-client[fastembed]>=1.12.0, google-cloud-storage>=2.0.0, psutil>=5.9.0, transformers (for tokenizer alignment)
**Storage**: GCS (source + cache buckets), Qdrant (vector DB, named vectors)
**Testing**: Manual integration testing (no automated test suite currently; only `test_qdrant.py` for ad-hoc search timing)
**Target Platform**: Linux server (GCP)
**Project Type**: Single project — CLI data pipeline
**Performance Goals**: Process ~50k+ files with parallel workers (14 threads), chunking must not add significant overhead vs. current character splitting
**Constraints**: <16GB server RAM (OOM observed at ~6200 files), 600s Qdrant timeout, 32MB max payload per point
**Scale/Scope**: ~80k+ files across 4 Qdrant collections (vista, vista-source, rpms, rpms-source)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Reliability First | PASS | Chunking change is within existing idempotent pipeline; `--force` re-indexing uses filter-delete-then-insert for clean orphan cleanup; resume still works via Qdrant skip detection on chunk-0 point ID |
| II. Observability | PASS | Existing per-file logging covers chunking; chunk counts logged; no new opaque operations introduced |
| III. Simplicity | PASS | Uses Docling's built-in `HybridChunker` (no custom framework); MUMPS chunker is a single-file module with regex-based label splitting; `transformers` added for tokenizer only |
| IV. Lazy Processing | PASS | DoclingDocument JSON cached to GCS alongside markdown; HybridChunker operates on cached document; no redundant re-conversion on reruns |
| Error Handling | PASS | Fallback to token-window splitting when Docling structural parsing fails; per-file error handling unchanged |

**Gate Result**: PASS — no violations. Proceed to Phase 0.

### Post-Phase 1 Re-evaluation

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Reliability First | PASS | `delete_by_source_path()` + batch upsert provides atomic-enough re-indexing per document; `chunk_text_fallback()` ensures every file produces at least one chunk even if structural parsing fails; point ID convention unchanged so resume via chunk-0 detection still works |
| II. Observability | PASS | Contracts specify `total_chunks` and `chunk_index` in metadata — logged per-file. Chunker type (`docling-hybrid`, `mumps-label`, `text-fallback`) tracked in metadata for post-hoc analysis |
| III. Simplicity | PASS | Two new modules (`chunker.py`, `url_resolver.py`) are single-file, no classes needed for `url_resolver`. `docling-core[chunking]` brings `transformers` as transitive dep but this is for tokenizer alignment only — no model inference. MUMPS chunker is ~50 lines of regex |
| IV. Lazy Processing | PASS | DoclingDocument JSON cached to `cache/{path}.docling.json` alongside existing markdown cache. `download_docling_json()` checked before re-extraction. No writes to source directory |
| Error Handling | PASS | Per-file error handling preserved in pipeline. Fallback path from structural → token-window chunking adds resilience. Failed files still logged and skipped |

**Post-Design Gate**: PASS — all principles maintained through Phase 1 design.

## Project Structure

### Documentation (this feature)

```text
specs/004-docling-chunking/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
src/
├── __init__.py
├── chunker.py              # NEW — Docling HybridChunker wrapper + MUMPS code chunker + fallback
├── url_resolver.py         # NEW — Reconstruct original source URLs from GCS paths
├── extractor.py            # MODIFIED — Return DoclingDocument object; cache JSON serialization
├── qdrant_client.py        # MODIFIED — Remove chunk_text(); accept pre-chunked data with metadata
├── pipeline_gcs.py         # MODIFIED — Wire chunker between extraction and indexing
├── embedder.py             # UNCHANGED
├── router.py               # UNCHANGED
├── types.py                # MODIFIED — Add ChunkResult dataclass, update IndexingResult
├── config.py               # UNCHANGED (token limit added to config.toml, read via existing config loader)
├── gcs_client.py           # MINOR — Add DoclingDocument JSON upload/download methods
├── file_classifier.py      # UNCHANGED
├── archive_extractor.py    # UNCHANGED
└── logging_config.py       # UNCHANGED

tests/                      # No changes (no automated tests currently)
```

**Structure Decision**: Single project layout preserved. Two new modules (`chunker.py`, `url_resolver.py`) added at the existing `src/` level. No new directories or packages created.
