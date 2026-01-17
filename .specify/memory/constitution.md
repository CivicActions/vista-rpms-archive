<!--
SYNC IMPACT REPORT
==================
Version change: 1.1.0 → 1.2.0
Modified principles: 
  - "IV. Lazy Processing" → Output as markdown to cache directory (not source), removed Qdrant skip
Added sections: None
Removed sections: None
Templates requiring updates: ✅ No updates needed (templates are generic)
Follow-up TODOs: None
-->

# Vista RPMS Archive Constitution

## Core Principles

### I. Reliability First

All file processing operations MUST be idempotent and resumable.
- Failed files MUST NOT block processing of remaining files
- Processing state MUST be recoverable after crashes or interruptions
- Qdrant writes MUST use batch upserts with retry logic
- Each file's processing status MUST be tracked to enable resume

**Rationale**: Large directory processing can take hours; failures should not require full restarts.

### II. Observability

All operations MUST emit logs with measurable progress.
- Progress reporting MUST include: files processed, files remaining, errors encountered
- Each file operation MUST log: file path, processing time, success/failure, error details if failed
- Batch operations to Qdrant MUST log: batch size, vectors inserted, latency

**Rationale**: Server-side batch jobs require visibility into progress and failures for debugging and monitoring.

### III. Simplicity

Prefer standard library and minimal dependencies; avoid premature abstraction.
- Use `concurrent.futures` for parallelism (no custom threading)
- Configuration via environment variables or simple CLI args
- Single entry point (`main.py`) with clear processing pipeline

**Rationale**: Data pipelines should be easy to understand, debug, and maintain.

### IV. Lazy Processing

Cache intermediate extracted markdown in a dedicated cache directory alongside source files.
- Extracted content MUST be written as markdown to a cache directory (e.g., `gs://bucket/cache/path/file.pdf.md`)
- Cache directory MUST be separate from source directory (no writes to source)
- Processing MUST skip extraction if cached markdown already exists

**Rationale**: Text extraction is expensive; avoid redundant work on reruns.

## Error Handling

Failed files MUST be logged to a dedicated error file with full context.
- Error logs MUST include: file path, exception type, exception message, timestamp
- Processing MUST continue after individual file failures
- Summary MUST report total processed, succeeded, failed counts

## Governance

This constitution defines non-negotiable standards for the project.
- All code changes MUST comply with these principles
- Amendments require updating this document with version increment
- Complexity beyond these principles MUST be justified in PR description

**Version**: 1.2.0 | **Ratified**: 2026-01-17 | **Last Amended**: 2026-01-17
