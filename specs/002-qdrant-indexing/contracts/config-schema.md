# Qdrant Configuration Schema

## config.toml additions

```toml
# =============================================================================
# Qdrant Vector Database Settings
# =============================================================================

[qdrant]
# Qdrant server URL (use http://localhost:6333 for local Docker)
url = "http://localhost:6333"

# Optional API key for Qdrant Cloud
api_key = ""

# Default collection for documents that don't match any routing rule
default_collection = "vista"

# Embedding model (must match mcp-server-qdrant for compatibility)
embedding_model = "sentence-transformers/all-MiniLM-L6-v2"

# =============================================================================
# Collection Routing Rules
# Evaluated in order - first matching pattern wins
# =============================================================================

[[qdrant.routing]]
# Route IHS (Indian Health Service) documents to RPMS collection
pattern = "ihs"
collection = "rpms"

[[qdrant.routing]]
# Route RPMS-specific documents to RPMS collection
pattern = "rpms"
collection = "rpms"

# Documents not matching any rule go to default_collection ("vista")
```

## Collection Configuration

Each collection is created with:

```json
{
  "vectors_config": {
    "size": 384,
    "distance": "Cosine"
  }
}
```

## Environment Variable Overrides

| Variable | Config Key | Description |
|----------|------------|-------------|
| `QDRANT_URL` | qdrant.url | Qdrant server URL |
| `QDRANT_API_KEY` | qdrant.api_key | API key for authentication |
| `QDRANT_COLLECTION` | qdrant.default_collection | Default collection name |
