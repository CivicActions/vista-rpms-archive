# vista-rpms-archive Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-01-17

## Active Technologies
- Python 3.11 + qdrant-client[fastembed] (includes FastEmbed for local embeddings) (002-qdrant-indexing)
- Qdrant vector database (Docker for local, configurable URL for production) (002-qdrant-indexing)

- Python 3.11+ + docling, google-cloud-storage, tomli (config parsing) (001-doc-extraction)

## Project Structure

```text
src/
tests/
```

## Commands

cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style

Python 3.11+: Follow standard conventions

## Recent Changes
- 002-qdrant-indexing: Added Python 3.11 + qdrant-client[fastembed] (includes FastEmbed for local embeddings)

- 001-doc-extraction: Added Python 3.11+ + docling, google-cloud-storage, tomli (config parsing)

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
