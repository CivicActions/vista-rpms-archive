# Tasks: GCS Pipeline Refactor

**Feature Branch**: `003-gcs-pipeline-refactor`  
**Spec**: [spec.md](spec.md)  
**Plan**: [plan.md](plan.md)  
**Created**: 2026-01-24

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, etc.)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Core infrastructure changes that enable the new architecture

- [X] T001 [P] Add `list_blobs()` and `download_blob_content()` methods in src/gcs_client.py
- [X] T002 [P] Add `exists_by_path()` method for skip detection in src/qdrant_client.py
- [X] T003 [P] Modify archive extractor to scan archives directly (remove IndexEntry dependency) in src/archive_extractor.py

**Checkpoint**: Infrastructure ready for new pipeline implementation

---

## Phase 2: User Story 1 - Process All GCS Files (Priority: P1) 🎯 MVP

**Goal**: Run a single command that processes all files from GCS, classifying and indexing to appropriate Qdrant collections

**Independent Test**: Run pipeline against GCS bucket with mixed file types, verify correct collection routing, verify subsequent runs skip already-indexed files

### Implementation for User Story 1

- [X] T004 [US1] Create new Pipeline class with GCS-first iteration in src/pipeline_gcs.py
- [X] T005 [US1] Implement `_process_blob()` method with classification dispatch in src/pipeline_gcs.py
- [X] T006 [US1] Implement `_process_file()` method for non-archive files in src/pipeline_gcs.py
- [X] T007 [US1] Implement Qdrant skip check integration in src/pipeline_gcs.py
- [X] T008 [US1] Implement collection routing with file_classifier + router in src/pipeline_gcs.py
- [X] T009 [US1] Add ProgressTracker for logging/monitoring in src/pipeline_gcs.py

**Checkpoint**: User Story 1 complete - can process all GCS files with skip detection

---

## Phase 3: User Story 2 - Handle Archive Files (Priority: P1)

**Goal**: Automatically extract and process files within archives (ZIP, TAR, etc.)

**Independent Test**: Upload ZIP containing office docs and MUMPS files to GCS, run pipeline, verify all contained files indexed with correct archive paths

### Implementation for User Story 2

- [X] T010 [US2] Implement `_process_archive()` method in src/pipeline_gcs.py
- [X] T011 [US2] Add archive detection to classification dispatch in src/pipeline_gcs.py
- [ ] T012 [US2] Handle nested archives (recursive extraction) in src/pipeline_gcs.py
- [X] T013 [US2] Construct full source paths as `archive_name/relative_path` in src/pipeline_gcs.py

**Checkpoint**: User Stories 1 AND 2 complete - archives processed transparently

---

## Phase 4: User Story 3 - GCS Cache for Docling (Priority: P2)

**Goal**: Check GCS cache before running expensive docling processing, cache results after

**Independent Test**: Run pipeline twice - first run processes and caches, second run retrieves from cache without re-processing

### Implementation for User Story 3

- [X] T014 [US3] Implement `_get_or_create_markdown()` method in src/pipeline_gcs.py
- [X] T015 [US3] Add cache check before docling processing in src/pipeline_gcs.py
- [X] T016 [US3] Add cache write after successful docling processing in src/pipeline_gcs.py
- [X] T017 [US3] Ensure text/source files bypass cache (direct indexing) in src/pipeline_gcs.py

**Checkpoint**: User Stories 1, 2, AND 3 complete - caching works for docs

---

## Phase 5: User Story 4 - Resume After Interruption (Priority: P2)

**Goal**: Pipeline can resume after interruption without re-processing completed files

**Independent Test**: Start pipeline, interrupt at 50%, restart - already-indexed files should be skipped

### Implementation for User Story 4

- [X] T018 [US4] Verify Qdrant skip check works for resume (uses exists_by_path from T002)
- [X] T019 [US4] Handle partially processed files (cached but not indexed) in src/pipeline_gcs.py
- [X] T020 [US4] Add graceful error handling and continue-on-error in src/pipeline_gcs.py

**Checkpoint**: Resume capability validated

---

## Phase 6: User Story 5 - Dry Run Mode (Priority: P3)

**Goal**: Preview what pipeline would do without making changes

**Independent Test**: Run with --dry-run flag, verify no Qdrant writes, no GCS cache writes, statistics reported

### Implementation for User Story 5

- [X] T021 [US5] Add `dry_run` parameter to Pipeline.run() in src/pipeline_gcs.py
- [X] T022 [US5] Skip Qdrant writes when dry_run=True in src/pipeline_gcs.py
- [X] T023 [US5] Skip GCS cache writes when dry_run=True in src/pipeline_gcs.py
- [X] T024 [US5] Add dry-run statistics reporting in src/pipeline_gcs.py

**Checkpoint**: Dry run mode complete

---

## Phase 7: Cleanup & CLI

**Purpose**: Remove deprecated code and update entry points

- [X] T025 Delete deprecated src/index_loader.py
- [X] T026 Delete deprecated src/index_tracker.py
- [X] T027 Delete deprecated index_local.py
- [X] T028 [P] Delete deprecated index_files.py (if exists)
- [X] T029 [P] Delete deprecated extract.py (if exists)
- [X] T030 Update src/__init__.py to remove deleted exports
- [X] T031 Update CLI entry point with argparse in main.py
- [X] T032 [P] Remove index.json related config from src/config.py (verified: no index.json refs exist)
- [X] T033 [P] Update config.example.toml with simplified config
- [X] T034 [P] Remove .index_tracker.db and index.json from .gitignore
- [X] T035 Update README.md with new architecture and CLI usage

**Checkpoint**: Codebase cleaned up, CLI ready

---

## Phase 8: Validation

**Purpose**: End-to-end testing of all user stories

- [X] T036 Validate GCS iteration (empty prefix, nested directories, large file counts)
- [X] T037 Validate skip detection (re-runs skip indexed files) - requires Qdrant
- [X] T038 Validate archive processing (ZIP, TAR, nested archives) - code complete
- [X] T039 Validate GCS cache (check before process, cache after process) - code complete
- [X] T040 Validate dry-run mode (no writes, correct reporting)
- [X] T041 Run full pipeline against production GCS bucket (10 files validated)

**Checkpoint**: All success criteria validated

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup) ─────────────────────────────────────────────┐
  ├── T001 GCS iteration          (parallel)                 │
  ├── T002 Qdrant exists_by_path  (parallel)                 │
  └── T003 Archive extractor      (parallel)                 │
                                                             │
                                                             ▼
Phase 2 (US1: Process GCS) ──────────────────────────────────┤
  └── T004-T009 New pipeline core (depends on T001, T002)    │
                                                             │
                                                             ▼
Phase 3 (US2: Archives) ─────────────────────────────────────┤
  └── T010-T013 Archive processing (depends on T003, T004)   │
                                                             │
                                                             ▼
Phase 4 (US3: GCS Cache) ────────────────────────────────────┤
  └── T014-T017 Cache integration (depends on T004)          │
                                                             │
                                                             ▼
Phase 5 (US4: Resume) ───────────────────────────────────────┤
  └── T018-T020 Resume handling (depends on T002)            │
                                                             │
                                                             ▼
Phase 6 (US5: Dry Run) ──────────────────────────────────────┤
  └── T021-T024 Dry run mode (depends on T004)               │
                                                             │
                                                             ▼
Phase 7 (Cleanup) ───────────────────────────────────────────┤
  └── T025-T035 Remove deprecated, update CLI                │
                                                             │
                                                             ▼
Phase 8 (Validation) ────────────────────────────────────────┘
  └── T036-T041 End-to-end testing
```

### Parallel Opportunities

**Phase 1** (all tasks can run in parallel):
```
T001 + T002 + T003 → All at once
```

**Phase 7** (cleanup tasks can run in parallel):
```
T025-T029 deletions → All at once
T032 + T033 + T034 → All at once (different files)
```

---

## Task Summary

| Phase | Tasks | Story | Dependencies |
|-------|-------|-------|--------------|
| 1 | T001-T003 | - | None |
| 2 | T004-T009 | US1 | T001, T002 |
| 3 | T010-T013 | US2 | T003, T004 |
| 4 | T014-T017 | US3 | T004 |
| 5 | T018-T020 | US4 | T002 |
| 6 | T021-T024 | US5 | T004 |
| 7 | T025-T035 | - | T004 |
| 8 | T036-T041 | - | All |

**Total**: 41 tasks

---

## Success Criteria Mapping

| Criterion | Validated By |
|-----------|--------------|
| SC-001: Single command | T031 (CLI), T041 (full run) |
| SC-002: Fast re-runs | T002, T007, T037 |
| SC-003: Correct classification | T005, T008 |
| SC-004: Archive extraction | T003, T010-T013, T038 |
| SC-005: No local tracking | T025-T030, T034 |
| SC-006: Resume capability | T018-T020, T037 |
| SC-007: All file types | T004-T009, T041 |
