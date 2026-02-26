# Quickstart: 004 — Docling Chunking

**Branch**: `004-docling-chunking`

## Prerequisites

```bash
git checkout 004-docling-chunking
uv sync                # install updated deps after docling-core[chunking] is added
```

## Implementation Order

### Step 1: Add dependency and data types

1. Add `docling-core[chunking]` to `pyproject.toml` dependencies
2. Add `ChunkResult` dataclass to `src/types.py`:
   ```python
   @dataclass
   class ChunkResult:
       text: str              # Display text (for Qdrant "document" field)
       embedding_text: str    # Text to embed (may differ from display)
       chunk_index: int
       total_chunks: int      # Set after all chunks computed
       source_path: str
       source_url: str
       content_hash: str
       metadata: dict         # Chunker-specific metadata
   ```
3. Run `uv sync` to install the new dependency

### Step 2: Implement `src/url_resolver.py`

- Implement `resolve_source_url(source_path, content=None) -> str`
- Priority: httrack comment → GitHub URL → domain reconstruction
- Unit-testable in isolation with no external deps
- See [contracts/internal-api.md](contracts/internal-api.md) Contract 2

### Step 3: Implement `src/chunker.py`

- `chunk_document()` — wraps `HybridChunker` with `max_tokens=512`
- `chunk_source_code()` — MUMPS label-based splitting with separate header chunks
- `chunk_text_fallback()` — token-window splitting for edge cases
- See [contracts/internal-api.md](contracts/internal-api.md) Contracts 1
- See [research.md](research.md) R1 for HybridChunker API details

```python
# Key imports
from docling_core.transforms.chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
```

### Step 4: Modify `src/extractor.py`

- Add `extract_to_document()` method returning `DoclingDocument`
- Keep existing `extract_to_markdown()` for backward compat / cache generation
- See [contracts/internal-api.md](contracts/internal-api.md) Contract 3

### Step 5: Modify `src/gcs_client.py`

- Add `upload_docling_json()` and `download_docling_json()` methods
- Cache path: `cache/{relative_path}.docling.json`
- See [contracts/internal-api.md](contracts/internal-api.md) Contract 5

### Step 6: Modify `src/qdrant_client.py`

- Add `index_chunks(chunks, ...)` method accepting `list[ChunkResult]`
- Add `delete_by_source_path(source_path, collection_name)` method
- Migrate from internal `chunk_text()` to accepting pre-chunked data
- Payload structure per [contracts/qdrant-payload.md](contracts/qdrant-payload.md)
- See [contracts/internal-api.md](contracts/internal-api.md) Contract 4

### Step 7: Wire into `src/pipeline_gcs.py`

- In `_process_doc_file()`:
  1. Extract → `DoclingDocument`
  2. Cache DoclingDocument JSON to GCS
  3. Resolve source URL
  4. Chunk with `chunk_document()`
  5. Index with `index_chunks()`
- In `_process_source_file()`:
  1. Resolve source URL
  2. Chunk with `chunk_source_code()`
  3. Index with `index_chunks()`

### Step 8: Test & validate

- Verify chunk count fits within embedding model's 512-token window
- Verify Qdrant payload structure matches [contracts/qdrant-payload.md](contracts/qdrant-payload.md)
- Verify mcp-server-qdrant returns `source_url`, `line_start`, etc. in `<metadata>`
- Re-index a sample collection with `--force` and verify orphan cleanup

## Key Validation Checks

| Check | Command / Method |
|-------|------------------|
| Install new deps | `uv sync` succeeds |
| Token limit | No chunk exceeds 512 tokens (SC-001) |
| Payload structure | Manual Qdrant query to inspect a point payload |
| MCP traceability | `qdrant-find` returns source_url in metadata (SC-008) |
| Re-index cleanup | `--force` on existing collection leaves no orphan points |
| MUMPS header chunks | Header lines are separate from first routine (FR-006) |
| Backward compat | Existing embeddings still retrievable (SC-006) |

## Files Modified / Created

| File | Action |
|------|--------|
| `pyproject.toml` | Add `docling-core[chunking]` |
| `src/types.py` | Add `ChunkResult` dataclass |
| `src/url_resolver.py` | **NEW** — URL reconstruction |
| `src/chunker.py` | **NEW** — Document + source code chunking |
| `src/extractor.py` | Add `extract_to_document()` |
| `src/gcs_client.py` | Add docling JSON cache methods |
| `src/qdrant_client.py` | Add `index_chunks()`, `delete_by_source_path()` |
| `src/pipeline_gcs.py` | Wire new chunking pipeline |
