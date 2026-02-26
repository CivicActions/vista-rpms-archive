# Tasks: Optimal Docling Chunking for Qdrant

**Input**: Design documents from `/specs/004-docling-chunking/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Not requested — manual integration testing per quickstart.md validation checks.

**Organization**: Tasks grouped by user story. US1 and US2 are combined (P1) because token-aligned chunking is inherent in the HybridChunker configuration. US4 (Rich Metadata) is cross-cutting — its requirements are implemented within US1 and US3 tasks, with a dedicated verification phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Add new dependency and shared data type

- [ ] T001 Add `docling-core[chunking]` to dependencies in pyproject.toml and run `uv sync`
- [ ] T002 [P] Add ChunkResult dataclass to src/types.py per data-model.md entity definition (fields: text, embedding_text, chunk_index, total_chunks, source_path, source_url, content_hash, metadata)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared modules that all user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 [P] Implement `resolve_source_url(source_path, content=None) -> str` in src/url_resolver.py per Contract 2 — httrack comment extraction, WorldVistA GitHub URL mapping, domain-first reconstruction (research R4)
- [ ] T004 [P] Add `extract_to_document(file_path) -> DoclingDocument` method to src/extractor.py per Contract 3 — return the DoclingDocument object from Docling's DocumentConverter instead of discarding it
- [ ] T005 [P] Add `upload_docling_json(source_path, json_str) -> str` and `download_docling_json(source_path) -> str | None` methods to src/gcs_client.py per Contract 5 — cache path convention: `cache/{relative_path}.docling.json`
- [ ] T006 [P] Implement `chunk_text_fallback(text, source_path, source_url, content_hash) -> list[ChunkResult]` in src/chunker.py per Contract 1 — token-window splitting at line boundaries using HuggingFaceTokenizer with max_tokens=512 (research R1)

**Checkpoint**: Foundation ready — all four tasks are independent (different files) and can execute in parallel

---

## Phase 3: User Story 1+2 — Structure-Aware, Token-Aligned Document Chunking (Priority: P1) 🎯 MVP

**Goal**: Replace character-based chunking with Docling HybridChunker for office documents (PDF, DOCX, PPTX, HTML). All chunks sized within 512-token embedding window. Heading context prepended via `contextualize()`.

**Independent Test**: Index a multi-section PDF, search for a concept within one section, verify returned chunk is coherent and ≤512 tokens with heading breadcrumb.

### Implementation for User Story 1+2

- [ ] T007 [US1] Implement `chunk_document(doc, source_path, source_url, content_hash) -> list[ChunkResult]` in src/chunker.py — instantiate HybridChunker with `HuggingFaceTokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2", max_tokens=512)` and `merge_peers=True`, iterate `chunk()`, use `contextualize()` for embedding_text, export `chunk.meta.export_json_dict()` into metadata (headings, page, doc_items), fall back to `chunk_text_fallback()` on error or zero chunks (FR-001, FR-002, FR-003, FR-004, FR-005, FR-009)
- [ ] T008 [P] [US1] Implement `index_chunks(chunks, collection_name, force, dry_run, file_size, original_format, cache_path) -> IndexingResult` and `delete_by_source_path(source_path, collection_name) -> int` in src/qdrant_client.py per Contract 4 — build Qdrant payload per contracts/qdrant-payload.md (document field = chunk.text, metadata dict with all common + type-specific fields, chunker type, collection name), embed via chunk.embedding_text, generate point IDs via existing uuid5 convention, call delete_by_source_path() when force=True before upserting
- [ ] T009 [US1] Wire document chunking into `_process_doc_file()` in src/pipeline_gcs.py — replace current flow with: (1) call extract_to_document() → DoclingDocument, (2) cache DoclingDocument JSON via upload_docling_json(), (3) generate markdown cache via existing path, (4) resolve source URL via resolve_source_url(), (5) call chunk_document(), (6) call index_chunks(); check download_docling_json() before extraction to reuse cached DoclingDocument
- [ ] T010 [US1] Remove deprecated `chunk_text()` method and its `_split_text()` helper from src/qdrant_client.py (FR-008) — replace any remaining callers of `index_document()` that relied on internal chunking with direct calls to index_chunks()

**Checkpoint**: Document chunking pipeline is fully functional. PDFs/DOCX/PPTX/HTML produce structure-aware, token-aligned chunks with heading context and rich metadata.

---

## Phase 4: User Story 3 — Code-Aware MUMPS Chunking (Priority: P2)

**Goal**: Split MUMPS source files at label/routine boundaries instead of arbitrary character positions. File headers emitted as separate chunks. Line ranges tracked in metadata.

**Independent Test**: Index several `.m` files, verify chunks align with label boundaries and header is its own chunk with `is_header=true`.

### Implementation for User Story 3

- [ ] T011 [US3] Implement `chunk_source_code(text, source_path, source_url, content_hash) -> list[ChunkResult]` in src/chunker.py per Contract 1 and research R3 — use MUMPS label regex `^(%?[A-Za-z][A-Za-z0-9]*)(?:\(([^)]*)\))?(?=[\s;(]|$)` with `re.MULTILINE` to find boundaries, emit lines before first label as header chunk (`is_header=True, routine_name="_header"`), emit each label+body as routine chunk with line_start/line_end, split oversized routines at blank-line/comment boundaries, fall back to chunk_text_fallback() if no labels found (FR-006)
- [ ] T012 [US3] Wire source code chunking into `_process_source_file()` in src/pipeline_gcs.py — replace current flow with: (1) resolve source URL via resolve_source_url(source_path, content), (2) call chunk_source_code(), (3) call index_chunks()

**Checkpoint**: Source code chunking pipeline is fully functional. MUMPS files produce label-boundary chunks with line ranges and header separation.

---

## Phase 5: User Story 4 — Rich Metadata in Qdrant Payloads (Priority: P2)

**Goal**: Ensure every chunk's Qdrant payload includes complete structural metadata enabling filtered search, MCP traceability, and source navigation.

**Independent Test**: Query a Qdrant point directly (via REST or client), verify payload matches contracts/qdrant-payload.md schema for both document and source code variants.

**Note**: US4 requirements (FR-013, FR-014, FR-015) are implemented across T003 (source URL), T007 (document metadata), T008 (payload assembly), T011 (source code metadata). This phase verifies end-to-end metadata completeness.

### Implementation for User Story 4

- [ ] T013 [US4] Validate Qdrant payload completeness for document chunks — process a sample PDF and verify the stored point payload contains all required fields per contracts/qdrant-payload.md: document (contextualized text), metadata.source_path, metadata.source_url, metadata.content_hash, metadata.chunk_index, metadata.total_chunks, metadata.original_format, metadata.file_size, metadata.cache_path, metadata.chunker ("docling-hybrid"), metadata.headings, metadata.page, metadata.doc_items, metadata.collection (SC-005, SC-008)
- [ ] T014 [US4] Validate Qdrant payload completeness for source code chunks — process a sample `.m` file and verify the stored point payload contains all required fields per contracts/qdrant-payload.md: metadata.language ("MUMPS"), metadata.routine_name, metadata.is_header, metadata.line_start, metadata.line_end, metadata.source_url (GitHub URL format), metadata.chunker ("mumps-label") (SC-005, SC-008)

**Checkpoint**: All metadata flows end-to-end from chunking through Qdrant to MCP results. Source URLs and location references are present in every payload.

---

## Phase 6: User Story 5 — Backward-Compatible Re-indexing (Priority: P3)

**Goal**: Re-index existing corpus with `--force`, replacing old character-based chunks with new structure-aware chunks, leaving no orphaned points.

**Independent Test**: Run `--force` on a previously-indexed collection subset, verify old points are deleted and new chunks exist with no orphans.

### Implementation for User Story 5

- [ ] T015 [US5] Verify force-mode orphan cleanup in src/qdrant_client.py — ensure index_chunks() with force=True calls delete_by_source_path() before upserting, handling the case where a document previously had N chunks but now has M≠N chunks (FR-010, SC-007)
- [ ] T016 [US5] End-to-end re-indexing validation — run pipeline with `--force` on a sample of previously-indexed documents, verify: (1) old character-based chunks are deleted, (2) new structure-aware chunks are inserted, (3) no orphaned points remain for any document, (4) Qdrant payload schema remains backward-compatible with mcp-server-qdrant (FR-012, SC-006, SC-007)

**Checkpoint**: Full re-indexing capability verified. Existing corpus can be safely migrated to new chunking strategy.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cleanup

- [ ] T017 Verify no chunk exceeds 512 tokens across a representative sample run — tokenize every produced chunk with the model tokenizer and assert ≤512 (SC-001, SC-004)
- [ ] T018 [P] Run quickstart.md validation checklist end-to-end against a live Qdrant instance
- [ ] T019 [P] Verify mcp-server-qdrant compatibility — confirm `qdrant-find` returns source_url and location references in `<metadata>` tag for both document and code chunks (SC-008)
- [ ] T020 Code cleanup — remove any unused imports, dead code from old chunking path, ensure logging covers chunk counts and chunker type per file in src/pipeline_gcs.py

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on T001 (uv sync for docling-core) — **BLOCKS all user stories**
- **US1+US2 (Phase 3)**: Depends on Phase 2 completion (T003–T006)
- **US3 (Phase 4)**: Depends on Phase 2 (T003, T006) and T008 (index_chunks) from Phase 3
- **US4 (Phase 5)**: Depends on Phase 3 + Phase 4 (verification of both pipelines)
- **US5 (Phase 6)**: Depends on Phase 3 + Phase 4 (needs new chunking in place to test re-indexing)
- **Polish (Phase 7)**: Depends on all previous phases

### User Story Dependencies

- **US1+US2 (P1)**: Can start after Foundational — no dependencies on other stories
- **US3 (P2)**: Can start after Foundational + T008 (needs index_chunks method) — T011 is parallelizable with T007
- **US4 (P2)**: Verification phase — depends on US1 and US3 being complete
- **US5 (P3)**: Depends on US1 and US3 — re-indexing needs both chunking paths

### Within Each Phase

- Phase 2: All 4 tasks are independent ([P]) — execute in parallel
- Phase 3: T007 and T008 are independent ([P]) — execute in parallel; T009 depends on both; T010 after T009
- Phase 4: T011 (chunker) before T012 (pipeline wiring)
- Phase 5: T013 and T014 are independent ([P])
- Phase 7: T018 and T019 are independent ([P])

### Parallel Opportunities

```
Phase 2 (all parallel):
  T003 (url_resolver.py) ║ T004 (extractor.py) ║ T005 (gcs_client.py) ║ T006 (chunker.py fallback)

Phase 3 (partial parallel):
  T007 (chunk_document) ║ T008 (index_chunks + delete_by_source_path)
  → T009 (wire doc pipeline) → T010 (remove old chunk_text)

Phase 3+4 overlap:
  T011 (chunk_source_code) can start in parallel with T009 (different function in chunker.py)

Phase 5 (all parallel):
  T013 (validate doc payloads) ║ T014 (validate code payloads)
```

---

## Implementation Strategy

### MVP First (User Stories 1+2 Only)

1. Complete Phase 1: Setup (T001–T002)
2. Complete Phase 2: Foundational (T003–T006)
3. Complete Phase 3: US1+US2 (T007–T010)
4. **STOP and VALIDATE**: Process a sample PDF, verify structure-aware chunks with 512-token limit and heading context
5. This is a deployable increment — document chunking is the highest-impact improvement

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1+US2 → Test document chunking → **Deploy (MVP!)**
3. Add US3 → Test MUMPS chunking → Deploy
4. Verify US4 → Confirm metadata end-to-end → Deploy
5. Run US5 → Re-index full corpus → Deploy
6. Polish → Final validation → Done

### Single Developer Strategy

1. Complete phases sequentially (1 → 2 → 3 → 4 → 5 → 6 → 7)
2. Within Phase 2, execute T003–T006 in parallel (different files)
3. Within Phase 3, execute T007 and T008 in parallel (different files)
4. Commit after each task or logical group
5. Stop and validate at each checkpoint

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- US2 (Token-Aligned) is combined with US1 because HybridChunker inherently enforces token limits via its tokenizer
- US4 (Rich Metadata) is cross-cutting — its FR-013/014/015 requirements are satisfied by implementations in T003, T007, T008, T011
- No test tasks included — project uses manual integration testing per quickstart.md
- Commit after each task or logical group
- Stop at any checkpoint to validate independently
