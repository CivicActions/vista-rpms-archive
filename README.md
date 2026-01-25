# Vista RPMS Archive

GCS-first document processing pipeline for Vista RPMS Archive. Iterates directly over GCS bucket contents, classifies files by content, and indexes them to appropriate Qdrant collections for semantic search.

## Features

- **GCS-first architecture**: Iterates directly over GCS bucket - no index.json required
- **Content-based classification**: Detects documentation vs source code vs MUMPS using python-magic and content analysis
- **Smart routing**: Documentation → `vista`/`rpms`, Source code → `vista-source`/`rpms-source`
- **Qdrant-based tracking**: Uses Qdrant as source of truth for skip detection - no local SQLite
- **Archive support**: Automatically extracts and indexes files from ZIP/TAR archives
- **GCS caching**: Caches converted markdown in GCS for fast re-runs
- **Resume capability**: Can resume after interruption without re-processing
- **Dry run mode**: Preview what would be processed without making changes
- Compatible with [mcp-server-qdrant](https://github.com/qdrant/mcp-server-qdrant) for MCP integration

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Google Cloud credentials (for GCS access)
- Qdrant server (local or cloud)

## Quick Start

### 1. Install Dependencies

```bash
uv sync
```

### 2. Start Qdrant (optional - for local development)

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

Copy `config.example.toml` to `config.toml` and customize:

```toml
[gcs]
source_bucket = "vista-rpms-docs"
source_prefix = ""
cache_bucket = "vista-rpms-docs"
cache_prefix = "cache/"

[qdrant]
url = "https://qdrant.cicd.civicactions.net"
default_collection = "vista"

[[qdrant.routing]]
pattern = "rpms"
collection = "rpms"
```

### 4. Set Environment Variables

```bash
# GCS credentials
export GOOGLE_APPLICATION_CREDENTIALS=path/to/credentials.json

# Qdrant credentials (optional - can be set in config.toml)
export QDRANT_URL="https://qdrant.cicd.civicactions.net"
export QDRANT_API_KEY="your-api-key"
```

### 5. Run Pipeline

```bash
# Process all files in GCS bucket
python main.py --config config.toml

# Dry run (preview what would be processed)
python main.py --config config.toml --dry-run

# Process only first 100 files
python main.py --config config.toml --limit 100

# Process files under specific prefix
python main.py --config config.toml --prefix "data/reports/"

# Force re-indexing (skip Qdrant existence check)
python main.py --config config.toml --force

# Enable debug logging
python main.py --config config.toml --log-level DEBUG
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Pipeline Entry                            │
│                     python main.py --config ...                  │
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
│      qdrant_client.exists_by_path(source_path)                   │
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

## Collections

| Collection | Content |
|------------|---------|
| `vista` | VistA documentation (manuals, guides, PDFs, text files) |
| `rpms` | RPMS/IHS documentation |
| `vista-source` | VistA source code (MUMPS routines, globals, other code) |
| `rpms-source` | RPMS source code |

## File Classification

The pipeline uses content-based classification to determine how to handle each file:

| Category | Detection Method | Routing |
|----------|-----------------|---------|
| DOCUMENTATION | .md, .txt, .pdf, .docx, .html, etc. | vista/rpms |
| SOURCE_CODE | .py, .js, .c, etc. with code patterns | vista-source/rpms-source |
| MUMPS_ROUTINE | .m files with MUMPS patterns | vista-source/rpms-source |
| MUMPS_GLOBAL | .zwr, .gsa with global patterns | vista-source/rpms-source |
| DATA | .csv, .json with data patterns | vista-source/rpms-source |
| BINARY | Images, executables, etc. | Skipped |

## MCP Integration

After indexing, query documents via MCP using [mcp-server-qdrant](https://github.com/qdrant/mcp-server-qdrant).

### Claude Desktop / VS Code Configuration

Add to your MCP settings (read-only access):

```json
{
  "mcpServers": {
    "qdrant-vista-rpms": {
      "command": "uvx",
      "args": ["mcp-server-qdrant", "--read-only"],
      "env": {
        "QDRANT_URL": "https://qdrant.cicd.civicactions.net",
        "QDRANT_API_KEY": "your-api-key",
        "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2"
      }
    }
  }
}
```

## Configuration Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `gcs.source_bucket` | (required) | GCS bucket containing source files |
| `gcs.source_prefix` | `""` | Prefix path within source bucket |
| `gcs.cache_bucket` | (required) | GCS bucket for cached markdown output |
| `gcs.cache_prefix` | `"cache/"` | Prefix path for cached files |
| `processing.workers` | CPU count | Number of parallel workers |
| `extraction.max_pages` | 500 | Maximum pages per document |
| `extraction.max_file_size` | 50MB | Maximum file size to process |
| `qdrant.url` | `http://localhost:6333` | Qdrant server URL |
| `qdrant.api_key` | `""` | API key for Qdrant Cloud |
| `qdrant.default_collection` | `vista` | Default collection for unmatched docs |
| `qdrant.routing[].pattern` | - | Substring or regex to match in path |
| `qdrant.routing[].collection` | - | Target collection for matched docs |

### Environment Variable Overrides

- `QDRANT_URL` - Override qdrant.url
- `QDRANT_API_KEY` - Override qdrant.api_key
- `GOOGLE_APPLICATION_CREDENTIALS` - GCS service account credentials

## Development

```bash
# Run tests
uv run pytest

# Run linter
uv run ruff check .

# Format code
uv run ruff format .
```

## License

Apache 2.0
