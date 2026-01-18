# Vista RPMS Archive

Document extraction pipeline for Vista RPMS Archive - extracts office documents to markdown and indexes them in Qdrant for semantic search.

## Features

- Extract text from PDF, DOCX, HTML, and other office documents
- Convert to markdown format for LLM consumption
- Index documents in Qdrant vector database for semantic search
- Configurable routing to multiple collections (vista, rpms)
- Skip already-indexed documents for efficient re-runs
- Compatible with [mcp-server-qdrant](https://github.com/qdrant/mcp-server-qdrant) for MCP integration

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker (for local Qdrant)
- Google Cloud credentials (for GCS access)

## Quick Start

### 1. Install Dependencies

```bash
uv sync
```

### 2. Start Qdrant with Docker

```bash
# Start Qdrant server with persistent storage
docker run -d --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant

# Verify it's running
curl http://localhost:6333/health
```

Access the dashboard at: http://localhost:6333/dashboard

### 3. Configure

Create `config.toml`:

```toml
source_bucket = "vista-rpms-archive"
source_prefix = "source/"
cache_bucket = "vista-rpms-archive"
cache_prefix = "cache/"
workers = 4

[qdrant]
url = "http://localhost:6333"
default_collection = "vista"

[[qdrant.routing]]
pattern = "ihs"
collection = "rpms"
```

### 4. Run Pipeline

```bash
# Set GCS credentials
export GOOGLE_APPLICATION_CREDENTIALS=path/to/credentials.json

# Run extraction and indexing
python extract.py --config config.toml --limit 100

# Dry run (no changes)
python extract.py --config config.toml --dry-run
```

## MCP Integration

After indexing, query documents via MCP:

```bash
# Run MCP server for vista collection
QDRANT_URL="http://localhost:6333" \
COLLECTION_NAME="vista" \
uvx mcp-server-qdrant
```

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `qdrant.url` | `http://localhost:6333` | Qdrant server URL |
| `qdrant.api_key` | (empty) | API key for Qdrant Cloud |
| `qdrant.default_collection` | `vista` | Default collection for unmatched docs |
| `qdrant.routing[].pattern` | - | Substring to match in source path |
| `qdrant.routing[].collection` | - | Target collection for matched docs |

Environment variable overrides:
- `QDRANT_URL` - Override qdrant.url
- `QDRANT_API_KEY` - Override qdrant.api_key

## License

Apache 2.0
