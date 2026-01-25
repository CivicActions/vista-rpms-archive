# Implementation Plan: GCS Pipeline Refactor

**Feature Branch**: `003-gcs-pipeline-refactor`  
**Spec**: [spec.md](spec.md)  
**Created**: 2026-01-24  
**Status**: Ready for Implementation

## Overview

Refactor the pipeline to iterate directly over GCS files, using Qdrant as the sole source of truth for tracking indexed files. Remove all local tracking mechanisms (SQLite, index.json).

## Code Analysis: Keep vs Remove

### KEEP (with modifications)
| Module | Status | Changes Needed |
|--------|--------|----------------|
| `src/file_classifier.py` | ✅ Keep | Already has python-magic, MUMPS detection - good to go |
| `src/gcs_client.py` | ✅ Keep | Add `list_blobs()` method for GCS iteration |
| `src/qdrant_client.py` | ✅ Keep | Add `exists_by_path()` method for skip detection |
| `src/router.py` | ✅ Keep | Already has `route_with_category()` |
| `src/embedder.py` | ✅ Keep | No changes needed |
| `src/extractor.py` | ✅ Keep | No changes needed (docling wrapper) |
| `src/archive_extractor.py` | 🔄 Modify | Remove dependency on index.json metadata, scan archives directly |
| `src/types.py` | 🔄 Modify | Remove `IndexEntry`, simplify to `ProcessedFile` |
| `src/config.py` | 🔄 Modify | Remove index.json related config |
| `src/pipeline.py` | 🔄 Rewrite | New GCS-first architecture |

### REMOVE
| File | Reason |
|------|--------|
| `src/index_loader.py` | index.json dependency - replaced by GCS iteration |
| `src/index_tracker.py` | SQLite tracking - replaced by Qdrant lookups |
| `index_local.py` | Separate index script - unified into pipeline |
| `index_files.py` | Legacy index script |
| `extract.py` | Legacy extraction script |
| `index.json` | Generated manifest - no longer needed |
| `.index_tracker.db` | SQLite database - no longer needed |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Pipeline Entry                            │
│                     python -m pipeline run                       │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     GCS Blob Iterator                            │
│              gcs_client.list_blobs(prefix)                       │
└─────────────────────────────────────────────────────────────────┘
                                │
                    For each blob │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Download & Classify                           │
│         file_classifier.classify_file(content)                   │
├─────────────────────────────────────────────────────────────────┤
│  BINARY → skip                                                   │
│  ARCHIVE → extract_and_process_contents()                        │
│  DOC/SOURCE → continue to Qdrant check                          │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Qdrant Skip Check                             │
│      qdrant_client.exists_by_path(source_path, collection)       │
├─────────────────────────────────────────────────────────────────┤
│  EXISTS → skip (already indexed)                                 │
│  NOT EXISTS → continue to processing                             │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Process by Category                          │
├─────────────────────────────────────────────────────────────────┤
│  DOCUMENTATION (office docs):                                    │
│    1. Check GCS cache for processed markdown                     │
│    2. If cached → read from cache                                │
│    3. If not → docling process → cache to GCS                    │
│    4. Index markdown to vista/rpms collection                    │
├─────────────────────────────────────────────────────────────────┤
│  SOURCE_CODE / MUMPS / DATA:                                     │
│    1. Read content directly (no docling)                         │
│    2. Index to vista-source/rpms-source collection               │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Index to Qdrant                             │
│    qdrant_client.index_document(source_path, content, ...)       │
└─────────────────────────────────────────────────────────────────┘
```

## Implementation Tasks

### Phase 1: Core Infrastructure Changes

#### Task 1.1: Add GCS Blob Iteration
**File**: `src/gcs_client.py`  
**Effort**: Small  

Add method to iterate over all blobs in a GCS bucket/prefix:

```python
def list_blobs(
    self,
    prefix: Optional[str] = None,
) -> Iterator[storage.Blob]:
    """Iterate over all blobs in source bucket.
    
    Args:
        prefix: Optional prefix to filter blobs (defaults to source_prefix)
    
    Yields:
        storage.Blob objects
    """
    effective_prefix = prefix if prefix is not None else self._source_prefix
    yield from self._source_bucket.list_blobs(prefix=effective_prefix)


def download_blob_content(self, blob: storage.Blob) -> bytes:
    """Download blob content as bytes.
    
    Args:
        blob: GCS Blob object
    
    Returns:
        Raw file content as bytes
    """
    return blob.download_as_bytes()
```

---

#### Task 1.2: Add Qdrant Path-Based Existence Check
**File**: `src/qdrant_client.py`  
**Effort**: Small  

Add method to check if a document exists by source_path (without content hash):

```python
def exists_by_path(self, source_path: str) -> tuple[bool, Optional[str]]:
    """Check if document exists in any collection by source path.
    
    Returns:
        Tuple of (exists, collection_name or None)
    """
    point_id = get_point_id(source_path)
    
    # Check all potential collections
    for collection in self._get_all_collections():
        try:
            points = self.client.retrieve(
                collection_name=collection,
                ids=[point_id],
                with_payload=False,
            )
            if points:
                return True, collection
        except UnexpectedResponse:
            continue
    
    return False, None
```

---

#### Task 1.3: Modify Archive Extractor for Direct Scanning
**File**: `src/archive_extractor.py`  
**Effort**: Medium  

Remove dependency on `IndexEntry.archive_contents` - scan archives directly:

```python
def extract_all_files(
    self,
    archive_path: Path,
    archive_name: str,
) -> Iterator[tuple[str, Path, bytes]]:
    """Extract all files from an archive.
    
    Args:
        archive_path: Path to downloaded archive file
        archive_name: Original name for path construction
    
    Yields:
        Tuple of (relative_path, extracted_file_path, content_bytes)
    """
    # Scan archive contents directly instead of using index metadata
```

---

### Phase 2: New Pipeline Core

#### Task 2.1: Create New Pipeline Module
**File**: `src/pipeline.py` (rewrite)  
**Effort**: Large  

Rewrite pipeline with GCS-first iteration:

```python
class Pipeline:
    """GCS-first document processing pipeline."""
    
    def __init__(self, config: Config):
        self.gcs_client = GCSClient(...)
        self.qdrant = QdrantIndexer(...)
        self.extractor = create_extractor(...)
        self.classifier = FileClassifier()
        self.archive_extractor = ArchiveExtractor()
    
    def run(self, dry_run: bool = False) -> ProcessingSummary:
        """Process all files from GCS bucket."""
        for blob in self.gcs_client.list_blobs():
            self._process_blob(blob, dry_run)
    
    def _process_blob(self, blob: storage.Blob, dry_run: bool) -> None:
        """Process a single GCS blob."""
        content = self.gcs_client.download_blob_content(blob)
        result = self.classifier.classify_file(blob.name, content)
        
        if result.category == FileCategory.BINARY:
            return  # Skip binary files
        
        if result.category == FileCategory.ARCHIVE:
            self._process_archive(blob, content, dry_run)
            return
        
        self._process_file(blob.name, content, result, dry_run)
    
    def _process_file(
        self,
        source_path: str,
        content: bytes,
        classification: ClassificationResult,
        dry_run: bool,
    ) -> None:
        """Process a non-archive file."""
        # Check if already indexed
        exists, _ = self.qdrant.exists_by_path(source_path)
        if exists:
            logger.debug(f"Skipping {source_path} - already indexed")
            return
        
        # Route to collection
        is_source = is_source_category(classification.category)
        collection = self.router.route_with_category(source_path, is_source)
        
        # Process based on category
        if is_source:
            text_content = content.decode('utf-8', errors='replace')
        else:
            text_content = self._get_or_create_markdown(source_path, content)
        
        if not dry_run:
            self.qdrant.index_document(
                source_path=source_path,
                content=text_content,
                is_source_code=is_source,
            )
```

---

#### Task 2.2: Implement Archive Processing
**File**: `src/pipeline.py`  
**Effort**: Medium  

Handle archives by extracting and processing each contained file:

```python
def _process_archive(
    self,
    blob: storage.Blob,
    content: bytes,
    dry_run: bool,
) -> None:
    """Extract and process files from an archive."""
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    
    try:
        with self.archive_extractor as extractor:
            for rel_path, file_path, file_content in extractor.extract_all_files(
                tmp_path, blob.name
            ):
                # Construct full source path: archive_name/relative_path
                full_path = f"{blob.name}/{rel_path}"
                result = self.classifier.classify_file(rel_path, file_content)
                
                if result.category not in (FileCategory.BINARY, FileCategory.ARCHIVE):
                    self._process_file(full_path, file_content, result, dry_run)
    finally:
        tmp_path.unlink(missing_ok=True)
```

---

#### Task 2.3: Implement GCS Cache Integration
**File**: `src/pipeline.py`  
**Effort**: Small  

Add docling cache check/store logic:

```python
def _get_or_create_markdown(
    self,
    source_path: str,
    content: bytes,
) -> str:
    """Get markdown from cache or process with docling."""
    cache_path = self.gcs_client.cache_path_for_source(source_path)
    
    # Check cache first
    if self.gcs_client.cache_exists_by_path(cache_path):
        return self.gcs_client.read_cached_markdown(cache_path)
    
    # Process with docling
    markdown = self._process_with_docling(source_path, content)
    
    # Cache result
    self.gcs_client.upload_markdown(cache_path, markdown)
    
    return markdown
```

---

### Phase 3: Cleanup & CLI

#### Task 3.1: Remove Deprecated Files
**Effort**: Small  

Delete the following files:
- `src/index_loader.py`
- `src/index_tracker.py`
- `index_local.py`
- `index_files.py`
- `extract.py`

Update `src/__init__.py` to remove exports.

---

#### Task 3.2: Update CLI Entry Point
**File**: `main.py`  
**Effort**: Small  

Replace with simple CLI:

```python
import argparse
from src.pipeline import Pipeline
from src.config import load_config

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prefix", help="GCS prefix to process")
    parser.add_argument("--config", default="config.toml")
    args = parser.parse_args()
    
    config = load_config(args.config)
    pipeline = Pipeline(config)
    summary = pipeline.run(dry_run=args.dry_run, prefix=args.prefix)
    print(summary)

if __name__ == "__main__":
    main()
```

---

#### Task 3.3: Update Configuration
**File**: `src/config.py`, `config.example.toml`  
**Effort**: Small  

Remove index.json related configuration. Simplify to:

```toml
[gcs]
source_bucket = "vista-rpms-docs"
cache_bucket = "vista-rpms-docs"
source_prefix = ""
cache_prefix = "cache/"

[qdrant]
url = "https://qdrant.cicd.civicactions.net"
default_collection = "vista"

[[routing]]
pattern = "rpms"
collection = "rpms"
```

---

#### Task 3.4: Update .gitignore
**File**: `.gitignore`  
**Effort**: Trivial  

Remove entries for:
- `.index_tracker.db`
- `index.json`

---

#### Task 3.5: Update README
**File**: `README.md`  
**Effort**: Small  

Update documentation to reflect new architecture:
- Remove references to index.json
- Remove references to SQLite tracking
- Update CLI usage examples
- Update architecture diagram

---

### Phase 4: Testing & Validation

#### Task 4.1: Test GCS Iteration
**Effort**: Medium  

Verify pipeline correctly iterates all GCS blobs and handles:
- Empty prefix
- Nested directories
- Large file counts

---

#### Task 4.2: Test Skip Detection
**Effort**: Medium  

Verify Qdrant-based skip detection:
- Files indexed in previous run are skipped
- New files are processed
- Files in archives are correctly tracked

---

#### Task 4.3: Test Archive Processing
**Effort**: Medium  

Verify archive handling:
- ZIP files extracted correctly
- TAR files extracted correctly
- Nested archives handled
- Archive paths include parent archive name

---

#### Task 4.4: Test Dry Run Mode
**Effort**: Small  

Verify dry-run mode:
- Reports actions without executing
- No Qdrant writes
- No GCS cache writes

---

## Task Summary

| Phase | Task | Effort | Dependencies |
|-------|------|--------|--------------|
| 1 | 1.1 GCS Blob Iteration | Small | None |
| 1 | 1.2 Qdrant Path Check | Small | None |
| 1 | 1.3 Archive Direct Scan | Medium | None |
| 2 | 2.1 New Pipeline Core | Large | 1.1, 1.2 |
| 2 | 2.2 Archive Processing | Medium | 1.3, 2.1 |
| 2 | 2.3 GCS Cache Integration | Small | 2.1 |
| 3 | 3.1 Remove Deprecated | Small | 2.1 |
| 3 | 3.2 Update CLI | Small | 2.1 |
| 3 | 3.3 Update Config | Small | 2.1 |
| 3 | 3.4 Update .gitignore | Trivial | 3.1 |
| 3 | 3.5 Update README | Small | All |
| 4 | 4.1-4.4 Testing | Medium | All |

## Estimated Total Effort

- **Small tasks**: 7 × ~30min = 3.5 hours
- **Medium tasks**: 4 × ~1.5hr = 6 hours
- **Large tasks**: 1 × ~3hr = 3 hours
- **Testing**: ~4 hours

**Total**: ~16-20 hours of implementation work

## Success Criteria Mapping

| Criterion | Validated By |
|-----------|--------------|
| SC-001: Single command | Task 3.2 (CLI) |
| SC-002: Fast re-runs | Task 1.2 (Qdrant skip check) |
| SC-003: Correct classification | Existing file_classifier.py |
| SC-004: Archive extraction | Tasks 1.3, 2.2 |
| SC-005: No local tracking | Tasks 3.1, 3.4 |
| SC-006: Resume capability | Task 1.2 (Qdrant as source of truth) |
| SC-007: All file types | Task 2.1 (unified pipeline) |
