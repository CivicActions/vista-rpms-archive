# VistA & RPMS Archive and Search Index

Archival, vector search, and MCP-based AI access for VistA and RPMS documents and source code. This repository contains the download scripts used to build the ~2TB archive, the Thresher pipeline configuration for indexing, and the Qdrant deployment configuration.

## Requesting access

Anyone working on VistA or RPMS in government or open source is welcome to request access using [this form](https://forms.gle/BEU58m5ttraSwKFT9).

* You will be e-mailed an API key giving you read-only access to the Qdrant search index via the Qdrant API and the MCP server configuration outlined below.
* You will have read only access (via your Google account) to the GCS bucket containing the raw archive files, extracted archives, cached markdown and DoclingDocument conversions, and Qdrant snapshots.
* You will also be subscribed to a Google Group for questions and announcements related to the archive, maintenance, updates etc.

The archive and index is hosted by CivicActions as a service to the VistA & RPMS community. We will try and answer any access questions, but have limited capacity for individual support.

## Archival scripts

The archive is built from several public sources related to VistA and RPMS. All downloaded content is synced to a GCS bucket for indexing.

**Website mirrors** — [download.sh](download.sh) uses HTTrack and wget to mirror:
- [VA Veterans Data Library (VDL)](https://www.va.gov/vdl/)
- [IHS RPMS site](https://www.ihs.gov/rpms/) and [SRCB](https://www.ihs.gov/sites/RPMS/SRCB/)
- [IHS CIS](https://www.ihs.gov/cis/)
- [WorldVistA](http://worldvista.org) and subdomains (code, resources, journal, education, FOIA)
- [Nancy's VistA Server](https://opensourcevista.net/NancysVistAServer/)
- [HardHats](https://hardhats.org)

**IHS RPMS FTP** — [download-ihs-ftp.py](download-ihs-ftp.py) crawls the [IHS RPMS FTP file browser](https://www.ihs.gov/rpms/applications/ftp/), which uses a dynamic JavaScript-based interface that standard mirroring tools can't navigate.

**VistApedia** — [download-vistapedia.py](download-vistapedia.py) crawls the [VistApedia MediaWiki](https://vistapedia.com/) via its API, downloading rendered HTML for all content pages with spam filtering.

**WorldVistA GitHub** — [download.sh](download.sh) also clones all repositories from the [WorldVistA GitHub organization](https://github.com/WorldVistA), including FOIA branches where available.

## MCP server configuration

The archive is searchable via the [`mcp-server-qdrant`](https://github.com/CivicActions/thresher/tree/main/mcp-server) MCP server, which exposes a single `qdrant-find` tool for semantic search across the indexed collections.

All configurations below run the server with [`uvx`](https://docs.astral.sh/uv/guides/tools/), which fetches and runs the server directly from the thresher repo on first use. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first if you don't have it.

> **Note:** Remember to set your API key in the configuration!

### Claude Code

```bash
claude mcp add vista-rpms \
  -e QDRANT_URL='https://qdrant.cicd.civicactions.net:443' \
  -e DEFAULT_COLLECTION='vista' \
  -e QDRANT_READ_ONLY='true' \
  -e QDRANT_API_KEY='<your-api-key>' \
  -e COLLECTIONS='[{"name": "rpms-source", "model": "jinaai/jina-embeddings-v2-base-code", "vector_name": "jina-code-v2", "vector_size": 768, "index_prefix": "", "query_prefix": ""}, {"name": "rpms", "model": "nomic-ai/nomic-embed-text-v1.5", "vector_name": "nomic-v1.5", "vector_size": 768, "index_prefix": "search_document: ", "query_prefix": "search_query: "}, {"name": "vista-source", "model": "jinaai/jina-embeddings-v2-base-code", "vector_name": "jina-code-v2", "vector_size": 768, "index_prefix": "", "query_prefix": ""}, {"name": "vista", "model": "nomic-ai/nomic-embed-text-v1.5", "vector_name": "nomic-v1.5", "vector_size": 768, "index_prefix": "search_document: ", "query_prefix": "search_query: "}]' \
  -e TOOL_FIND_DESCRIPTION='Semantic search over VistA and RPMS documents and source code. Queries should be natural language descriptions of concepts or topics, not keywords. Collections: vista (VistA documentation), vista-source (VistA MUMPS/M source code), rpms (RPMS/IHS documentation), rpms-source (RPMS MUMPS/M source code). Use source_path to filter by file path when you know the package or routine name.' \
  -- uvx --from 'git+https://github.com/CivicActions/thresher@main#subdirectory=mcp-server' mcp-server-qdrant
```

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "vista-rpms": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/CivicActions/thresher@main#subdirectory=mcp-server",
        "mcp-server-qdrant"
      ],
      "env": {
        "QDRANT_URL": "https://qdrant.cicd.civicactions.net:443",
        "DEFAULT_COLLECTION": "vista",
        "QDRANT_READ_ONLY": "true",
        "COLLECTIONS": "[{\"name\": \"rpms-source\", \"model\": \"jinaai/jina-embeddings-v2-base-code\", \"vector_name\": \"jina-code-v2\", \"vector_size\": 768, \"index_prefix\": \"\", \"query_prefix\": \"\"}, {\"name\": \"rpms\", \"model\": \"nomic-ai/nomic-embed-text-v1.5\", \"vector_name\": \"nomic-v1.5\", \"vector_size\": 768, \"index_prefix\": \"search_document: \", \"query_prefix\": \"search_query: \"}, {\"name\": \"vista-source\", \"model\": \"jinaai/jina-embeddings-v2-base-code\", \"vector_name\": \"jina-code-v2\", \"vector_size\": 768, \"index_prefix\": \"\", \"query_prefix\": \"\"}, {\"name\": \"vista\", \"model\": \"nomic-ai/nomic-embed-text-v1.5\", \"vector_name\": \"nomic-v1.5\", \"vector_size\": 768, \"index_prefix\": \"search_document: \", \"query_prefix\": \"search_query: \"}]",
        "TOOL_FIND_DESCRIPTION": "Semantic search over VistA and RPMS documents and source code. Queries should be natural language descriptions of concepts or topics, not keywords — e.g. 'how patient allergies are stored and validated' rather than 'allergy API'. Collections: 'vista' (VistA documentation — manuals, patches, technical guides), 'vista-source' (VistA MUMPS/M routines and source code), 'rpms' (RPMS/IHS documentation), 'rpms-source' (RPMS MUMPS/M routines and source code). Use source_path to filter by file path when you know the package or routine name. Results are document chunks with source file paths — use multiple queries to triangulate across docs and source code.",
        "QDRANT_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

### VS Code

Add to `.vscode/mcp.json` in your workspace or View -> Command Palette -> MCP: Open User Configuration and add:

```json
{
  "servers": {
    "vistaRpms": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/CivicActions/thresher@main#subdirectory=mcp-server",
        "mcp-server-qdrant"
      ],
      "env": {
        "QDRANT_URL": "https://qdrant.cicd.civicactions.net:443",
        "DEFAULT_COLLECTION": "vista",
        "QDRANT_READ_ONLY": "true",
        "COLLECTIONS": "[{\"name\": \"rpms-source\", \"model\": \"jinaai/jina-embeddings-v2-base-code\", \"vector_name\": \"jina-code-v2\", \"vector_size\": 768, \"index_prefix\": \"\", \"query_prefix\": \"\"}, {\"name\": \"rpms\", \"model\": \"nomic-ai/nomic-embed-text-v1.5\", \"vector_name\": \"nomic-v1.5\", \"vector_size\": 768, \"index_prefix\": \"search_document: \", \"query_prefix\": \"search_query: \"}, {\"name\": \"vista-source\", \"model\": \"jinaai/jina-embeddings-v2-base-code\", \"vector_name\": \"jina-code-v2\", \"vector_size\": 768, \"index_prefix\": \"\", \"query_prefix\": \"\"}, {\"name\": \"vista\", \"model\": \"nomic-ai/nomic-embed-text-v1.5\", \"vector_name\": \"nomic-v1.5\", \"vector_size\": 768, \"index_prefix\": \"search_document: \", \"query_prefix\": \"search_query: \"}]",
        "TOOL_FIND_DESCRIPTION": "Semantic search over VistA and RPMS documents and source code. Queries should be natural language descriptions of concepts or topics, not keywords — e.g. 'how patient allergies are stored and validated' rather than 'allergy API'. Collections: 'vista' (VistA documentation — manuals, patches, technical guides), 'vista-source' (VistA MUMPS/M routines and source code), 'rpms' (RPMS/IHS documentation), 'rpms-source' (RPMS MUMPS/M routines and source code). Use source_path to filter by file path when you know the package or routine name. Results are document chunks with source file paths — use multiple queries to triangulate across docs and source code.",
        "QDRANT_API_KEY": "${input:qdrantApiKey}"
      }
    }
  },
  "inputs": [
    {
      "id": "qdrantApiKey",
      "type": "promptString",
      "description": "Qdrant API key (use a read-only key scoped to the search collections)",
      "password": true
    }
  ]
}
```

### Cursor

Add to your Cursor MCP configuration:

```json
{
  "mcpServers": {
    "vista-rpms": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/CivicActions/thresher@main#subdirectory=mcp-server",
        "mcp-server-qdrant"
      ],
      "env": {
        "QDRANT_URL": "https://qdrant.cicd.civicactions.net:443",
        "DEFAULT_COLLECTION": "vista",
        "QDRANT_READ_ONLY": "true",
        "COLLECTIONS": "[{\"name\": \"rpms-source\", \"model\": \"jinaai/jina-embeddings-v2-base-code\", \"vector_name\": \"jina-code-v2\", \"vector_size\": 768, \"index_prefix\": \"\", \"query_prefix\": \"\"}, {\"name\": \"rpms\", \"model\": \"nomic-ai/nomic-embed-text-v1.5\", \"vector_name\": \"nomic-v1.5\", \"vector_size\": 768, \"index_prefix\": \"search_document: \", \"query_prefix\": \"search_query: \"}, {\"name\": \"vista-source\", \"model\": \"jinaai/jina-embeddings-v2-base-code\", \"vector_name\": \"jina-code-v2\", \"vector_size\": 768, \"index_prefix\": \"\", \"query_prefix\": \"\"}, {\"name\": \"vista\", \"model\": \"nomic-ai/nomic-embed-text-v1.5\", \"vector_name\": \"nomic-v1.5\", \"vector_size\": 768, \"index_prefix\": \"search_document: \", \"query_prefix\": \"search_query: \"}]",
        "TOOL_FIND_DESCRIPTION": "Semantic search over VistA and RPMS documents and source code. Queries should be natural language descriptions of concepts or topics, not keywords — e.g. 'how patient allergies are stored and validated' rather than 'allergy API'. Collections: 'vista' (VistA documentation — manuals, patches, technical guides), 'vista-source' (VistA MUMPS/M routines and source code), 'rpms' (RPMS/IHS documentation), 'rpms-source' (RPMS MUMPS/M routines and source code). Use source_path to filter by file path when you know the package or routine name. Results are document chunks with source file paths — use multiple queries to triangulate across docs and source code.",
        "QDRANT_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

### Customization

You may want to adjust these environment variables in your configuration:

- **`DEFAULT_COLLECTION`** — The collection searched when no `collection_name` is specified. Set to whichever collection you query most often (e.g. `vista`, `rpms`).
- **`COLLECTIONS`** — A JSON array defining which collections are available and their embedding models. Remove collections you don't need to reduce noise.
- **`TOOL_FIND_DESCRIPTION`** — The tool description seen by the LLM. Adjust this to change when and how the model decides to search.
- **`QDRANT_SEARCH_LIMIT`** — Default number of results returned per query (default: 10).

## Programmatic access via the Qdrant API

You can also query the index directly over HTTP using the [Qdrant REST API](https://api.qdrant.tech/) — useful for testing your API key, or for building your own tools and scripts on top of the index.

**Test your API key:**

```bash
curl -H "api-key: <your-api-key>" \
  https://qdrant.cicd.civicactions.net/collections
```

This should return JSON listing the four collections (`vista`, `vista-source`, `rpms`, `rpms-source`).

**Run a semantic search:**

The collections do not run embedding inference server-side, so you need to compute the query embedding yourself and POST it. Use the matching model and vector name for the collection you are searching:

| Collection | Model | Vector name | Query prefix |
|---|---|---|---|
| `vista`, `rpms` | `nomic-ai/nomic-embed-text-v1.5` | `nomic-v1.5` | `search_query: ` |
| `vista-source`, `rpms-source` | `jinaai/jina-embeddings-v2-base-code` | `jina-code-v2` | (none) |

A minimal end-to-end Python example using [fastembed](https://github.com/qdrant/fastembed) (the same library the MCP server uses):

```python
import os, requests
from fastembed import TextEmbedding

model = TextEmbedding("nomic-ai/nomic-embed-text-v1.5")
vector = next(model.embed(["search_query: how are patient allergies stored"]))

r = requests.post(
    "https://qdrant.cicd.civicactions.net/collections/vista/points/query",
    headers={"api-key": os.environ["QDRANT_API_KEY"]},
    json={"query": vector.tolist(), "using": "nomic-v1.5",
          "limit": 5, "with_payload": True},
)
for hit in r.json()["result"]["points"]:
    print(hit["score"], hit["payload"]["source"])
```

To narrow results to a specific source file or directory, add a payload filter:

```json
"filter": {"must": [{"key": "source", "match": {"text": "GMRA"}}]}
```

## Indexing details

Documents are indexed into [Qdrant](https://qdrant.tech/), an open-source vector search engine, using [Thresher](https://github.com/CivicActions/thresher), a cloud-native pipeline that converts documents into chunked markdown and indexes them as vector embeddings.

**Collections:**

| Collection | Contents | Embedding model |
|---|---|---|
| `vista` | VistA documents (PDFs, Office files, HTML, text) | `nomic-ai/nomic-embed-text-v1.5` |
| `vista-source` | VistA source code (MUMPS routines, globals, general source) | `jinaai/jina-embeddings-v2-base-code` |
| `rpms` | IHS RPMS documents | `nomic-ai/nomic-embed-text-v1.5` |
| `rpms-source` | IHS RPMS source code | `jinaai/jina-embeddings-v2-base-code` |

The full pipeline configuration — including file type groups, routing rules, chunking strategies, and processing settings — is in [prod-config.yaml](prod-config.yaml).

**File types and chunking:**

Files are classified into groups based on extension, MIME type, and (for ambiguous `.m`, `.ro`, and `.zwr` files) content detectors that look for MUMPS label patterns and caret density. Each group is processed by a specific extractor and chunker. All chunks target ~512 tokens.

| File group | Extensions | Extractor | Chunker | Notes |
|---|---|---|---|---|
| `office-documents` | `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.html`, `.htm`, `.rtf` | [Docling](https://github.com/docling-project/docling) (with OCR for scanned PDFs) | `docling-hybrid` (structure-aware: respects headings, tables, and document layout) | Max 200 MB to accommodate large VistA/RPMS technical manuals |
| `audio-video` | `.wav`, `.mp3`, `.m4a`, `.aac`, `.ogg`, `.flac`, `.mp4`, `.avi`, `.mov` | Docling ASR pipeline using [Whisper Turbo](https://github.com/openai/whisper) for speech-to-text | `chonkie-recursive` | No file size cap; transcribes meeting recordings, demo videos, and audio prompts |
| `mumps-source` | `.m`, `.ro` | raw text | `mumps-label-boundary` (splits on MUMPS routine labels to keep each callable entry point together) | Detected by label-pattern heuristic; 10 MB cap |
| `mumps-globals` | `.zwr` | raw text | `mumps-label-boundary` | Detected by caret-density heuristic; 10 MB cap |
| `general-source` | `.py`, `.js`, `.ts`, `.java`, `.c`, `.cpp`, `.go`, `.rs`, `.rb`, `.php`, `.sh`, `.sql`, etc. | raw text | `chonkie-code` (language-aware AST/syntax-based splitting via [Chonkie](https://github.com/chonkie-inc/chonkie)) | 10 MB cap |
| `plain-text` | `.txt`, `.md`, `.rst`, `.log`, `.readme` | raw text | `chonkie-recursive` (markdown-aware recursive splitting) | |
| `data-files` | `.json`, `.xml`, `.csv`, `.tsv`, `.yaml`, `.toml`, `.ini`, `.cfg` | raw text | `chonkie-recursive` | |
| `images` | `.png`, `.jpg`, `.gif`, `.bmp`, `.tiff`, `.svg` | Docling (OCR) | `docling-hybrid` | Images under 50 KB are skipped to avoid OCR noise |

Archives (`.zip`, `.tar`, `.tar.gz`, `.7z`, etc.) are recursively expanded up to two levels deep before classification. Java/Python packaging archives (`.jar`, `.war`, `.whl`, `.egg`, `.apk`, `.ipa`) are not expanded.

Routing into the four collections is determined by file group and source path: source-code groups under IHS/RPMS paths go to `rpms-source`; other IHS/RPMS files go to `rpms`; other source-code goes to `vista-source`; everything else goes to `vista`. Diff-report directories from Araxis Merge (which made up ~71% of files in expanded archives) are skipped entirely.

Not every file made it into the index — some were skipped due to per-file timeouts, OCR/Docling memory exhaustion, or repeated extraction failures. The raw files remain available in the GCS archive.

## Statistics

| Metric | Count | Size |
|---|---|---|
| Source files (downloaded) | 543,053 | 693.8 GiB |
| Expanded files (from archives) | 5,806,249 | 1,338.0 GiB |
| **Total archived files** | **6,349,302** | **2,031.8 GiB** |

| Collection | Indexed chunks |
|---|---|
| `vista` | 3,553,641 |
| `vista-source` | 4,009,384 |
| `rpms` | 1,299,342 |
| `rpms-source` | 719,607 |
| **Total** | **9,581,974** |

## Qdrant snapshots

Full Qdrant snapshots for all collections are archived in `gs://vista-rpms-archive/snapshots/`. These can be used to restore the vector index without re-running the Thresher pipeline.

| Snapshot | Size | Date |
|---|---|---|
| `vista.snapshot` | 15.26 GiB | 2026-04-19 |
| `vista-source.snapshot` | 17.21 GiB | 2026-04-19 |
| `rpms.snapshot` | 5.52 GiB | 2026-04-18 |
| `rpms-source.snapshot` | 2.9 GiB | 2026-04-18 |
| **Total** | **40.89 GiB** | |

To restore a snapshot to a Qdrant instance:

```bash
curl -X POST 'http://localhost:6333/collections/{collection}/snapshots/upload' \
  -H 'Content-Type: multipart/form-data' \
  -F 'snapshot=@/path/to/{collection}.snapshot'
```

## Archive details

All archival content lives in the GCS bucket `gs://vista-rpms-archive` with the following structure:

| Prefix | Contents |
|---|---|
| `source/` | Raw downloaded files from all sources (website mirrors, FTP, VistApedia, GitHub clones). This is the direct output of the archival scripts. |
| `expanded/` | Contents extracted from archives (ZIPs, tarballs, etc.) found in `source/`, expanded up to 2 levels deep. Each archive has an `.expansion-record.json` for idempotency. |
| `cache/` | Intermediate extraction cache (e.g. Docling document conversions) used to avoid re-processing on subsequent runs. |
| `queue/` | Batch processing state (if running) for the Thresher runner pipeline, split into `pending/`, `claimed/`, and `completed/` sub-prefixes. |

