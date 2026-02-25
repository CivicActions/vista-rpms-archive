# Feature Specification: Optimal Docling Chunking for Qdrant

**Feature Branch**: `004-docling-chunking`
**Created**: 2025-02-25
**Status**: Draft
**Input**: User description: "Given Qdrant recommendations on chunking, revise this tool to use optimal configuration for Docling HybridChunker to produce chunks aligned with Qdrant best practices for both large office documents and MUMPS source code"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Structure-Aware Chunking for Office Documents (Priority: P1)

As a user searching the VistA/RPMS knowledge base, I want large office documents (PDF, DOCX, PPTX) to be chunked along natural structural boundaries (sections, headings, paragraphs) rather than at arbitrary character positions, so that each retrieved chunk contains a coherent, self-contained piece of information.

**Why this priority**: The majority of the indexed corpus consists of structured office documents. The current naive character-splitting approach produces chunks that break mid-sentence, mid-paragraph, and mid-section, resulting in poor retrieval quality. Fixing this is the single highest-impact improvement.

**Independent Test**: Index a multi-section PDF using the new chunking, then search for a concept that spans a section. Verify that the returned chunk includes the complete section context (heading + body) rather than a fragment cut at an arbitrary character boundary.

**Acceptance Scenarios**:

1. **Given** a 100-page PDF with headings and sections, **When** the pipeline processes it, **Then** chunks are produced that respect heading/section boundaries and each chunk starts with or includes its parent heading context.
2. **Given** a PDF with many short paragraphs under one heading, **When** the pipeline processes it, **Then** small adjacent paragraphs under the same heading are merged into a single chunk (up to the token limit) rather than producing many tiny chunks.
3. **Given** a document with a very long section exceeding the token limit, **When** the pipeline processes it, **Then** the section is split at sentence or paragraph boundaries (not mid-word or mid-sentence), and the heading context is preserved in each resulting sub-chunk.
4. **Given** any processed document chunk, **When** it is stored in Qdrant, **Then** its payload includes structural metadata (heading path, chunk index, total chunks, page number if available).

---

### User Story 2 - Token-Aligned Chunking (Priority: P1)

As the system, I need all chunks to be sized within the embedding model's optimal token window, so that no content is truncated during embedding and retrieval quality is maximized.

**Why this priority**: The current system chunks by character count (4MB target), which has no relationship to the embedding model's token limit (512 tokens for all-MiniLM-L6-v2). Oversized chunks are silently truncated by the embedding model, causing data loss. This is a correctness issue.

**Independent Test**: Process a large document and verify that every produced chunk, when tokenized with the embedding model's tokenizer, fits within the configured maximum token count.

**Acceptance Scenarios**:

1. **Given** the embedding model is `sentence-transformers/all-MiniLM-L6-v2` with a 512-token max sequence length, **When** any document is chunked, **Then** no chunk exceeds 512 tokens when measured by the model's tokenizer.
2. **Given** a chunk that would be undersized (e.g., 10 tokens), **When** it shares the same heading context as its neighbor, **Then** it is merged with its neighbor until the combined size approaches but does not exceed the token limit.
3. **Given** the embedding model is changed in configuration, **When** the pipeline runs, **Then** the chunker automatically uses the new model's tokenizer and token limit for chunk sizing.

---

### User Story 3 - Code-Aware Chunking for MUMPS Source Files (Priority: P2)

As a developer searching for MUMPS routines, I want source code files to be chunked along natural code boundaries (routine/label boundaries, logical blocks) rather than at arbitrary positions, so that each retrieved chunk contains a complete, meaningful unit of code.

**Why this priority**: MUMPS source code has no Docling structural parsing support, but the current character-based chunking produces poor results. A code-aware chunker is the second most impactful improvement after document chunking.

**Independent Test**: Index several MUMPS `.m` files and verify that chunks are split at label/routine boundaries rather than mid-routine.

**Acceptance Scenarios**:

1. **Given** a MUMPS source file with multiple labeled routines, **When** the pipeline processes it, **Then** chunks are split at label boundaries (column-1 identifiers) rather than at arbitrary character positions.
2. **Given** a single MUMPS routine that exceeds the token limit, **When** the pipeline processes it, **Then** it is split at blank-line or comment boundaries within the routine, with the routine label preserved as context.
3. **Given** any source code chunk stored in Qdrant, **When** I examine its payload, **Then** it includes metadata such as `file_path`, `routine_name` (if applicable), `chunk_index`, `total_chunks`, and `line_start`/`line_end`.

---

### User Story 4 - Rich Metadata in Qdrant Payloads (Priority: P2)

As a user performing filtered search, I want each chunk's Qdrant payload to include rich structural metadata from the chunking process, so that I can filter or group results by document section, page, or heading.

**Why this priority**: Qdrant best practices emphasize payload metadata for filtering and hybrid search. The current payload has minimal metadata (source_path, content_hash, chunk_index). Enriching this enables much more effective retrieval.

**Independent Test**: Index a structured PDF and verify that the Qdrant payload for each point includes heading path, page number, and section context from Docling's metadata output.

**Acceptance Scenarios**:

1. **Given** a chunked document, **When** it is indexed to Qdrant, **Then** the payload includes Docling-provided metadata: heading hierarchy, document-level metadata, and chunk position information.
2. **Given** a document chunk produced by Docling's `contextualize()` method, **When** it is embedded, **Then** the embedded text includes the heading context prepended to the chunk body, improving semantic retrieval.
3. **Given** a source code chunk, **When** it is indexed, **Then** the payload includes file path, detected language, line ranges, and any extracted routine/label names.
4. **Given** any chunk (document or source code) stored in Qdrant, **When** an MCP client retrieves it via `qdrant-find`, **Then** the metadata includes a browsable source URL and a concise location reference (page number or line range), enabling the user to navigate directly to the original source.

---

### User Story 5 - Backward-Compatible Re-indexing (Priority: P3)

As an operator, I want the ability to re-index the entire corpus with the new chunking strategy, replacing the old character-based chunks, so that the full knowledge base benefits from improved retrieval quality.

**Why this priority**: Existing indexed data uses the old chunking approach. Without re-indexing, the corpus will have inconsistent chunk quality. This is lower priority because the pipeline already supports `--force` mode.

**Independent Test**: Run the pipeline with `--force` on a subset of already-indexed documents. Verify old chunks are replaced with new structure-aware chunks.

**Acceptance Scenarios**:

1. **Given** documents previously indexed with character-based chunks, **When** the pipeline runs with `--force`, **Then** old points are replaced with new structure-aware chunks in Qdrant.
2. **Given** a document that was 1 chunk under the old system but produces 5 chunks under the new system, **When** re-indexed, **Then** all 5 new chunks exist and no orphaned old chunks remain.

---

### Edge Cases

- What happens when Docling fails to parse a document's structure (e.g., scanned image-only PDF)? The system falls back to treating the extracted text as a single block and applies token-window splitting.
- What happens when a MUMPS file has no recognizable label boundaries? The system falls back to token-window splitting with line-boundary awareness.
- What happens when a document produces zero chunks from Docling (empty or unreadable)? The system logs a warning and marks the document as extraction-failed (same as current behavior).
- What happens when a single table or code block exceeds the token limit? The system splits it at row or line boundaries rather than mid-content.
- How are existing multi-chunk documents cleaned up when re-indexed with a different chunk count? The system deletes all existing chunk points for a document before inserting new ones during `--force` re-indexing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST use Docling's `HybridChunker` for all office document types (PDF, DOCX, PPTX, HTML) to produce structure-aware, token-limited chunks.
- **FR-002**: System MUST configure the chunker's tokenizer to match the embedding model specified in configuration (`sentence-transformers/all-MiniLM-L6-v2` by default).
- **FR-003**: System MUST enable `merge_peers=True` in the `HybridChunker` so that small adjacent chunks under the same heading are merged up to the token limit.
- **FR-004**: System MUST use Docling's `contextualize()` method to produce embedding text that includes heading/section context prepended to chunk body.
- **FR-005**: System MUST export Docling chunk metadata (`chunk.meta.export_json_dict()`) into the Qdrant payload alongside existing metadata fields.
- **FR-006**: System MUST implement a custom code chunker for MUMPS source files that splits at label/routine boundaries and respects the embedding model's token limit. File-level header content (comments and documentation before the first label) MUST be emitted as its own separate chunk, as it typically contains the routine name, description, author, and patch history.
- **FR-007**: System MUST include `chunk_index`, `total_chunks`, and `document_id` in every chunk's Qdrant payload.
- **FR-008**: System MUST remove the current character-based `chunk_text()` function and replace it with the new Docling-based and code-aware chunking approach.
- **FR-009**: System MUST handle the case where Docling structural parsing fails by falling back to token-window splitting on the raw extracted text.
- **FR-010**: System MUST clean up orphaned chunk points when re-indexing a document (during `--force` mode) by querying Qdrant for all existing points matching `metadata.source_path` and deleting them before inserting new chunks. This ensures no stale chunks remain when a document's chunk count changes.
- **FR-011**: System MUST support configurable token limits via the configuration file, defaulting to the embedding model's context window size.
- **FR-012**: System MUST preserve backward compatibility with the existing Qdrant payload schema (the `document` and `metadata` field structure and the named vector format).
- **FR-013**: System MUST include a browsable source URL in every chunk's metadata payload, reconstructed from the GCS source path by mapping the first path element to the domain and the remainder to the URL path (e.g., `source/www.va.gov/vdl/doc.pdf` → `https://www.va.gov/vdl/doc.pdf`; `source/WorldVistA/{repo}/{path}` → `https://github.com/WorldVistA/{repo}/{path}`). For httrack-mirrored HTML files with renamed filenames, the canonical URL MUST be extracted from the `<!-- Mirrored from ... -->` HTML comment.
- **FR-014**: System MUST include location references in every chunk's metadata: page number(s) for office documents (when available from Docling) and `line_start`/`line_end` for source code files.
- **FR-015**: Source URL and location references MUST be stored in the metadata payload (not embedded in the chunk text), to avoid wasting embedding tokens and diluting semantic meaning.
- **FR-016**: System MUST cache the serialized `DoclingDocument` (as JSON) to GCS alongside the existing markdown cache, so that `HybridChunker` can operate on the rich document structure without re-converting from the source file on every run.

### Key Entities

- **Chunk**: A segment of a document or source file, with associated text content, embedding vector, structural metadata, and position information. Stored as a Qdrant point.
- **Document**: A source file (office document or source code) from GCS, identified by its `source_path`. May produce one or more chunks.
- **Heading Context**: The hierarchy of headings (from Docling) under which a chunk falls, used both for metadata and for embedding enrichment via `contextualize()`.
- **Routine/Label (MUMPS)**: A named entry point in a MUMPS source file, serving as the primary structural boundary for code chunking.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: No chunk produced by the system exceeds the embedding model's token limit (512 tokens for all-MiniLM-L6-v2), verified by tokenizing every chunk during a test run.
- **SC-002**: For structured documents (PDF with headings), at least 80% of produced chunks begin with or include their parent heading context.
- **SC-003**: For MUMPS source files, at least 90% of chunks align with label/routine boundaries (i.e., do not split mid-routine).
- **SC-004**: Average chunk size is within 50-100% of the target token limit (no excessive fragmentation), measured across a representative sample of 1000 documents.
- **SC-005**: Qdrant payload for every chunk includes at minimum: `source_path`, `source_url`, `content_hash`, `chunk_index`, `total_chunks`, `indexed_at`, and location references (`page` for documents, `line_start`/`line_end` for source code).
- **SC-006**: Search result relevance improves qualitatively: a manual review of 20 sample queries shows retrieved chunks are self-contained and contextually meaningful (vs. current character-split fragments).
- **SC-007**: Re-indexing with `--force` produces no orphaned points in Qdrant (old chunks from previous indexing are cleaned up).
- **SC-008**: Every MCP search result includes a browsable source URL and a concise location reference (page or line range) enabling the user to trace back to the original document.

## Assumptions

- The embedding model `sentence-transformers/all-MiniLM-L6-v2` has a max sequence length of 512 tokens. Chunk targets will be set to 512 tokens by default.
- Docling's `HybridChunker` is available in the installed version of the `docling` package (>=2.0.0).
- MUMPS source files use standard conventions: labels start in column 1, routines are separated by labels, and comments begin with `;`.
- The `contextualize()` output from Docling is suitable for direct embedding (i.e., it produces a single string combining heading context and chunk body).
- The current `chunk.meta.export_json_dict()` format from Docling includes heading path and positional metadata.
- Performance impact of using `HybridChunker` instead of the current character-based splitter is acceptable (Docling chunking should be fast relative to document conversion and embedding).
- The GCS cache will store both the serialized `DoclingDocument` (as JSON) and the markdown export. Chunking operates on the cached `DoclingDocument` to preserve structural information required by `HybridChunker`. The markdown cache is retained for human-readable inspection.
- The source URL is the original web URL reconstructed from the GCS source path: the first element after `source/` is the domain name, and the remainder forms the URL path (e.g., `source/www.va.gov/vdl/doc.pdf` → `https://www.va.gov/vdl/doc.pdf`). GitHub repos under `WorldVistA/` map to `https://github.com/WorldVistA/{repo}/{path}`. For httrack-mirrored HTML files whose filenames were renamed, the canonical URL is extracted from the `<!-- Mirrored from ... -->` HTML comment embedded by httrack.

## Dependencies

- `docling` >= 2.0.0 (already a dependency; need to verify `HybridChunker` availability)
- `docling-core` (transitive dependency of docling; provides `HybridChunker`, `HuggingFaceTokenizer`)
- `transformers` (for `AutoTokenizer` to align with the embedding model)
- `fastembed` (already a dependency; provides the embedding model)
- Qdrant server with existing collections (backward compatible payload schema required)

## Clarifications

### Session 2025-02-25

- Q: How should the pipeline cache documents for HybridChunker, given it requires a DoclingDocument object (not plain markdown)? → A: Cache serialized DoclingDocument JSON alongside markdown; chunk from cached DoclingDocument.
- Q: What format should the source URL in chunk metadata use (GCS URL, gs:// URI, or original web URL)? → A: Reconstruct the original web URL from the GCS source path. The first path element after `source/` is the domain name (e.g., `source/www.va.gov/vdl/doc.pdf` → `https://www.va.gov/vdl/doc.pdf`). For httrack-mirrored HTML files, the canonical URL may be extracted from the `<!-- Mirrored from ... -->` HTML comment. For GitHub repos under `WorldVistA/`, construct `https://github.com/WorldVistA/{repo}/{path}`.
- Q: What should the default token limit per chunk be (256, 384, or 512)? → A: 512 tokens — the model's actual max sequence length. Maximizes information density per chunk and reduces total chunk count.
- Q: How should orphan chunk cleanup work during `--force` re-indexing? → A: Filter-delete: query Qdrant by `metadata.source_path` to find and delete all existing points for that document before inserting new chunks.
- Q: Should MUMPS file header content (comments/docs before the first label) be a separate chunk or attached to the first routine? → A: Separate chunk. File headers often contain routine name, description, author, and patch history — useful retrieval targets on their own, semantically distinct from the first routine's code.

## Out of Scope

- Changing the embedding model (the spec focuses on chunking strategy, not model selection)
- Implementing tree-sitter-based syntactic parsing for MUMPS (no tree-sitter grammar exists for MUMPS)
- Implementing query-time neighbor expansion in Qdrant (this is a retrieval optimization, not an indexing concern)
- Changing the Qdrant collection schema or vector configuration
- OCR improvements for scanned documents (Docling OCR is separately configurable)
