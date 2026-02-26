# Research: Optimal Docling Chunking for Qdrant

**Date**: 2025-02-25 | **Branch**: `004-docling-chunking`

## R1: Docling HybridChunker API

**Decision**: Use `docling_core.transforms.chunker.HybridChunker` with `HuggingFaceTokenizer`.

**Rationale**: HybridChunker is the recommended Docling chunker for structure-aware, token-limited chunking. It operates on `DoclingDocument` objects (not raw text), preserving heading hierarchy and document structure. The `HuggingFaceTokenizer` wrapper aligns token counting with our embedding model.

**Key findings**:

- **Import**: `from docling_core.transforms.chunker import HybridChunker`
- **Tokenizer**: `from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer`
- **Constructor**: `HybridChunker(tokenizer=..., merge_peers=True)` — no `max_tokens` param on HybridChunker itself; token limit comes from the tokenizer's `get_max_tokens()`.
- **Modern usage** (avoids deprecation warnings):
  ```python
  tokenizer = HuggingFaceTokenizer.from_pretrained(
      model_name="sentence-transformers/all-MiniLM-L6-v2",
      max_tokens=512,
  )
  chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)
  ```
- **`chunk()` method**: `chunker.chunk(dl_doc: DoclingDocument) -> Iterator[DocChunk]` — yields `DocChunk` instances.
- **`contextualize()` method**: `chunker.contextualize(chunk: DocChunk) -> str` — returns chunk text with heading context prepended. Use this as the embedding input.
- **Chunk metadata**: `chunk.meta.export_json_dict()` returns `{"schema_name": ..., "doc_items": [...], "headings": ["H1", "H2"], "origin": {...}}`.
- **`chunk.export_json_dict()`** returns `{"text": "...", "meta": {...}}`.

**Alternatives considered**:
- `HierarchicalChunker` (Docling) — less flexible, no peer merging, less token control.
- Custom regex-based splitter — would lose Docling's structural understanding.
- LangChain/LlamaIndex chunkers — external dependency, don't understand Docling's document model.

## R2: DoclingDocument Serialization

**Decision**: Serialize `DoclingDocument` via `model_dump_json()` and deserialize via `DoclingDocument.model_validate_json()`.

**Rationale**: DoclingDocument is a Pydantic v2 model with full round-trip JSON serialization support. This is the simplest and most reliable approach for GCS caching.

**Key findings**:

- **Serialize**: `json_str = doc.model_dump_json()` → JSON string
- **Deserialize**: `doc = DoclingDocument.model_validate_json(json_str)` → DoclingDocument
- **Also available**: `doc.export_to_dict()` → dict; `doc.save_as_json(filename)` → file; `DoclingDocument.load_from_json(filename)` → DoclingDocument
- **Import**: `from docling_core.types import DoclingDocument`
- **Size concern**: DoclingDocument JSON includes all structural data (tables, figures, text items). Typically 2-10x larger than markdown export. For a 100-page PDF, expect 5-50MB JSON. This is acceptable for GCS caching.

**Alternatives considered**:
- Pickle — not portable, version-sensitive.
- Markdown-only cache — loses structural info needed by HybridChunker.
- Protobuf — Docling doesn't provide a protobuf schema.

## R3: MUMPS Label Convention & Regex

**Decision**: Use regex `^(%?[A-Za-z][A-Za-z0-9]*)(?:\(([^)]*)\))?(?=[\s;(]|$)` with `re.MULTILINE` to detect label boundaries.

**Rationale**: MUMPS labels are defined purely by column-1 position. All code body lines are indented. This makes regex detection highly reliable with near-zero false positives in standard VistA/RPMS code.

**Key findings**:

- **Labels start in column 1**: `%?[A-Za-z][A-Za-z0-9]*` — optional `%` prefix, then letter, then alphanumeric.
- **Formal parameters**: Optional `(ARG1,ARG2)` immediately after the label name (no space).
- **Code body lines**: Always start with whitespace (space or tab).
- **Comments**: `;` to end-of-line. `;;` (double) is VistA convention for structured data/version strings.
- **File header**: Line 1 is always the routine name as a column-1 label + comment. Lines 2+ have indented `;;` version/package metadata, then optional `;` comment lines.
- **No explicit routine-end marker**. Routines run from one label to the next (or EOF).
- **VistA conventions**: Labels are typically UPPERCASE, 1-8 chars. Common patterns: `EN` (main entry), `INIT`, `CLEAN`, `EXIT`.

**Chunker algorithm**:
1. Split file at label boundaries (column-1 regex matches).
2. Lines before the first label (if any) → file header chunk.
3. Each label + its indented body → one routine chunk.
4. If a routine chunk exceeds the token limit, split at blank-line or `;`-comment boundaries.

**Alternatives considered**:
- Tree-sitter parser — no MUMPS grammar exists.
- Indent-based splitting only — misses label semantics, produces less meaningful chunks.
- Fixed-size token windows — loses routine boundary context.

## R4: Source URL Reconstruction

**Decision**: Map GCS `source/` paths to original web URLs using domain-first path convention.

**Rationale**: The GCS archive was built by httrack, wget2, and `gh repo clone`. The first path element after `source/` is the domain name. This convention is consistent across all source types.

**Key findings**:

- **Web-mirrored sites**: `source/{domain}/{path}` → `https://{domain}/{path}`
  - Example: `source/www.va.gov/vdl/doc.pdf` → `https://www.va.gov/vdl/doc.pdf`
  - Domains: `www.va.gov`, `www.ihs.gov`, `hardhats.org`, `worldvista.org`, `opensourcevista.net`, `code.worldvista.org`, `foia-vista.worldvista.org`, `osehra.s3-website-us-east-1.amazonaws.com`
- **GitHub repos**: `source/WorldVistA/{repo}/{path}` → `https://github.com/WorldVistA/{repo}/blob/HEAD/{path}`
- **httrack-renamed HTML files**: httrack may rename files (e.g., `index-2.html`). The canonical URL is embedded in an HTML comment: `<!-- Mirrored from {url} by HTTrack ... -->`. Extract and use this when present.
- **Protocol assumption**: Default to `https://` for all domains.

**URL resolution priority**:
1. If file content contains `<!-- Mirrored from {url}`, use that URL.
2. If path starts with `WorldVistA/`, construct GitHub URL.
3. Otherwise, construct `https://{first_element}/{remainder}`.

**Alternatives considered**:
- `gs://` URI — not browsable, requires GCP credentials.
- GCS public URL — bucket may not be public.
- No URL — loses traceability, which is the whole point.

## R5: Dependency Requirements

**Decision**: Add `docling-core[chunking]` and `transformers` as dependencies.

**Rationale**: `HybridChunker` requires `semchunk` and `transformers` (via the `chunking` extra of docling-core). `transformers` is needed for `HuggingFaceTokenizer` to load the model's tokenizer for accurate token counting.

**Key findings**:

- `docling-core[chunking]` — installs `semchunk`, `transformers` extras needed by HybridChunker.
- `docling>=2.0.0` already pulls in `docling-core` as a transitive dependency.
- `transformers` — needed for `HuggingFaceTokenizer.from_pretrained()` to load the model tokenizer weights.
- `HuggingFaceTokenizer` auto-detects `max_tokens` from `sentence_bert_config.json` if available. For all-MiniLM-L6-v2, it auto-detects 512.
- No new system-level dependencies required.

**pyproject.toml change**:
```toml
dependencies = [
    "docling>=2.0.0",
    "docling-core[chunking]",      # NEW — HybridChunker + tokenizer support
    "google-cloud-storage>=2.0.0",
    "qdrant-client[fastembed]>=1.12.0",
    "python-magic>=0.4.27",
    "psutil>=5.9.0",
]
```

**Alternatives considered**:
- Using the deprecated string-based tokenizer API — works but emits warnings and may be removed.
- Bundling a custom tokenizer — unnecessary complexity.
