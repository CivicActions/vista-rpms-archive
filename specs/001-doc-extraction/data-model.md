# Data Model: Document Extraction Pipeline

**Feature**: 001-doc-extraction  
**Date**: 2026-01-17

## Entities

### IndexEntry

Represents a file record from `index.json`.

| Field | Type | Description |
|-------|------|-------------|
| path | str | Relative path from source root |
| absolute_path | str | Full path (for reference) |
| size_bytes | int | File size in bytes |
| mime_type | str | Detected MIME type |
| extension | str \| None | File extension (lowercase) or None |
| modified | str | ISO timestamp of last modification |
| archive_contents | list[ArchiveEntry] \| None | Contents if file is an archive |
| archive_file_count | int \| None | Count of files in archive (if applicable) |

### ArchiveEntry

Represents a file within an archive.

| Field | Type | Description |
|-------|------|-------------|
| name | str | Path within archive |
| size | int | Uncompressed size |
| compressed_size | int \| None | Compressed size (ZIP only) |
| is_dir | bool | True if directory entry |

### ExtractionResult

Outcome of processing a single file.

| Field | Type | Description |
|-------|------|-------------|
| source_path | str | Source file path (relative) |
| cache_path | str \| None | Cache file path if successful |
| success | bool | True if extraction succeeded |
| skipped | bool | True if skipped (already cached) |
| error | str \| None | Error message if failed |
| processing_time_ms | int | Time taken in milliseconds |

### Config

Runtime configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| source_bucket | str | required | GCS bucket for source files |
| source_prefix | str | "" | Prefix path within source bucket |
| cache_bucket | str | required | GCS bucket for cache files |
| cache_prefix | str | "cache/" | Prefix path within cache bucket |
| workers | int | os.cpu_count() | Number of parallel workers |
| max_pending | int | 20 | Max concurrent tasks (memory bound) |
| timeout | int | 120 | Per-file timeout in seconds |
| max_pages | int | 500 | Max pages to extract per document |
| max_file_size | int | 50MB | Max file size to process |

### ProcessingSummary

Summary of a processing run.

| Field | Type | Description |
|-------|------|-------------|
| total_files | int | Total files in index |
| filtered_files | int | Files matching MIME type filter |
| processed | int | Files successfully extracted |
| skipped | int | Files skipped (already cached) |
| failed | int | Files that failed extraction |
| total_time_ms | int | Total processing time |

## MIME Type Filters

### Office Document Types (Included)

| MIME Type | Extensions |
|-----------|------------|
| application/pdf | .pdf |
| application/msword | .doc |
| application/vnd.openxmlformats-officedocument.wordprocessingml.document | .docx |
| application/vnd.ms-excel | .xls |
| application/vnd.openxmlformats-officedocument.spreadsheetml.sheet | .xlsx |
| application/vnd.ms-powerpoint | .ppt |
| application/vnd.openxmlformats-officedocument.presentationml.presentation | .pptx |
| text/rtf | .rtf |

> **Important**: DOCX, XLSX, and PPTX are ZIP-based formats but MUST NOT be treated as archives for extraction purposes. Filter by MIME type, not by archive structure.

### Archive Types (For Nested Extraction)

| MIME Type | Extensions |
|-----------|------------|
| application/zip | .zip |
| application/x-tar | .tar |
| application/gzip | .tar.gz, .tgz |

## State Transitions

```
IndexEntry
    │
    ├── [MIME type not office] ──► Skipped (filtered)
    │
    ├── [Cache exists] ──► Skipped (cached)
    │
    └── [Process]
            │
            ├── [Success] ──► ExtractionResult(success=True, cache_path=...)
            │
            └── [Failure] ──► ExtractionResult(success=False, error=...)
```

## File Naming Convention

| Source Path | Cache Path |
|-------------|------------|
| `gs://source/data/reports/annual.pdf` | `gs://cache/cache/data/reports/annual.pdf.md` |
| `gs://source/data/docs.zip/report.pdf` | `gs://cache/cache/data/docs.zip/report.pdf.md` |

Cache paths mirror source structure with `.md` appended.
