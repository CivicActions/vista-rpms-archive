# Implementation Plan: Document Extraction Pipeline

**Branch**: `001-doc-extraction` | **Date**: 2026-01-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-doc-extraction/spec.md`

## Summary

Build a Python script that extracts text content from office documents (PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX) stored in GCS into markdown files in a separate GCS cache directory. Uses docling for extraction with OCR disabled. Processes files in parallel, skips already-cached files, and filters by MIME type. Includes stub functions for future Qdrant loading.

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: docling, google-cloud-storage, tomli (config parsing)  
**Storage**: GCS (source bucket, cache bucket/prefix)  
**Testing**: pytest  
**Target Platform**: Linux server  
**Project Type**: single  
**Performance Goals**: Process 1000+ documents per hour with parallel workers  
**Constraints**: <2GB memory for 100 concurrent documents  
**Scale/Scope**: ~10k files in archive, ~1k office documents

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I. Reliability First | Idempotent, resumable, failures don't block | ✅ Compliant - uses index.json + cache check |
| II. Observability | Logs with progress, file-level logging | ✅ Compliant - FR-007 requires progress logging |
| III. Simplicity | concurrent.futures, single entry point | ✅ Compliant - uses standard library parallelism |
| IV. Lazy Processing | Cache to separate directory, skip if exists | ✅ Compliant - FR-005 requires cache skip |
| Error Handling | Log errors to file, continue processing | ✅ Compliant - FR-008 requires continuation |

**Gate Status**: ✅ PASS - All principles satisfied

## Project Structure

### Documentation (this feature)

```text
specs/001-doc-extraction/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (N/A - no API)
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
src/
├── config.py            # Configuration loading (TOML + CLI)
├── extractor.py         # Docling extraction wrapper
├── gcs_client.py        # GCS read/write operations
├── index_loader.py      # Load and filter index.json
├── pipeline.py          # Main processing pipeline
├── qdrant_stub.py       # Stub for future Qdrant loading
└── types.py             # Data classes (IndexEntry, ExtractionResult, Config)

tests/
├── unit/
│   ├── test_config.py
│   ├── test_extractor.py
│   └── test_index_loader.py
└── integration/
    └── test_pipeline.py

extract.py               # CLI entry point
config.example.toml      # Example configuration file
```

**Structure Decision**: Single project structure - this is a standalone data pipeline script, not a web app or mobile app.

## Complexity Tracking

> No violations - design follows all constitution principles.

## Post-Design Constitution Re-Check

*After Phase 1 design completion*

| Principle | Design Element | Verification |
|-----------|----------------|--------------|
| I. Reliability First | ThreadPoolExecutor with per-task try/except, cache check before processing | ✅ Errors isolated, resumable via re-run |
| II. Observability | ProgressTracker class, error log file, summary stats | ✅ Full visibility into progress and failures |
| III. Simplicity | Single ThreadPoolExecutor, TOML config, single extract.py entry point | ✅ Standard library, no custom frameworks |
| IV. Lazy Processing | blob.exists() check before download, .md files in separate cache prefix | ✅ Skip if cached, separate from source |
| Error Handling | extraction_errors.log with path/exception/timestamp, ProcessingResult.error field | ✅ Full context logged, processing continues |

**Post-Design Gate Status**: ✅ PASS - Design adheres to all constitution principles
