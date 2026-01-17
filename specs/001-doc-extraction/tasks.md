# Tasks: Document Extraction Pipeline

**Input**: Design documents from `/specs/001-doc-extraction/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: Not explicitly requested - test tasks omitted.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root

---

## Phase 1: Setup

**Purpose**: Project initialization and basic structure

- [ ] T001 Create project directory structure: `src/`, `tests/unit/`, `tests/integration/`
- [ ] T002 Initialize Python project with pyproject.toml (uv, Python 3.11+, dependencies: docling, google-cloud-storage, tomli)
- [ ] T003 [P] Create config.example.toml with all configuration options documented

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T004 Create data classes for IndexEntry and ArchiveEntry in src/types.py
- [ ] T005 [P] Create data classes for ExtractionResult, Config, ProcessingSummary in src/types.py
- [ ] T006 [P] Create GCS client wrapper with connection pooling in src/gcs_client.py
- [ ] T007 Implement blob.exists() cache check method in src/gcs_client.py
- [ ] T008 [P] Implement download_to_temp() method in src/gcs_client.py
- [ ] T009 [P] Implement upload_markdown() method in src/gcs_client.py
- [ ] T010 Create TOML config loader in src/config.py
- [ ] T011 Add CLI argument parsing with config file override in src/config.py
- [ ] T012 Create MIME type filter constants (OFFICE_MIME_TYPES, ARCHIVE_MIME_TYPES) in src/types.py. Note: OFFICE_MIME_TYPES includes RTF; ARCHIVE_MIME_TYPES must EXCLUDE office MIME types (DOCX/XLSX/PPTX are ZIP-based but are NOT archives)

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Extract Office Documents to Markdown (Priority: P1) 🎯 MVP

**Goal**: Extract text from office documents (PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, RTF) to markdown in GCS cache

**Independent Test**: Run `python extract.py --config config.toml` against a bucket with a few office documents and verify markdown files appear in cache

### Implementation for User Story 1

- [ ] T013 [US1] Create index loader to read and parse index.json from GCS in src/index_loader.py
- [ ] T014 [US1] Implement MIME type filter for office documents in src/index_loader.py
- [ ] T015 [US1] Create docling extractor wrapper with PdfPipelineOptions(do_ocr=False) in src/extractor.py
- [ ] T016 [US1] Configure ImageRefMode.PLACEHOLDER for markdown export in src/extractor.py
- [ ] T017 [US1] Implement extract_to_markdown(file_path) -> str method in src/extractor.py
- [ ] T018 [US1] Create single-file processing function (download -> extract -> upload) in src/pipeline.py
- [ ] T019 [US1] Create CLI entry point with basic run mode in extract.py
- [ ] T020 [US1] Add progress logging (files processed, remaining) in src/pipeline.py
- [ ] T021 [US1] Add error logging to extraction_errors.log in src/pipeline.py

**Checkpoint**: At this point, User Story 1 should be fully functional - can extract office documents to markdown

---

## Phase 4: User Story 2 - Skip Already Extracted Files (Priority: P2)

**Goal**: Skip files that already have cached markdown, making re-runs fast

**Independent Test**: Run extraction twice - second run should complete in seconds with "skipped (cached)" logs

### Implementation for User Story 2

- [ ] T022 [US2] Add cache_path_for_source() helper to compute cache paths in src/gcs_client.py
- [ ] T023 [US2] Implement cache existence check before processing in src/pipeline.py
- [ ] T024 [US2] Add "skipped (cached)" logging for cached files in src/pipeline.py
- [ ] T025 [US2] Update progress to show "X to process, Y cached" in src/pipeline.py

**Checkpoint**: Re-running extraction skips already-cached files

---

## Phase 5: User Story 3 - Filter Files by MIME Type (Priority: P2)

**Goal**: Only process office document types, skip source code and data files

**Independent Test**: Run against mixed directory (.pdf, .m, .json, .docx) - only .pdf and .docx processed

### Implementation for User Story 3

- [ ] T026 [US3] Implement is_office_document(mime_type) filter in src/index_loader.py
- [ ] T027 [US3] Filter index entries by MIME type before processing in src/pipeline.py
- [ ] T028 [US3] Add filtered count to progress output ("Found X files, Y office documents") in src/pipeline.py

**Checkpoint**: Only office documents are processed, other file types ignored

---

## Phase 6: User Story 5 - Parallel Processing (Priority: P2)

**Goal**: Process files in parallel using ThreadPoolExecutor

**Independent Test**: Process 100 documents - should use multiple cores and complete faster than sequential

### Implementation for User Story 5

- [ ] T029 [US5] Create ProgressTracker class with thread-safe counters in src/pipeline.py
- [ ] T030 [US5] Implement ThreadPoolExecutor with configurable worker count in src/pipeline.py
- [ ] T031 [US5] Add max_pending backpressure to control memory in src/pipeline.py
- [ ] T032 [US5] Implement per-task error isolation (try/except per future) in src/pipeline.py
- [ ] T033 [US5] Add concurrent progress reporting in src/pipeline.py

**Checkpoint**: Parallel processing works, errors isolated per file

---

## Phase 7: User Story 4 - Extract from Archives (Priority: P3)

**Goal**: Extract office documents from within ZIP and TAR archives

**Independent Test**: Run against ZIP containing PDFs - markdown created at `<cache>/docs.zip/report.pdf.md`

### Implementation for User Story 4

- [ ] T034 [US4] Implement is_archive(mime_type) check in src/index_loader.py. Must return False for ZIP-based office formats (DOCX, XLSX, PPTX) even though they are technically ZIP files
- [ ] T035 [US4] Add extension-based filter for files within archives (from index.json archive_contents) in src/index_loader.py
- [ ] T036 [US4] Skip archives with no office documents based on index.json in src/pipeline.py
- [ ] T037 [US4] Implement archive download and extraction to temp directory in src/pipeline.py
- [ ] T038 [US4] Process extracted files and upload markdown with archive path prefix in src/pipeline.py
- [ ] T039 [US4] Clean up temp files after archive processing in src/pipeline.py

**Checkpoint**: Archives with office documents are processed, empty archives skipped

---

## Phase 8: User Story 6 - Configuration File (Priority: P3)

**Goal**: Support TOML configuration file with CLI override capability

**Independent Test**: Create config.toml, run without CLI args - uses config values

### Implementation for User Story 6

- [ ] T040 [US6] Add --config flag to CLI argument parser in extract.py
- [ ] T041 [US6] Implement config file loading with tomli in src/config.py
- [ ] T042 [US6] Merge CLI args over config file values (CLI takes precedence) in src/config.py
- [ ] T043 [US6] Add config validation with clear error messages in src/config.py

**Checkpoint**: Configuration works via file or CLI

---

## Phase 9: Qdrant Stubs & Polish

**Purpose**: Stub for future Qdrant loading + final polish

- [ ] T044 [P] Create Qdrant stub functions in src/qdrant_stub.py (load_document, create_collection - not implemented)
- [ ] T045 [P] Add --dry-run flag to show what would be processed without processing in extract.py
- [ ] T046 Add --stats-only flag to show cache coverage statistics in extract.py
- [ ] T047 Add ProcessingSummary output at end of run in src/pipeline.py
- [ ] T048 Run quickstart.md validation - verify all documented commands work

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-8)**: All depend on Foundational phase completion
- **Polish (Phase 9)**: Depends on US1 (core extraction) being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2 - No dependencies on other stories
- **US2 (P2)**: Can start after Phase 2 - Uses cache check from US1 flow
- **US3 (P2)**: Can start after Phase 2 - Uses filter before US1 processing
- **US5 (P2)**: Can start after Phase 2 - Wraps US1 processing in parallel
- **US4 (P3)**: Can start after US1 - Extends processing for archives
- **US6 (P3)**: Can start after Phase 2 - Independent config handling

### Parallel Opportunities

**Within Phase 2 (Foundational):**
```
T004 + T005 (types.py - different classes)
T006 + T008 + T009 (gcs_client.py - different methods)
T010 + T012 (different files)
```

**After Phase 2 (User Stories):**
```
US1 → then US2, US3, US5 can start in parallel (different aspects)
US4 depends on US1 core processing
US6 can run anytime after Phase 2
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Extract a few documents, verify markdown in cache
5. Deploy/run if ready

### Incremental Delivery

1. **MVP**: Setup + Foundational + US1 → Basic extraction works
2. **+Cache Skip**: Add US2 → Re-runs are fast
3. **+Filtering**: Add US3 → Only office docs processed
4. **+Parallel**: Add US5 → Fast batch processing
5. **+Archives**: Add US4 → Full archive support
6. **+Config**: Add US6 → Operationally ready

---

## Summary

| Phase | Tasks | Parallel | Description |
|-------|-------|----------|-------------|
| 1. Setup | T001-T003 | 1 | Project structure |
| 2. Foundational | T004-T012 | 6 | Core infrastructure |
| 3. US1 Extract | T013-T021 | 0 | MVP extraction |
| 4. US2 Cache Skip | T022-T025 | 0 | Skip cached files |
| 5. US3 MIME Filter | T026-T028 | 0 | Filter by type |
| 6. US5 Parallel | T029-T033 | 0 | Concurrent processing |
| 7. US4 Archives | T034-T039 | 0 | Archive extraction |
| 8. US6 Config | T040-T043 | 0 | Config file support |
| 9. Polish | T044-T048 | 2 | Stubs + validation |

**Total Tasks**: 48  
**Tasks per User Story**: US1=9, US2=4, US3=3, US4=6, US5=5, US6=4  
**Parallel Opportunities**: 9 tasks marked [P]  
**Independent Test Criteria**: Each user story has clear validation criteria  
**Suggested MVP**: Phase 1-3 (Setup + Foundational + US1) = 21 tasks
