# Internal API Contracts: Chunking Module

**Date**: 2025-02-25 | **Branch**: `004-docling-chunking`

This project is a CLI pipeline (not a web service), so contracts describe internal Python module interfaces rather than HTTP APIs. These contracts define the public interfaces between modules that change in this feature.

## Contract 1: `src/chunker.py` — Chunking Interface

### `chunk_document(doc, source_path, source_url, content_hash) -> list[ChunkResult]`

Chunk a DoclingDocument using HybridChunker.

**Parameters**:
| Name | Type | Description |
|------|------|-------------|
| `doc` | `DoclingDocument` | Docling document object (from extraction or cache) |
| `source_path` | `str` | GCS source path |
| `source_url` | `str` | Reconstructed original web URL |
| `content_hash` | `str` | SHA256 hash of full content |

**Returns**: `list[ChunkResult]` — one per chunk, with `embedding_text` from `contextualize()`.

**Error behavior**: If HybridChunker produces zero chunks or raises, falls back to `chunk_text_fallback()`.

---

### `chunk_source_code(text, source_path, source_url, content_hash) -> list[ChunkResult]`

Chunk MUMPS source code at label/routine boundaries.

**Parameters**:
| Name | Type | Description |
|------|------|-------------|
| `text` | `str` | Raw source code text (UTF-8 decoded) |
| `source_path` | `str` | GCS source path |
| `source_url` | `str` | Reconstructed original web URL |
| `content_hash` | `str` | SHA256 hash of full content |

**Returns**: `list[ChunkResult]` — one per routine/header, with line ranges in metadata.

**Error behavior**: If no labels found, falls back to `chunk_text_fallback()`.

---

### `chunk_text_fallback(text, source_path, source_url, content_hash) -> list[ChunkResult]`

Token-window splitting fallback for when structural chunking fails.

**Parameters**: Same as `chunk_source_code`.

**Returns**: `list[ChunkResult]` — token-sized chunks split at line boundaries.

---

## Contract 2: `src/url_resolver.py` — URL Resolution Interface

### `resolve_source_url(source_path, content=None) -> str`

Reconstruct the original web URL from a GCS source path.

**Parameters**:
| Name | Type | Description |
|------|------|-------------|
| `source_path` | `str` | GCS source path (e.g., `source/www.va.gov/vdl/doc.pdf`) |
| `content` | `str \| None` | Optional file content (for extracting httrack Mirrored-from comment) |

**Returns**: `str` — the reconstructed URL (e.g., `https://www.va.gov/vdl/doc.pdf`).

**Resolution priority**:
1. If `content` contains `<!-- Mirrored from {url}`, return `https://{url}`.
2. If path starts with `WorldVistA/`, return `https://github.com/WorldVistA/{repo}/blob/HEAD/{remaining_path}`.
3. Otherwise, return `https://{first_path_element}/{remaining_path}`.

---

## Contract 3: `src/extractor.py` — Modified Extraction Interface

### `extract_to_document(file_path) -> DoclingDocument`

**NEW method** — Extract document and return the DoclingDocument object (not markdown).

**Parameters**:
| Name | Type | Description |
|------|------|-------------|
| `file_path` | `Path` | Path to document file |

**Returns**: `DoclingDocument` — the full Docling document object.

**Note**: `extract_to_markdown()` is retained for backward compatibility and cache generation, but the primary extraction method for the chunking pipeline becomes `extract_to_document()`.

---

## Contract 4: `src/qdrant_client.py` — Modified Indexing Interface

### `index_chunks(chunks, collection_name, force, dry_run, file_size, original_format, cache_path) -> IndexingResult`

**REPLACES** the current `index_document()` method. Accepts pre-chunked data with metadata instead of raw text.

**Parameters**:
| Name | Type | Description |
|------|------|-------------|
| `chunks` | `list[ChunkResult]` | Pre-chunked data from chunker module |
| `collection_name` | `str` | Target Qdrant collection |
| `force` | `bool` | If True, delete existing points before inserting |
| `dry_run` | `bool` | If True, skip actual Qdrant operations |
| `file_size` | `int` | Original file size in bytes |
| `original_format` | `str` | File extension (e.g., `.pdf`) |
| `cache_path` | `str` | GCS cache path |

**Returns**: `IndexingResult` — status of the indexing operation.

**Force mode behavior**: When `force=True`, deletes all existing points for this document's `source_path` before inserting new chunks.

---

### `delete_by_source_path(source_path, collection_name) -> int`

**NEW method** — Delete all points matching a source_path.

**Parameters**:
| Name | Type | Description |
|------|------|-------------|
| `source_path` | `str` | GCS source path to match |
| `collection_name` | `str` | Target Qdrant collection |

**Returns**: `int` — number of points deleted.

---

## Contract 5: `src/gcs_client.py` — Modified Cache Interface

### `upload_docling_json(source_path, json_str) -> str`

**NEW method** — Upload serialized DoclingDocument JSON to GCS cache.

**Parameters**:
| Name | Type | Description |
|------|------|-------------|
| `source_path` | `str` | GCS source path (used to derive cache path) |
| `json_str` | `str` | Serialized DoclingDocument JSON |

**Returns**: `str` — GCS cache path of the uploaded JSON.

---

### `download_docling_json(source_path) -> str | None`

**NEW method** — Download cached DoclingDocument JSON from GCS.

**Parameters**:
| Name | Type | Description |
|------|------|-------------|
| `source_path` | `str` | GCS source path (used to derive cache path) |

**Returns**: `str | None` — JSON string if cache exists, `None` if not cached.
