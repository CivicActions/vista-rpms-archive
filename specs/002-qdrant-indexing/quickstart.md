# Quickstart: Qdrant Vector Database Indexing

## Prerequisites

- Docker installed and running
- Python 3.11+ with uv
- Existing document extraction pipeline working

## 1. Start Qdrant with Docker

```bash
# Start Qdrant server with persistent storage
docker run -d --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant

# Verify it's running
curl http://localhost:6333/health
# Should return: {"title":"qdrant - vector search engine","version":"..."}
```

Access the dashboard at: http://localhost:6333/dashboard

## 2. Configure Qdrant in config.toml

Add to your existing `config.toml`:

```toml
[qdrant]
url = "http://localhost:6333"
default_collection = "vista"
embedding_model = "sentence-transformers/all-MiniLM-L6-v2"

[[qdrant.routing]]
pattern = "ihs"
collection = "rpms"
```

## 3. Install Dependencies

```bash
# Add qdrant-client with fastembed
uv add "qdrant-client[fastembed]"
```

## 4. Run Pipeline with Indexing

```bash
# Process documents - they'll be indexed automatically after conversion
python extract.py --config config.toml --limit 10

# Check collections in dashboard
open http://localhost:6333/dashboard
```

## 5. Query with mcp-server-qdrant (Optional)

```bash
# Install mcp-server-qdrant
uvx mcp-server-qdrant --help

# Run MCP server for the vista collection
QDRANT_URL="http://localhost:6333" \
COLLECTION_NAME="vista" \
EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2" \
uvx mcp-server-qdrant

# Or for RPMS collection
QDRANT_URL="http://localhost:6333" \
COLLECTION_NAME="rpms" \
uvx mcp-server-qdrant
```

## Verification

1. **Check collection counts**:
   ```bash
   curl http://localhost:6333/collections/vista
   curl http://localhost:6333/collections/rpms
   ```

2. **Test search** (via dashboard):
   - Go to http://localhost:6333/dashboard
   - Select collection (vista or rpms)
   - Use "Search" tab to query documents

3. **Re-run pipeline** (should skip indexed docs):
   ```bash
   python extract.py --config config.toml --limit 10
   # Should show "skipped" count > 0
   ```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Connection refused | Ensure Docker container is running: `docker ps` |
| Collection not found | Pipeline auto-creates collections on first run |
| Slow first run | FastEmbed downloads model on first use (~100MB) |
| API key error | Leave `api_key` empty for local Docker |
