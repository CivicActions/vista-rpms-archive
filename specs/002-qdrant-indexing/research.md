# Research: Qdrant Vector Database Indexing

**Feature**: 002-qdrant-indexing  
**Date**: 2026-01-17

## Research Tasks

### 1. Qdrant Client API for Python

**Decision**: Use `qdrant-client[fastembed]` package

**Rationale**:
- Official Qdrant Python client with FastEmbed integration
- FastEmbed provides local ONNX-based embeddings (no API calls needed)
- Same embedding model used by mcp-server-qdrant ensures compatibility
- Supports both sync and async operations
- Batch upsert for efficient bulk indexing

**Alternatives considered**:
- sentence-transformers directly: Requires more code, heavier dependency (PyTorch)
- OpenAI embeddings: Requires API key, external calls, privacy concerns
- Fastembed standalone: Would need manual integration with qdrant-client

**Key API patterns**:
```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

# Connect
client = QdrantClient(url="http://localhost:6333")

# Create collection (384 dims for all-MiniLM-L6-v2)
client.create_collection(
    collection_name="vista",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE)
)

# Upsert with embedded vectors
client.upsert(
    collection_name="vista",
    points=[
        PointStruct(id=hash(path), vector=embedding, payload={"source_path": path, ...})
    ]
)

# Check if point exists (for skip detection)
client.retrieve(collection_name="vista", ids=[point_id])
```

### 2. Embedding Model Selection

**Decision**: `sentence-transformers/all-MiniLM-L6-v2`

**Rationale**:
- Default model for mcp-server-qdrant (ensures compatibility)
- 384 dimensions - good balance of quality and storage
- Fast inference on CPU via ONNX (FastEmbed)
- Well-tested for semantic similarity tasks
- No external API calls required

**Alternatives considered**:
- all-mpnet-base-v2: Better quality but 768 dims (2x storage), slower
- text-embedding-3-small (OpenAI): Requires API key, external calls
- BGE models: Good quality but less tested with mcp-server-qdrant

### 3. Document Identity and Skip Detection

**Decision**: Use source path hash as point ID, store content hash in payload

**Rationale**:
- Source path (GCS blob path) uniquely identifies a document
- Content hash detects when document needs re-indexing
- Point ID from path hash enables O(1) lookup for skip detection
- Payload stores content_hash for change detection

**Implementation**:
```python
import hashlib

def get_point_id(source_path: str) -> int:
    """Generate deterministic point ID from source path."""
    return int(hashlib.md5(source_path.encode()).hexdigest()[:16], 16)

def get_content_hash(content: str) -> str:
    """Generate content hash for change detection."""
    return hashlib.sha256(content.encode()).hexdigest()[:32]
```

### 4. Collection Routing Configuration

**Decision**: TOML config with pattern-to-collection mapping

**Rationale**:
- Consistent with existing config.toml usage
- Patterns evaluated in order (first match wins)
- Default collection for unmatched paths
- Easy to extend for additional collections

**Config format**:
```toml
[qdrant]
url = "http://localhost:6333"
api_key = ""  # Optional, for Qdrant Cloud
default_collection = "vista"

[[qdrant.routing]]
pattern = "ihs"
collection = "rpms"

[[qdrant.routing]]
pattern = "rpms"
collection = "rpms"
```

### 5. MCP Server Compatibility

**Decision**: Store documents in format compatible with mcp-server-qdrant

**Rationale**:
- Enables semantic search via MCP without additional tooling
- Same embedding model ensures vector compatibility
- Payload structure matches mcp-server-qdrant expectations

**mcp-server-qdrant usage**:
```bash
# After indexing, query via MCP
QDRANT_URL="http://localhost:6333" \
COLLECTION_NAME="vista" \
EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2" \
uvx mcp-server-qdrant
```

### 6. Docker Setup for Local Development

**Decision**: Standard Qdrant Docker image

**Command**:
```bash
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

**Dashboard**: http://localhost:6333/dashboard

## Resolved Clarifications

| Item | Resolution |
|------|------------|
| Embedding model | sentence-transformers/all-MiniLM-L6-v2 (384 dims) |
| Skip detection | Point ID from path hash + content hash in payload |
| Collection routing | TOML config with pattern matching |
| MCP compatibility | Same model as mcp-server-qdrant |
