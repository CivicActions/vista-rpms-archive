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
                timeout=120,  # Increased timeout for slow connections
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
    
    def index_document(
        self,
        source_path: str,
        content: str,
        cache_path: str,
        file_size: int,
        original_format: str,
        dry_run: bool = False,
        force: bool = False,
    ) -> IndexingResult:
        """Index a document into Qdrant.
        
        Handles routing, skip detection, embedding, and upsert.
        
        Args:
            source_path: GCS source path of the document.
            content: Markdown content of the document.
            cache_path: GCS path to cached markdown file.
            file_size: Original file size in bytes.
            original_format: Original file extension (.pdf, .html, etc.).
            dry_run: If True, check if indexing would happen but don't write.
            force: If True, skip the duplicate check and always re-index.
        
        Returns:
            IndexingResult with status and any error message.
        """
        # Determine target collection
        collection_name = self.router.route(source_path)
        
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
        
        # Calculate content hash
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
        
        # Generate embedding
        try:
            vector = self.embedder.embed(content)
        except Exception as e:
            return IndexingResult(
                source_path=source_path,
                collection=collection_name,
                status="failed",
                error=f"Embedding failed: {e}",
            )
        
        # Prepare payload - compatible with mcp-server-qdrant
        # mcp-server-qdrant expects: {"document": text, "metadata": {...}}
        payload = {
            "document": content,  # Required by mcp-server-qdrant
            "metadata": {  # Nested metadata dict for mcp-server-qdrant compatibility
                "source_path": source_path,
                "content_hash": content_hash,
                "indexed_at": datetime.now(timezone.utc).isoformat(),
                "file_size": file_size,
                "original_format": original_format,
                "cache_path": cache_path,
            },
            # Also keep top-level fields for our own skip detection
            "content_hash": content_hash,
        }
        
        # Dry run mode - return what would be indexed without writing
        if dry_run:
            logger.debug(f"Dry run: would index '{source_path}' to '{collection_name}'")
            return IndexingResult(
                source_path=source_path,
                collection=collection_name,
                status="indexed",  # Would be indexed
            )
        
        # Upsert to Qdrant
        try:
            point_id = get_point_id(source_path)
            self.client.upsert(
                collection_name=collection_name,
                points=[
                    models.PointStruct(
                        id=point_id,
                        vector={self.VECTOR_NAME: vector},  # Named vector
                        payload=payload,
                    )
                ],
            )
            logger.debug(f"Indexed '{source_path}' to collection '{collection_name}'")
            return IndexingResult(
                source_path=source_path,
                collection=collection_name,
                status="indexed",
            )
        except Exception as e:
            return IndexingResult(
                source_path=source_path,
                collection=collection_name,
                status="failed",
                error=f"Upsert failed: {e}",
            )
    
    def close(self) -> None:
        """Close connection to Qdrant."""
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.debug("Qdrant connection closed")
