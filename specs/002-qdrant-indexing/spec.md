# Feature Specification: Qdrant Vector Database Indexing

**Feature Branch**: `002-qdrant-indexing`  
**Created**: 2026-01-17  
**Status**: Draft  
**Input**: User description: "Build out the qdrant functionality - from a performance perspective it probably makes sense to load each file after we convert it. We can skip files already imported into qdrant. We will need a vista index configured (with most files going there) and an rpms index (with ihs files). For testing, bring up a server instance with docker."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Index Converted Documents in Real-Time (Priority: P1)

As a pipeline operator, I want converted markdown documents to be automatically indexed into Qdrant immediately after conversion so that documents become searchable without requiring a separate batch process.

**Why this priority**: This is the core value proposition - integrating document conversion with vector indexing eliminates manual steps and ensures documents are searchable as soon as they are processed.

**Independent Test**: Run the pipeline on 5 documents with Qdrant running locally; verify all 5 appear in the Qdrant collection with correct content and metadata.

**Acceptance Scenarios**:

1. **Given** a document is successfully converted to markdown, **When** the pipeline completes processing that document, **Then** the document content is indexed in the appropriate Qdrant collection within the same pipeline run
2. **Given** the Qdrant server is unavailable, **When** attempting to index a document, **Then** the failure is logged and the pipeline continues processing remaining documents
3. **Given** a document is indexed, **When** searching for terms contained in that document, **Then** the document is returned in search results with relevant metadata

---

### User Story 2 - Route Documents to Correct Collection (Priority: P1)

As a data administrator, I want IHS/RPMS documents routed to a dedicated "rpms" collection while all other VistA documents go to a "vista" collection so that searches can be scoped appropriately for different user communities.

**Why this priority**: The routing logic is fundamental to the architecture - without it, users cannot distinguish between VistA and RPMS documentation, which serve different user bases.

**Independent Test**: Process a mix of 3 IHS documents and 3 general VistA documents; verify 3 appear in "rpms" collection and 3 in "vista" collection.

**Acceptance Scenarios**:

1. **Given** a document with source path matching a configured routing pattern, **When** the document is indexed, **Then** it is stored in the collection specified by that pattern
2. **Given** a document that does not match any configured routing pattern, **When** the document is indexed, **Then** it is stored in the default collection ("vista")
3. **Given** the routing rules are applied, **When** querying each collection, **Then** only documents matching that collection's routing patterns are returned

---

### User Story 3 - Skip Already-Indexed Documents (Priority: P2)

As a pipeline operator, I want the system to skip documents that have already been indexed in Qdrant so that re-running the pipeline is efficient and does not create duplicate entries.

**Why this priority**: Enables incremental processing - users can add new documents to the source bucket and re-run the pipeline without re-indexing everything, saving time and compute resources.

**Independent Test**: Index 5 documents, then re-run the pipeline on the same documents; verify 0 new indexing operations occur and existing documents remain unchanged.

**Acceptance Scenarios**:

1. **Given** a document has already been indexed in Qdrant, **When** the pipeline encounters that document, **Then** it skips indexing and logs "already indexed"
2. **Given** a document content has changed since last indexing, **When** the pipeline encounters that document, **Then** it re-indexes the document with updated content
3. **Given** dry-run mode is enabled, **When** checking for already-indexed documents, **Then** the system reports which documents would be skipped without making changes

---

### User Story 4 - Local Development with Docker (Priority: P2)

As a developer, I want to run Qdrant locally using Docker so that I can test indexing functionality without requiring access to a production Qdrant instance.

**Why this priority**: Essential for development workflow - developers need a local testing environment before deploying to production.

**Independent Test**: Start Qdrant via provided Docker command, run pipeline, verify collections are created and documents are searchable via Qdrant dashboard.

**Acceptance Scenarios**:

1. **Given** Docker is installed, **When** running the provided Docker command, **Then** Qdrant starts and is accessible at localhost:6333
2. **Given** Qdrant is running locally, **When** running the pipeline with default configuration, **Then** documents are indexed to the local Qdrant instance
3. **Given** Qdrant is running locally, **When** accessing localhost:6333/dashboard, **Then** the web UI shows indexed collections and allows querying

---

### User Story 5 - Configure Qdrant Connection (Priority: P3)

As a system administrator, I want to configure the Qdrant connection details (URL, API key, collection names) via configuration so that I can deploy to different environments without code changes.

**Why this priority**: Required for production deployment but not essential for initial functionality testing.

**Independent Test**: Set Qdrant URL via config file, run pipeline, verify documents are indexed to the configured endpoint.

**Acceptance Scenarios**:

1. **Given** a Qdrant URL is specified in config.toml, **When** the pipeline runs, **Then** it connects to the specified Qdrant instance
2. **Given** an API key is specified in config or environment variable, **When** connecting to Qdrant, **Then** the API key is used for authentication
3. **Given** custom collection names are specified in config, **When** indexing documents, **Then** the custom collection names are used instead of defaults

---

### Edge Cases

- What happens when Qdrant connection times out during indexing?
- How does the system handle documents that exceed Qdrant payload size limits?
- What happens if the embedding generation fails for a specific document?
- How are special characters in document content handled during vectorization?
- What happens when a collection does not exist on first run?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST index each converted markdown document into Qdrant immediately after successful conversion
- **FR-002**: System MUST route documents to a configured default collection when no routing rules match
- **FR-003**: System MUST support configurable path-to-collection routing rules in config.toml (e.g., paths containing "ihs" route to "rpms" collection)
- **FR-004**: System MUST evaluate routing rules in order, using the first matching rule
- **FR-004**: System MUST skip indexing documents that already exist in Qdrant (matching by source path)
- **FR-005**: System MUST create collections automatically if they do not exist on first run
- **FR-006**: System MUST store document metadata (source path, conversion timestamp, file size, original format) as Qdrant payload
- **FR-007**: System MUST continue processing remaining documents if indexing fails for a single document
- **FR-008**: System MUST log indexing failures with document path and error details
- **FR-009**: System MUST support configurable Qdrant URL (default: http://localhost:6333)
- **FR-010**: System MUST support optional API key authentication for Qdrant Cloud deployments
- **FR-011**: System MUST include indexing statistics in pipeline summary (indexed count, skipped count, failed count)
- **FR-012**: System MUST respect dry-run mode - no actual indexing when dry-run is enabled
- **FR-013**: System MUST generate embeddings for the full markdown text content
- **FR-014**: System MUST re-index documents when content hash differs from previously indexed version

### Key Entities

- **QdrantDocument**: Represents a document to be indexed - contains markdown content, source path, metadata, and generated embedding vector
- **Collection**: Qdrant collection (either "vista" or "rpms") - stores vectors with payload metadata, supports similarity search
- **IndexingResult**: Outcome of indexing a single document - success/skip/failure status with optional error message

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Documents are searchable in Qdrant within 5 seconds of conversion completion
- **SC-002**: Re-running the pipeline on 100 already-indexed documents completes in under 30 seconds (skip detection is fast)
- **SC-003**: 100% of successfully converted documents are indexed (zero silent failures)
- **SC-004**: Pipeline summary accurately reports indexing statistics matching actual Qdrant state
- **SC-005**: Local Docker setup works on first attempt following provided instructions
- **SC-006**: Document routing achieves 100% accuracy according to configured routing rules

## Assumptions

- Qdrant Python client (qdrant-client) will be used for all Qdrant operations
- Embeddings will be generated using FastEmbed with `sentence-transformers/all-MiniLM-L6-v2` model (384 dimensions)
- This model is compatible with mcp-server-qdrant for future MCP integration
- The embedding model runs locally via ONNX (no external API calls)
- Document identity is determined by source path (GCS blob path) - same path = same document
- Content change detection uses a hash of the markdown content
- Collections use cosine similarity distance metric
- Routing rules are evaluated in order; first match wins
- Default routing configuration: paths containing "ihs" route to "rpms", all others to "vista"

## Future Integration

This feature indexes documents in a format compatible with [mcp-server-qdrant](https://github.com/qdrant/mcp-server-qdrant), enabling future semantic search via MCP (Model Context Protocol). The indexed collections can be queried using:
```
QDRANT_URL="http://localhost:6333" COLLECTION_NAME="vista" uvx mcp-server-qdrant
```

## Out of Scope

- Qdrant Cloud managed service setup (only local Docker for this feature)
- Search API implementation (indexing only - search will be a separate feature)
- Batch re-indexing of already-converted documents (only processes during conversion)
- Multi-tenancy or access control within Qdrant
- Vector quantization or advanced Qdrant performance tuning
