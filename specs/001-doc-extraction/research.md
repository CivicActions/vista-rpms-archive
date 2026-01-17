# Research: Document Extraction Pipeline

**Feature**: 001-doc-extraction  
**Date**: 2026-01-17

## Research Questions

1. How to use docling Python API for document extraction?
2. How to efficiently interact with GCS for batch file operations?
3. Best practices for parallel file processing with concurrent.futures?

---

## 1. Docling Python API

### Decision: Use DocumentConverter with PdfPipelineOptions

### Key API Components

```python
from docling.document_converter import DocumentConverter
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfFormatOption
from docling_core.types.doc import ImageRefMode
```

### Configuration for No-OCR + Image Placeholders

```python
# Disable OCR (equivalent to --no-ocr)
pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = False
pipeline_options.do_table_structure = True  # Keep table extraction

# Create converter
converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
    }
)

# Export with image placeholders (equivalent to --image-export-mode placeholder)
markdown = result.document.export_to_markdown(
    image_mode=ImageRefMode.PLACEHOLDER,
    image_placeholder="<!-- image -->"
)
```

### Supported Formats

- PDF (primary, with full pipeline options)
- DOCX, PPTX, XLSX (Office formats)
- HTML, Images

### Memory Management Options

| Option | Purpose | Recommended Value |
|--------|---------|-------------------|
| `max_num_pages` | Limit pages processed | 500 |
| `max_file_size` | Limit file size (bytes) | 50MB |
| `document_timeout` | Processing timeout | 120s |

### Rationale
Docling provides a clean Python API that mirrors its CLI functionality. The `ImageRefMode.PLACEHOLDER` mode is exactly what we need to avoid bloating markdown with base64 images.

### Alternatives Considered
- **pypdf/pdfminer**: Lower-level, no Office format support
- **unstructured**: Heavier dependency, more complex API
- **Apache Tika**: Java dependency, harder to deploy

---

## 2. Google Cloud Storage Client

### Decision: Single client instance with connection pooling

### Key Patterns

```python
from google.cloud import storage
from google.api_core import retry

# Create ONE client and reuse (handles connection pooling)
client = storage.Client()
bucket = client.bucket(bucket_name)

# Check existence (lightweight HEAD request)
blob = bucket.blob(blob_name)
exists = blob.exists(timeout=60)

# Download to temp file
blob.download_to_filename(str(temp_path), retry=retry.DEFAULT_RETRY)

# Upload markdown
blob.upload_from_string(content, content_type="text/markdown", retry=retry.DEFAULT_RETRY)
```

### Error Handling

```python
from google.cloud.exceptions import NotFound, Forbidden, GoogleCloudError

try:
    content = blob.download_as_bytes()
except NotFound:
    # File doesn't exist
except Forbidden:
    # Permission denied
except GoogleCloudError:
    # Other GCS errors (retryable via retry.DEFAULT_RETRY)
```

### Rationale
The google-cloud-storage client has built-in connection pooling, retry logic for transient errors (408, 429, 5xx), and efficient blob.exists() for cache checking.

### Alternatives Considered
- **gcsfs**: Higher-level but adds filesystem abstraction overhead
- **gsutil subprocess**: Less control, harder error handling
- **Direct HTTP**: Unnecessary complexity

---

## 3. Parallel Processing with concurrent.futures

### Decision: ThreadPoolExecutor for I/O, single-stage pipeline

### Architecture Choice

For our workload (GCS I/O + docling CPU), we considered:

| Pattern | Complexity | Benefit |
|---------|------------|---------|
| Single ThreadPoolExecutor | Low | Simpler, GIL released during I/O |
| Single ProcessPoolExecutor | Low | True parallelism for CPU |
| Mixed pipeline (Thread→Process→Thread) | High | Optimal throughput |

**Decision**: Start with **ThreadPoolExecutor** for simplicity (Constitution III). Docling releases GIL during heavy computation. If profiling shows CPU bottleneck, upgrade to ProcessPoolExecutor.

### Progress Tracking Pattern

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import threading

@dataclass
class Progress:
    total: int
    completed: int = 0
    failed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def complete(self, success: bool):
        with self._lock:
            if success:
                self.completed += 1
            else:
                self.failed += 1
```

### Error Isolation Pattern

```python
# Submit all tasks
futures = {executor.submit(process, item): item for item in items}

# Process results as they complete - errors don't affect others
for future in as_completed(futures):
    item = futures[future]
    try:
        result = future.result()
        yield ProcessingResult(item.id, success=True, result=result)
    except Exception as e:
        logger.error(f"Failed {item.id}: {e}")
        yield ProcessingResult(item.id, success=False, error=str(e))
```

### Memory-Bounded Processing

```python
# Limit concurrent futures to control memory
max_pending = 20  # Configurable based on available memory

while pending_futures or more_items:
    # Only submit up to max_pending
    while len(pending_futures) < max_pending and more_items:
        future = executor.submit(process, next_item())
        pending_futures[future] = next_item
    
    # Wait for some to complete before submitting more
    done, _ = wait(pending_futures, return_when=FIRST_COMPLETED)
    for future in done:
        yield process_result(future)
        del pending_futures[future]
```

### Rationale
concurrent.futures is standard library (Constitution III: Simplicity), provides clean error isolation, and supports memory-bounded processing through backpressure.

### Alternatives Considered
- **asyncio**: More complex, docling isn't async-native
- **multiprocessing.Pool**: Less flexible error handling
- **ray/dask**: Overkill for single-machine processing

---

## Configuration File Format

### Decision: TOML format

```toml
[gcs]
source_bucket = "my-bucket"
source_prefix = "data/"
cache_bucket = "my-bucket"
cache_prefix = "cache/"

[processing]
workers = 8  # Default: os.cpu_count()
max_pending = 20
timeout = 120

[extraction]
max_pages = 500
max_file_size = 52428800  # 50MB

[qdrant]
# Stub for future use
host = "localhost"
port = 6333
collection = "documents"
```

### Rationale
TOML is human-readable, well-supported in Python 3.11+ (tomllib), and commonly used for Python project configuration.

---

## Summary of Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Document extraction | docling with PdfPipelineOptions | Clean API, Office format support |
| OCR setting | `do_ocr = False` | Per requirements |
| Image handling | `ImageRefMode.PLACEHOLDER` | Avoid base64 bloat |
| GCS client | Single reused storage.Client | Connection pooling |
| Cache check | `blob.exists()` | Lightweight HEAD request |
| Parallelism | ThreadPoolExecutor | Simplicity, GIL released during I/O |
| Error handling | try/except per task | Isolation, continue on failure |
| Memory control | max_pending backpressure | Bounded memory usage |
| Config format | TOML | Standard, readable |
