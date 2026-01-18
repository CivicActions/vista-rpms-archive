# Tasks: Qdrant Vector Database Indexing

**Input**: Design documents from `/specs/002-qdrant-indexing/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Add Qdrant dependency and project configuration

- [x] T001 Add qdrant-client[fastembed] dependency to pyproject.toml
- [x] T002 [P] Update README.md with Docker setup instructions for Qdrant

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core types, configuration, and client infrastructure that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 Add IndexingResult dataclass to src/types.py (status: indexed/skipped/failed, error message)
- [x] T004 [P] Add RoutingRule dataclass to src/types.py (pattern, collection fields)
- [x] T005 Add Qdrant config fields to Config class in src/types.py (url, api_key, default_collection, routing rules, embedding_model)
- [x] T006 [P] Extend config.py CLI parser to load Qdrant settings from config.toml
- [x] T007 Create src/embedder.py with Embedder class using FastEmbed (all-MiniLM-L6-v2, 384 dims)
- [x] T008 [P] Create src/router.py with Router class (pattern matching, first-match-wins logic)
- [x] T009 Create src/qdrant_client.py with QdrantIndexer class (connect, create_collection, check_exists, upsert)
- [x] T010 Add helper functions to src/qdrant_client.py: get_point_id(path) and get_content_hash(content)
- [x] T011 Delete or replace existing src/qdrant_stub.py (no longer needed)

**Checkpoint**: Foundation ready - all Qdrant infrastructure in place

---

## Phase 3: User Story 1 - Index Converted Documents in Real-Time (Priority: P1) 🎯 MVP

**Goal**: Documents indexed into Qdrant immediately after conversion

**Independent Test**: Run pipeline on 5 docs with Qdrant Docker, verify all 5 in collection

### Implementation for User Story 1

- [x] T012 [US1] Add index_document() method to QdrantIndexer in src/qdrant_client.py (embed + upsert with payload)
- [x] T013 [US1] Add indexing hook to _process_file() in src/pipeline.py (call index_document after successful conversion)
- [x] T014 [US1] Add indexing statistics to ProcessingSummary in src/types.py (indexed_count, index_skipped_count, index_failed_count)
- [x] T015 [US1] Update ProgressTracker in src/pipeline.py to track indexing outcomes
- [x] T016 [US1] Add error handling in pipeline.py - log failures, continue processing (FR-007)
- [x] T017 [US1] Update summary logging in pipeline.py to include indexing statistics

**Checkpoint**: User Story 1 complete - documents index after conversion

---

## Phase 4: User Story 2 - Route Documents to Correct Collection (Priority: P1)

**Goal**: IHS/RPMS documents route to "rpms" collection, others to "vista"

**Independent Test**: Process 3 IHS + 3 non-IHS docs, verify correct collection routing

### Implementation for User Story 2

- [x] T018 [US2] Add get_collection() method to Router in src/router.py (evaluate rules, return collection name)
- [x] T019 [US2] Integrate Router into QdrantIndexer.index_document() in src/qdrant_client.py
- [x] T020 [US2] Update config.toml with default routing rules (ihs → rpms, default → vista)
- [x] T021 [US2] Ensure collections are auto-created on first upsert in src/qdrant_client.py (FR-005)

**Checkpoint**: User Story 2 complete - routing works according to config

---

## Phase 5: User Story 3 - Skip Already-Indexed Documents (Priority: P2)

**Goal**: Re-running pipeline skips unchanged documents efficiently

**Independent Test**: Index 5 docs, re-run pipeline, verify 0 new index operations

### Implementation for User Story 3

- [x] T022 [US3] Add is_indexed() method to QdrantIndexer in src/qdrant_client.py (check by point ID)
- [x] T023 [US3] Add needs_reindex() method to QdrantIndexer (compare content_hash in payload)
- [x] T024 [US3] Add skip logic to index_document() - return "skipped" status if already indexed with same hash
- [x] T025 [US3] Update indexing hook in pipeline.py to handle skipped status in logging
- [x] T026 [US3] Support dry-run mode in index_document() - check but don't write (FR-012)

**Checkpoint**: User Story 3 complete - incremental indexing works

---

## Phase 6: User Story 4 - Local Development with Docker (Priority: P2)

**Goal**: Developers can test indexing locally with Docker

**Independent Test**: Start Qdrant Docker, run pipeline, verify dashboard shows collections

### Implementation for User Story 4

- [ ] T027 [US4] Add Docker command to quickstart.md in specs/002-qdrant-indexing/
- [N/A] T028 [P] [US4] Add .vscode/mcp.json with mcp-server-qdrant configuration for Copilot integration (deferred)
- [ ] T029 [US4] Update config.toml with local development defaults (localhost:6333)

**Checkpoint**: User Story 4 complete - local dev workflow documented

---

## Phase 7: User Story 5 - Configure Qdrant Connection (Priority: P3)

**Goal**: Connection details configurable for different environments

**Independent Test**: Set custom URL in config, verify connection to that endpoint

### Implementation for User Story 5

- [x] T030 [US5] Support QDRANT_URL environment variable override in src/config.py
- [x] T031 [US5] Support QDRANT_API_KEY environment variable override in src/config.py
- [x] T032 [US5] Add API key authentication to QdrantClient initialization in src/qdrant_client.py
- [x] T033 [US5] Add connection validation on startup in src/qdrant_client.py (log connection status)

**Checkpoint**: User Story 5 complete - configurable for any Qdrant deployment

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and validation

- [ ] T034 [P] Run full pipeline test with 20 documents and verify Qdrant dashboard
- [ ] T035 [P] Validate mcp-server-qdrant compatibility - run `uvx mcp-server-qdrant` against indexed collection
- [ ] T036 Update README.md with Qdrant integration documentation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - install dep first
- **Foundational (Phase 2)**: Depends on Setup - BLOCKS all user stories
- **User Stories (Phase 3-7)**: All depend on Foundational phase completion
- **Polish (Phase 8)**: Depends on at least US1+US2 being complete

### User Story Dependencies

- **US1 (P1)**: Core indexing - independent after Foundational
- **US2 (P1)**: Routing - builds on US1 but independently testable
- **US3 (P2)**: Skip detection - builds on US1/US2
- **US4 (P2)**: Docker setup - can be done in parallel with US1-US3
- **US5 (P3)**: Configuration - enhances US1 but optional for MVP

### Parallel Opportunities per User Story

**Phase 2 (Foundational)**:
```
T003 ─┬─ T004 (parallel - different dataclasses)
      └─ T005 (after T003, T004 - extends Config)
T006 ─┬─ T007 (parallel - different files)
      └─ T008 (parallel - different files)
T009 ─── T010 (sequential - same file)
T011 (independent)
```

**Phase 3 (US1)**:
```
T012 → T013 → T014 → T015 → T016 → T017 (mostly sequential, same files)
```

---

## Implementation Strategy

### MVP (User Stories 1 + 2)

1. Complete Phase 1: Setup (dependency)
2. Complete Phase 2: Foundational (types, config, embedder, router, client)
3. Complete Phase 3: US1 - Index documents after conversion
4. Complete Phase 4: US2 - Route to correct collections
5. **VALIDATE**: Test with 10 documents, check both collections
6. Deploy/demo - core value delivered

### Incremental Additions

- Add US3 (skip detection) for efficient re-runs
- Add US4 (Docker docs) for developer onboarding
- Add US5 (configuration) for production deployment

---

## Notes

- Tasks T003-T011 are Foundational and MUST complete before any US work
- FastEmbed downloads model on first use (~100MB) - expect slower first run
- Qdrant auto-creates collections on first upsert - no need for separate creation step
- Point ID = MD5 hash of source_path truncated to 64-bit int
- Content hash = SHA256 of markdown content, first 32 chars
