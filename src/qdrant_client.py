"""Qdrant vector database client for document indexing.

Handles connection, collection management, and document upsert/lookup operations.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

from .embedder import Embedder
from .router import Router
from .types import IndexingResult, QdrantConfig

logger = logging.getLogger(__name__)


def get_point_id(source_path: str) -> int:
    """Generate a deterministic point ID from source path.
    
    Uses MD5 hash truncated to 64-bit unsigned integer for Qdrant compatibility.
    
    Args:
        source_path: GCS source path of the document.
    
    Returns:
        64-bit integer point ID.
    """
    hash_bytes = hashlib.md5(source_path.encode("utf-8")).digest()
    # Take first 8 bytes as unsigned 64-bit integer
    return int.from_bytes(hash_bytes[:8], byteorder="big", signed=False)


def get_content_hash(content: str) -> str:
    """Generate a content hash for change detection.
    
    Uses SHA256 truncated to 32 characters for compactness.
    
    Args:
        content: Document content (markdown text).
    
    Returns:
        32-character hex string.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]


# Maximum payload size Qdrant will accept (32MB).
# Content must be chunked below this to avoid 400 errors.
QDRANT_MAX_PAYLOAD_BYTES = 32 * 1024 * 1024

# Target chunk size in characters. Qdrant's 32MB limit is on the JSON payload
# which includes metadata overhead, so we target ~4MB of text per chunk.
# This also keeps embedding input at a reasonable size.
CHUNK_TARGET_CHARS = 4 * 1024 * 1024  # 4MB of text

# Overlap between chunks in characters to preserve context across boundaries.
CHUNK_OVERLAP_CHARS = 2000


def chunk_text(text: str, chunk_size: int = CHUNK_TARGET_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Split text into overlapping chunks.
    
    Splits on line boundaries where possible to avoid breaking mid-line.
    
    Args:
        text: Full document text.
        chunk_size: Target chunk size in characters.
        overlap: Overlap between chunks in characters.
    
    Returns:
        List of text chunks. Returns [text] if no chunking needed.
    """
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        
        # Try to break at a line boundary within the last 10% of the chunk
        search_start = end - (chunk_size // 10)
        newline_pos = text.rfind('\n', search_start, end)
        if newline_pos > start:
            end = newline_pos + 1  # Include the newline
        
        chunks.append(text[start:end])
        start = end - overlap
    
    return chunks


class QdrantIndexer:
    """Manages document indexing in Qdrant vector database.
    
    Handles:
    - Connection to Qdrant server
    - Collection creation with correct vector configuration
    - Document embedding and upsert
    - Skip detection via content hash comparison
    
    Uses named vectors compatible with mcp-server-qdrant (fast-{model_name}).
    """
    
    VECTOR_SIZE = 384  # all-MiniLM-L6-v2 dimension
    DISTANCE = models.Distance.COSINE
    # Named vector format matching mcp-server-qdrant: fast-{model_name_lowercase}
    VECTOR_NAME = "fast-all-minilm-l6-v2"
    
    def __init__(
        self,
        config: QdrantConfig,
        embedder: Optional[Embedder] = None,
        router: Optional[Router] = None,
    ) -> None:
        """Initialize Qdrant indexer.
        
        Args:
            config: Qdrant configuration.
            embedder: Optional pre-configured embedder (created if not provided).
            router: Optional pre-configured router (created if not provided).
        """
        self.config = config
        self._client: Optional[QdrantClient] = None
        self._embedder = embedder
        self._router = router
        self._collections_checked: set[str] = set()
        
        logger.info(f"QdrantIndexer initialized for {config.url}")
    
    @property
    def client(self) -> QdrantClient:
        """Lazy-load Qdrant client on first use."""
        if self._client is None:
            self._client = self._connect()
        return self._client
    
    @property
    def embedder(self) -> Embedder:
        """Lazy-load embedder on first use."""
        if self._embedder is None:
            self._embedder = Embedder(model_name=self.config.embedding_model)
        return self._embedder
    
    @property
    def router(self) -> Router:
        """Lazy-load router on first use."""
        if self._router is None:
            self._router = Router(
                rules=self.config.routing,
                default_collection=self.config.default_collection,
            )
        return self._router
    
    def _connect(self) -> QdrantClient:
        """Establish connection to Qdrant server.
        
        Returns:
            Connected QdrantClient instance.
        
        Raises:
            ConnectionError: If unable to connect to Qdrant.
        """
        logger.info(f"Connecting to Qdrant at {self.config.url}")
        
        try:
            client = QdrantClient(
                url=self.config.url,
                port=None,  # Don't append default port - URL includes port or uses standard HTTPS 443
                api_key=self.config.api_key if self.config.api_key else None,
                timeout=600,  # 10 minute timeout for large document uploads
                prefer_grpc=False,  # Use HTTP for better firewall compatibility
            )
            # Test connection
            client.get_collections()
            logger.info("Successfully connected to Qdrant")
            return client
        except Exception as e:
            logger.error(f"Failed to connect to Qdrant: {e}")
            raise ConnectionError(f"Failed to connect to Qdrant: {e}") from e
    
    def preload(self) -> None:
        """Preload embedding model before starting parallel workers.
        
        Call this once before processing to avoid race conditions
        with model download progress bars.
        """
        self.embedder.preload()
    
    def ensure_collection(self, collection_name: str) -> None:
        """Create collection if it doesn't exist.
        
        Args:
            collection_name: Name of the collection to create.
        """
        if collection_name in self._collections_checked:
            return
        
        try:
            collection_info = self.client.get_collection(collection_name)
            logger.debug(f"Collection '{collection_name}' exists with "
                        f"{collection_info.points_count} points")
            self._collections_checked.add(collection_name)
        except UnexpectedResponse as e:
            if "not found" in str(e).lower() or e.status_code == 404:
                logger.info(f"Creating collection '{collection_name}'")
                try:
                    # Use named vector config for mcp-server-qdrant compatibility
                    self.client.create_collection(
                        collection_name=collection_name,
                        vectors_config={
                            self.VECTOR_NAME: models.VectorParams(
                                size=self.VECTOR_SIZE,
                                distance=self.DISTANCE,
                            )
                        },
                    )
                    logger.info(f"Collection '{collection_name}' created successfully")
                except UnexpectedResponse as create_err:
                    # Handle race condition: another worker may have created it
                    if create_err.status_code == 409 or "already exists" in str(create_err).lower():
                        logger.debug(f"Collection '{collection_name}' was created by another worker")
                    else:
                        raise
                self._collections_checked.add(collection_name)
            else:
                raise
    
    def check_exists(
        self,
        source_path: str,
        content_hash: str,
        collection_name: str,
    ) -> bool:
        """Check if document already exists with same content hash.
        
        Used for skip detection to avoid re-indexing unchanged documents.
        
        Args:
            source_path: GCS source path of the document.
            content_hash: SHA256 hash of the document content.
            collection_name: Qdrant collection to check.
        
        Returns:
            True if document exists with same content hash.
        """
        point_id = get_point_id(source_path)
        
        try:
            points = self.client.retrieve(
                collection_name=collection_name,
                ids=[point_id],
                with_payload=["content_hash"],
            )
            
            if not points:
                return False
            
            existing_hash = points[0].payload.get("content_hash", "")
            if existing_hash == content_hash:
                logger.debug(f"Document '{source_path}' unchanged, skipping")
                return True
            
            logger.debug(f"Document '{source_path}' content changed, will re-index")
            return False
            
        except UnexpectedResponse:
            # Collection or point doesn't exist
            return False
    
    def exists_by_path(
        self,
        source_path: str,
        collection_name: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Check if document exists by source path (without content hash comparison).
        
        Used for skip detection in the GCS-first pipeline architecture where
        we want to skip files that have already been indexed, regardless of
        whether the content has changed.
        
        Args:
            source_path: GCS source path of the document.
            collection_name: Optional specific collection to check. If None,
                           checks all collections.
        
        Returns:
            Tuple of (exists: bool, collection_name: str or None).
            If exists is True, collection_name contains the collection where found.
        """
        point_id = get_point_id(source_path)
        
        # If specific collection provided, check only that one
        if collection_name:
            try:
                points = self.client.retrieve(
                    collection_name=collection_name,
                    ids=[point_id],
                    with_payload=False,
                )
                if points:
                    logger.debug(f"Document '{source_path}' exists in '{collection_name}'")
                    return True, collection_name
            except UnexpectedResponse:
                pass
            return False, None
        
        # Check all known collections
        collections_to_check = self._get_all_collections()
        
        for coll in collections_to_check:
            try:
                points = self.client.retrieve(
                    collection_name=coll,
                    ids=[point_id],
                    with_payload=False,
                )
                if points:
                    logger.debug(f"Document '{source_path}' exists in '{coll}'")
                    return True, coll
            except UnexpectedResponse:
                # Collection doesn't exist or point not found
                continue
        
        logger.debug(f"Document '{source_path}' not found in any collection")
        return False, None
    
    def _get_all_collections(self) -> list[str]:
        """Get list of all collection names to check for existence.
        
        Returns collections based on routing configuration plus defaults.
        
        Returns:
            List of collection names.
        """
        # Start with unique collections from routing rules
        collections = set()
        for rule in self.router.rules:
            collections.add(rule.collection)
        
        # Add default collection
        collections.add(self.router.default_collection)
        
        # Add source variants if base collections exist
        base_collections = list(collections)
        for base in base_collections:
            if not base.endswith("-source"):
                collections.add(f"{base}-source")
        
        return list(collections)
    
    def index_document(
        self,
        source_path: str,
        content: str,
        cache_path: str,
        file_size: int,
        original_format: str,
        dry_run: bool = False,
        force: bool = False,
        is_source_code: bool = False,
    ) -> IndexingResult:
        """Index a document into Qdrant, chunking if needed.
        
        Large documents are automatically split into chunks that each
        fit within Qdrant's payload size limit. Each chunk is stored
        as a separate point. Chunk 0 keeps the original point ID so
        that exists_by_path skip detection still works.
        
        Args:
            source_path: GCS source path of the document.
            content: Markdown content of the document.
            cache_path: GCS path to cached markdown file.
            file_size: Original file size in bytes.
            original_format: Original file extension (.pdf, .html, etc.).
            dry_run: If True, check if indexing would happen but don't write.
            force: If True, skip the duplicate check and always re-index.
            is_source_code: If True, route to source collection variant.
        
        Returns:
            IndexingResult with status and any error message.
        """
        # Determine target collection (with source code awareness)
        collection_name = self.router.route_with_category(source_path, is_source_code)
        
        # Ensure collection exists (unless dry_run)
        if not dry_run:
            try:
                self.ensure_collection(collection_name)
            except Exception as e:
                return IndexingResult(
                    source_path=source_path,
                    collection=collection_name,
                    status="failed",
                    error=f"Failed to ensure collection: {e}",
                )
        
        # Calculate content hash over full content for skip detection
        content_hash = get_content_hash(content)
        
        # Check if already indexed with same content (skip if force=True)
        if not force:
            try:
                if self.check_exists(source_path, content_hash, collection_name):
                    return IndexingResult(
                        source_path=source_path,
                        collection=collection_name,
                        status="skipped",
                    )
            except Exception as e:
                logger.warning(f"Skip check failed for '{source_path}': {e}")
                # Continue with indexing if check fails
        
        # Split into chunks if content is large
        chunks = chunk_text(content)
        if len(chunks) > 1:
            logger.info(
                f"Chunking '{source_path}' into {len(chunks)} chunks "
                f"({len(content)} chars total)"
            )
        
        # Dry run mode
        if dry_run:
            logger.debug(f"Dry run: would index '{source_path}' to '{collection_name}' ({len(chunks)} chunks)")
            return IndexingResult(
                source_path=source_path,
                collection=collection_name,
                status="indexed",
            )
        
        # Index each chunk as a separate point
        indexed_count = 0
        for chunk_idx, chunk_text_content in enumerate(chunks):
            try:
                self._upsert_chunk(
                    source_path=source_path,
                    chunk_text=chunk_text_content,
                    chunk_index=chunk_idx,
                    total_chunks=len(chunks),
                    content_hash=content_hash,
                    collection_name=collection_name,
                    cache_path=cache_path,
                    file_size=file_size,
                    original_format=original_format,
                )
                indexed_count += 1
            except Exception as e:
                error_msg = f"Upsert failed for chunk {chunk_idx}/{len(chunks)}: {e}"
                logger.error(f"[{source_path}] {error_msg}")
                return IndexingResult(
                    source_path=source_path,
                    collection=collection_name,
                    status="failed",
                    error=error_msg,
                )
        
        if len(chunks) > 1:
            logger.debug(f"Indexed '{source_path}' to '{collection_name}' ({indexed_count} chunks)")
        else:
            logger.debug(f"Indexed '{source_path}' to collection '{collection_name}'")
        
        return IndexingResult(
            source_path=source_path,
            collection=collection_name,
            status="indexed",
        )
    
    def _upsert_chunk(
        self,
        source_path: str,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        content_hash: str,
        collection_name: str,
        cache_path: str,
        file_size: int,
        original_format: str,
    ) -> None:
        """Upsert a single chunk to Qdrant.
        
        Chunk 0 uses the base point ID (from source_path) so that
        exists_by_path skip detection still works. Subsequent chunks
        use deterministic IDs derived from '{source_path}#chunk{N}'.
        
        Args:
            source_path: GCS source path of the document.
            chunk_text: Text content of this chunk.
            chunk_index: 0-based chunk index.
            total_chunks: Total number of chunks for this document.
            content_hash: Hash of the full document content.
            collection_name: Target Qdrant collection.
            cache_path: GCS cache path.
            file_size: Original file size.
            original_format: Original file extension.
        """
        # Chunk 0 uses base ID; subsequent chunks use derived IDs
        if chunk_index == 0:
            point_id = get_point_id(source_path)
        else:
            point_id = get_point_id(f"{source_path}#chunk{chunk_index}")
        
        # Generate embedding for this chunk's text
        vector = self.embedder.embed(chunk_text)
        
        # Build payload compatible with mcp-server-qdrant
        payload = {
            "document": chunk_text,
            "metadata": {
                "source_path": source_path,
                "content_hash": content_hash,
                "indexed_at": datetime.now(timezone.utc).isoformat(),
                "file_size": file_size,
                "original_format": original_format,
                "cache_path": cache_path,
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
            },
            "content_hash": content_hash,
        }
        
        self.client.upsert(
            collection_name=collection_name,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={self.VECTOR_NAME: vector},
                    payload=payload,
                )
            ],
        )
    
    def close(self) -> None:
        """Close connection to Qdrant."""
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.debug("Qdrant connection closed")
