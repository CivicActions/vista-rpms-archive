# Feature Specification: GCS Pipeline Refactor

**Feature Branch**: `003-gcs-pipeline-refactor`  
**Created**: 2026-01-24  
**Status**: Draft  
**Input**: User description: "Refactor pipeline to iterate over GCS files directly, using Qdrant for tracking instead of local SQLite/index.json. Remove index.json and index_*.py scripts."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Process All GCS Files in Single Run (Priority: P1)

As an operator, I want to run a single command that processes all files from a GCS bucket, classifying each file, checking if it's already indexed in Qdrant, and indexing new files appropriately, so that I can maintain a complete searchable index without needing separate tracking databases or index files.

**Why this priority**: This is the core functionality - the entire pipeline must iterate over GCS and index to Qdrant in one coherent flow. Without this, no other features work.

**Independent Test**: Can be tested by running the pipeline against a GCS bucket with mixed file types and verifying that files are indexed to the correct Qdrant collections, and subsequent runs skip already-indexed files.

**Acceptance Scenarios**:

1. **Given** a GCS bucket with office documents, text files, and MUMPS source code, **When** I run the pipeline, **Then** each file is classified and indexed to the appropriate collection (vista/rpms for docs, vista-source/rpms-source for source)
2. **Given** a file that was previously indexed, **When** the pipeline encounters it again, **Then** it skips the file without re-processing or re-indexing
3. **Given** a file that failed processing in a previous run, **When** the pipeline runs again, **Then** it attempts to process and index the file

---

### User Story 2 - Handle Archive Files Transparently (Priority: P1)

As an operator, I want the pipeline to automatically extract and process files within archives (ZIP, TAR, etc.), so that I don't need to manually extract archives before indexing.

**Why this priority**: A significant portion of the source data is contained in archive files. The pipeline must handle archives to be useful.

**Independent Test**: Can be tested by uploading a ZIP file containing office documents and MUMPS files to GCS, running the pipeline, and verifying all contained files are indexed.

**Acceptance Scenarios**:

1. **Given** a ZIP archive containing multiple files on GCS, **When** the pipeline processes it, **Then** each file within the archive is extracted, classified, and indexed individually
2. **Given** a nested archive (archive within archive), **When** the pipeline processes it, **Then** files at all nesting levels are extracted and indexed
3. **Given** an archive containing a mix of document and source files, **When** processed, **Then** documents go to vista/rpms collections and source files go to vista-source/rpms-source collections

---

### User Story 3 - Use GCS Cache for Docling-Processed Files (Priority: P2)

As an operator, I want the pipeline to check for cached docling output in GCS before processing office documents, so that repeated runs don't re-process expensive document conversions.

**Why this priority**: Docling processing is computationally expensive. Using cached results dramatically improves efficiency for incremental runs.

**Independent Test**: Can be tested by running the pipeline twice - first run processes documents and caches results, second run retrieves cached results instead of re-processing.

**Acceptance Scenarios**:

1. **Given** an office document not in the GCS cache, **When** the pipeline processes it, **Then** the docling output is stored in the GCS cache
2. **Given** an office document with existing cached output, **When** the pipeline encounters it, **Then** it retrieves the cached markdown instead of re-running docling
3. **Given** a text/source file (not requiring docling), **When** the pipeline processes it, **Then** it indexes the file directly without caching

---

### User Story 4 - Resume After Interruption (Priority: P2)

As an operator, I want the pipeline to gracefully resume after interruption, so that I don't lose progress when processing large datasets.

**Why this priority**: Processing tens of thousands of files takes time. The ability to resume prevents data loss and wasted compute.

**Independent Test**: Can be tested by starting the pipeline, interrupting it after partial progress, and restarting - already-indexed files should be skipped.

**Acceptance Scenarios**:

1. **Given** a pipeline run that was interrupted after indexing 50% of files, **When** I restart the pipeline, **Then** it checks Qdrant and skips already-indexed files
2. **Given** a file that was partially processed (cached but not indexed), **When** the pipeline restarts, **Then** it uses the cached version and completes indexing

---

### User Story 5 - Dry Run Mode (Priority: P3)

As an operator, I want to preview what the pipeline would do without making changes, so that I can validate the configuration before committing to a full run.

**Why this priority**: Useful for testing and validation but not essential for core functionality.

**Independent Test**: Can be tested by running with dry-run flag and verifying no files are indexed while statistics are reported.

**Acceptance Scenarios**:

1. **Given** a GCS bucket with files to process, **When** I run in dry-run mode, **Then** the pipeline reports what it would do without actually indexing anything
2. **Given** dry-run mode, **When** the pipeline encounters a file needing docling processing, **Then** it does not cache results to GCS

---

### Edge Cases

- What happens when a file in GCS is corrupted or unreadable? → Log error and continue with next file
- What happens when Qdrant is temporarily unavailable? → Retry with exponential backoff, fail gracefully after max retries
- What happens when GCS returns a rate limit error? → Retry with exponential backoff
- How does the system handle files with no extension? → Use content-based classification (magic/heuristics)
- What happens when the same file content exists at multiple paths? → Each path is indexed separately (different source_path metadata)
- How does the system handle very large files that don't fit in memory? → Stream processing where possible, skip with warning if too large

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST iterate over all files and directories in the configured GCS bucket/prefix
- **FR-002**: System MUST download each file from GCS for local processing
- **FR-003**: System MUST detect and extract archive files (ZIP, TAR, GZ, BZ2, 7Z) and process contained files recursively
- **FR-004**: System MUST classify each non-archive file using content-based detection (python-magic, file signatures, MUMPS pattern matching)
- **FR-005**: System MUST check Qdrant for existing indexed documents by source path before processing
- **FR-006**: System MUST skip files that are already indexed in Qdrant (matching source_path)
- **FR-007**: System MUST route documentation files to vista/rpms collections based on source path
- **FR-008**: System MUST route source code files to vista-source/rpms-source collections based on source path
- **FR-009**: System MUST check GCS cache for docling-processed output before running docling
- **FR-010**: System MUST cache docling output to GCS after successful processing
- **FR-011**: System MUST index text/source files directly without docling processing
- **FR-012**: System MUST log progress and errors for monitoring
- **FR-013**: System MUST support a dry-run mode that reports actions without executing them
- **FR-014**: System MUST handle binary files by skipping them (not indexing)
- **FR-015**: System MUST remove dependency on local SQLite tracking database
- **FR-016**: System MUST remove dependency on index.json manifest files
- **FR-017**: System MUST remove index_local.py and related index scripts

### Key Entities

- **GCS File**: A blob in Google Cloud Storage with path, content, and metadata
- **Archive File**: A compressed file containing multiple files (ZIP, TAR, etc.)
- **Classified File**: A file with determined category (documentation, source, MUMPS routine, MUMPS global, binary)
- **Qdrant Document**: An indexed document with vector embedding, content, and metadata (source_path, collection)
- **GCS Cache Entry**: A cached docling output stored in GCS with path derived from source file hash/path
- **Collection**: A Qdrant collection for storing related documents (vista, rpms, vista-source, rpms-source)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Pipeline processes entire GCS bucket in a single command without requiring separate index scripts
- **SC-002**: Subsequent pipeline runs complete in under 10% of initial run time by skipping already-indexed files
- **SC-003**: All file types (office documents, text files, MUMPS source) are correctly classified and routed to appropriate collections
- **SC-004**: Archive files are fully extracted and all contained files are indexed
- **SC-005**: No local tracking database or index.json files are created or required
- **SC-006**: Pipeline can resume after interruption without re-processing completed files
- **SC-007**: 100% of files previously handled by index_local.py are now handled by the unified pipeline

## Assumptions

- Qdrant is available and accessible via the configured URL with API key
- GCS bucket is accessible with appropriate credentials
- File paths in Qdrant metadata are sufficient for duplicate detection (no content hashing required)
- The existing file classification logic (python-magic, MUMPS detection) is correct and will be reused
- The existing docling integration is correct and will be reused
- Memory constraints allow downloading and processing individual files (streaming not required for initial implementation)

## Out of Scope

- Content change detection (re-indexing modified files) - future enhancement
- Parallel processing of multiple files simultaneously - future enhancement
- Web UI for monitoring pipeline progress
- Automated scheduling/cron integration
