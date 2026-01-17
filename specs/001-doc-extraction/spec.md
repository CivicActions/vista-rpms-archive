# Feature Specification: Document Extraction Pipeline

**Feature Branch**: `001-doc-extraction`  
**Created**: 2026-01-17  
**Status**: Draft  
**Input**: User description: "Document extraction from office files to markdown cache using docling"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Extract Office Documents to Markdown (Priority: P1)

As an operator, I want to run a script that extracts text content from office documents (PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, RTF) into markdown files stored in a cache directory, so that the content is ready for vector embedding.

**Why this priority**: This is the core value of the pipeline—without extraction, nothing else works.

**Independent Test**: Run the script against a directory containing a few office documents and verify markdown files are created in the cache directory with readable content.

**Acceptance Scenarios**:

1. **Given** a source directory with office documents and a pre-generated `index.json`, **When** I run the extraction script, **Then** markdown files are created in the cache directory mirroring the source structure.
2. **Given** a PDF document at `/data/reports/annual.pdf`, **When** extraction completes, **Then** a markdown file exists at `<cache>/reports/annual.pdf.md` containing the document text.
3. **Given** an Excel file with multiple sheets, **When** extraction completes, **Then** the markdown contains content from all sheets in readable format.

---

### User Story 2 - Skip Already Extracted Files (Priority: P2)

As an operator, I want the script to skip files that have already been extracted to the cache, so that re-running the script is fast and does not duplicate work.

**Why this priority**: Essential for operational efficiency on large datasets where full re-extraction would take hours.

**Independent Test**: Run extraction twice on the same directory and verify the second run completes in seconds without re-processing files.

**Acceptance Scenarios**:

1. **Given** a file has already been extracted to cache, **When** the script runs again, **Then** it skips that file and logs "skipped (cached)".
2. **Given** 100 files where 95 are cached, **When** the script runs, **Then** only 5 files are processed and progress shows "5 to process, 95 cached".

---

### User Story 3 - Filter Files by MIME Type (Priority: P2)

As an operator, I want the script to only process office document types (not source code or data files), so that I do not waste processing time on irrelevant files.

**Why this priority**: The archive contains many MUMPS `.m` files and data files that are not useful for document search.

**Independent Test**: Run against a directory with mixed file types and verify only office documents are processed.

**Acceptance Scenarios**:

1. **Given** a directory with `.pdf`, `.m`, `.json`, and `.docx` files, **When** extraction runs, **Then** only the `.pdf` and `.docx` are processed.
2. **Given** a file with no extension but MIME type `application/pdf`, **When** extraction runs, **Then** the file is processed (MIME type takes precedence for top-level files).
3. **Given** a file inside an archive with extension `.pdf`, **When** extraction runs, **Then** the file is processed based on extension (no MIME detection inside archives).

---

### User Story 4 - Extract from Archives (Priority: P3)

As an operator, I want the script to extract office documents from within ZIP and TAR archives, so that I can access content from compressed files without manual extraction.

**Why this priority**: Archives contain valuable documents but require extra handling; can be deferred to later iteration.

**Independent Test**: Run against a ZIP file containing PDFs and verify the PDFs are extracted to markdown.

**Acceptance Scenarios**:

1. **Given** an archive `docs.zip` containing `report.pdf`, **When** extraction runs, **Then** markdown is created at `<cache>/docs.zip/report.pdf.md`.
2. **Given** the `index.json` shows an archive contains no office documents, **When** extraction runs, **Then** the archive is skipped entirely (not downloaded/opened).

---

### User Story 5 - Parallel Processing (Priority: P2)

As an operator, I want files to be processed in parallel, so that large batches complete in reasonable time.

**Why this priority**: Single-threaded processing of thousands of documents would be impractically slow.

**Independent Test**: Process 100 documents and verify processing uses multiple CPU cores and completes faster than sequential processing.

**Acceptance Scenarios**:

1. **Given** a configurable worker count, **When** the script runs, **Then** up to that many files are processed concurrently.
2. **Given** one file fails during extraction, **When** processing continues, **Then** other parallel workers are unaffected and processing completes.

---

### User Story 6 - Configuration File (Priority: P3)

As an operator, I want to configure source paths, cache paths, and processing options via a config file, so that I do not need to pass many CLI arguments.

**Why this priority**: Important for maintainability but not blocking for initial operation via CLI args.

**Independent Test**: Create a config file and verify the script reads settings from it.

**Acceptance Scenarios**:

1. **Given** a config file specifying source and cache directories, **When** the script runs without those CLI args, **Then** it uses the config file values.
2. **Given** CLI args and a config file, **When** both specify the same setting, **Then** CLI args take precedence.

---

### Edge Cases

- What happens when a file is corrupted and cannot be extracted? -> Log error, skip file, continue processing.
- What happens when the cache directory does not exist? -> Create it automatically.
- What happens when a file in `index.json` no longer exists on disk? -> Log warning, skip, continue.
- What happens when disk space runs out during extraction? -> Fail gracefully with clear error message.
- What happens when an archive is nested (ZIP inside ZIP)? -> Process only one level deep initially.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST read file metadata from `index.json` at the source directory root
- **FR-002**: System MUST filter files to process based on MIME type (for top-level files) or extension (for archived files)
- **FR-003**: System MUST extract text from supported office formats: PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, RTF
- **FR-004**: System MUST write extracted content as markdown files to a cache directory, preserving source path structure
- **FR-005**: System MUST skip extraction if a cached markdown file already exists for a source file
- **FR-006**: System MUST process files in parallel using configurable worker count (default: os.cpu_count())
- **FR-007**: System MUST log progress including: files processed, files skipped, files remaining, errors
- **FR-008**: System MUST continue processing after individual file failures
- **FR-009**: System MUST use docling for extraction with OCR disabled and image placeholders (no base64 embedding)
- **FR-010**: System MUST support configuration via file (TOML or YAML) with CLI override capability
- **FR-011**: System MUST provide stub functions for Qdrant loading (not implemented in this phase)
- **FR-012**: System MUST use `uv` for package management

### Supported MIME Types (Office Documents)

| MIME Type | Extensions |
|-----------|------------|
| `application/pdf` | .pdf |
| `application/msword` | .doc |
| `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | .docx |
| `application/vnd.ms-excel` | .xls |
| `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | .xlsx |
| `application/vnd.ms-powerpoint` | .ppt |
| `application/vnd.openxmlformats-officedocument.presentationml.presentation` | .pptx |
| `text/rtf` | .rtf |

> **Note**: DOCX, XLSX, and PPTX files are internally ZIP archives but MUST be treated as office documents, not archives. Archive extraction logic MUST exclude these MIME types.

### Key Entities

- **IndexEntry**: A file record from `index.json` containing path, size, MIME type, and optional archive contents
- **ExtractionResult**: Outcome of processing a single file (success/failure, output path, error if any)
- **Config**: Runtime configuration including source path, cache path, worker count, MIME type filters

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All supported office documents in a test directory are extracted to markdown within one run
- **SC-002**: Re-running extraction on a fully cached directory completes in under 10 seconds (no re-processing)
- **SC-003**: Processing 100 documents uses less than 2GB memory total
- **SC-004**: Individual file failures do not prevent processing of remaining files
- **SC-005**: Progress logs show accurate counts (processed/skipped/remaining) throughout execution
- **SC-006**: Parallel processing of 100 documents completes at least 3x faster than sequential processing

## Assumptions

- `index.json` is pre-generated and available at the source bucket root (created by `index_files.py`)
- Source files are stored in GCS and accessible via google-cloud-storage client
- Cache files are written to a separate GCS prefix/bucket
- Docling library supports all listed office formats
- Python 3.11+ is available on the target server

## Clarifications

### Session 2026-01-17

- Q: What should the default parallelism level be? → A: Match CPU count (os.cpu_count())
- Q: Where are files stored? → A: GCS (both source and cache)
