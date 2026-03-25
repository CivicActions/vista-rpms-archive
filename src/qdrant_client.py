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
from .types import ChunkResult, IndexingResult, QdrantConfig

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
                timeout=600,  # 10 minute timeout for large document uploads
                prefer_grpc=False,  # Use HTTP for better firewall compatibility
            )
            # Test connection with lightweight healthz check
            # (get_collections() can timeout with large collections behind nginx)
            import httpx
            resp = httpx.get(
                f"{self.config.url}/healthz",
                headers={"api-key": self.config.api_key} if self.config.api_key else {},
                timeout=15,
            )
            resp.raise_for_status()
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
                # Ensure payload index on metadata.source_path for fast
                # filter-based deletes (avoids O(n) full scans).
                try:
                    self.client.create_payload_index(
                        collection_name=collection_name,
                        field_name="metadata.source_path",
                        field_schema=models.PayloadSchemaType.KEYWORD,
                    )
                except Exception as idx_err:
                    logger.warning(f"Payload index creation failed for '{collection_name}': {idx_err}")
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
    
    # -----------------------------------------------------------------
    # New chunk-based indexing API
    # -----------------------------------------------------------------

    def delete_by_source_path(self, source_path: str, collection_name: str) -> int:
        """Delete all points matching a source_path from a collection.

        Uses a scroll+delete approach: filters by metadata.source_path
        and deletes all matching points.

        Args:
            source_path: GCS source path to match.
            collection_name: Target Qdrant collection.

        Returns:
            Number of points deleted.
        """
        try:
            result = self.client.delete(
                collection_name=collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="metadata.source_path",
                                match=models.MatchValue(value=source_path),
                            )
                        ]
                    )
                ),
            )
            logger.debug(f"Deleted points for '{source_path}' from '{collection_name}'")
            # Qdrant delete returns operation info but not count
            # We return 0 since we can't easily get the count without a prior scroll
            return 0
        except UnexpectedResponse as e:
            if "not found" in str(e).lower() or e.status_code == 404:
                return 0
            raise

    def index_chunks(
        self,
        chunks: list[ChunkResult],
        collection_name: str,
        force: bool = False,
        dry_run: bool = False,
        file_size: int = 0,
        original_format: str = "",
        cache_path: str = "",
    ) -> IndexingResult:
        """Index pre-chunked data into Qdrant.

        Replaces the old index_document() flow. Accepts ChunkResult objects
        already produced by the chunking module.

        Args:
            chunks: Pre-chunked data from chunker module.
            collection_name: Target Qdrant collection.
            force: If True, delete existing points before inserting.
            dry_run: If True, skip actual Qdrant operations.
            file_size: Original file size in bytes.
            original_format: File extension (e.g., '.pdf').
            cache_path: GCS cache path.

        Returns:
            IndexingResult with status and any error message.
        """
        if not chunks:
            return IndexingResult(
                source_path="",
                collection=collection_name,
                status="failed",
                error="No chunks to index",
            )

        source_path = chunks[0].source_path
        content_hash = chunks[0].content_hash

        # Ensure collection exists
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

        # Check if already indexed with same content (skip unless force)
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

        # Dry run
        if dry_run:
            logger.debug(
                f"Dry run: would index '{source_path}' to '{collection_name}' "
                f"({len(chunks)} chunks)"
            )
            return IndexingResult(
                source_path=source_path,
                collection=collection_name,
                status="indexed",
            )

        # Force mode: delete existing points for this document first
        if force:
            try:
                self.delete_by_source_path(source_path, collection_name)
            except Exception as e:
                logger.warning(f"Force-delete failed for '{source_path}': {e}")

        # Batch embed + batch upsert to minimise ONNX/HTTP overhead and
        # native-memory fragmentation (critical for archives with thousands
        # of small files like GOLD.zip).
        try:
            texts = [chunk.embedding_text for chunk in chunks]
            import psutil, os
            rss_pre = psutil.Process(os.getpid()).memory_info().rss // (1024 * 1024)
            vectors = self.embedder.embed_batch(texts)
            rss_post = psutil.Process(os.getpid()).memory_info().rss // (1024 * 1024)
            if rss_post - rss_pre > 500:
                logger.warning(
                    f"[{source_path}] embed_batch RSS: {rss_pre}→{rss_post} MB "
                    f"(+{rss_post-rss_pre} MB, {len(texts)} texts)"
                )
        except Exception as e:
            error_msg = f"Batch embed failed: {e}"
            logger.error(f"[{source_path}] {error_msg}")
            return IndexingResult(
                source_path=source_path,
                collection=collection_name,
                status="failed",
                error=error_msg,
            )

        points = []
        for chunk, vector in zip(chunks, vectors):
            try:
                if chunk.chunk_index == 0:
                    point_id = get_point_id(source_path)
                else:
                    point_id = get_point_id(f"{source_path}#chunk{chunk.chunk_index}")

                metadata = {
                    "source_path": chunk.source_path,
                    "source_url": chunk.source_url,
                    "content_hash": chunk.content_hash,
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                    "file_size": file_size,
                    "original_format": original_format,
                    "cache_path": cache_path,
                    "chunk_index": chunk.chunk_index,
                    "total_chunks": chunk.total_chunks,
                    "collection": collection_name,
                }
                metadata.update(chunk.metadata)

                payload = {
                    "document": chunk.text,
                    "metadata": metadata,
                    "content_hash": chunk.content_hash,
                }

                points.append(
                    models.PointStruct(
                        id=point_id,
                        vector={self.VECTOR_NAME: vector},
                        payload=payload,
                    )
                )
            except Exception as e:
                error_msg = (
                    f"Payload build failed for chunk {chunk.chunk_index}/{chunk.total_chunks}: {e}"
                )
                logger.error(f"[{source_path}] {error_msg}")
                return IndexingResult(
                    source_path=source_path,
                    collection=collection_name,
                    status="failed",
                    error=error_msg,
                )

        try:
            rss_pre_upsert = psutil.Process(os.getpid()).memory_info().rss // (1024 * 1024)
            self.client.upsert(
                collection_name=collection_name,
                points=points,
            )
            rss_post_upsert = psutil.Process(os.getpid()).memory_info().rss // (1024 * 1024)
            if rss_post_upsert - rss_pre_upsert > 500:
                logger.warning(
                    f"[{source_path}] upsert RSS: {rss_pre_upsert}→{rss_post_upsert} MB "
                    f"(+{rss_post_upsert-rss_pre_upsert} MB, {len(points)} points)"
                )
        except Exception as e:
            error_msg = f"Batch upsert failed ({len(points)} points): {e}"
            logger.error(f"[{source_path}] {error_msg}")
            return IndexingResult(
                source_path=source_path,
                collection=collection_name,
                status="failed",
                error=error_msg,
            )

        indexed_count = len(points)

        logger.debug(
            f"Indexed '{source_path}' to '{collection_name}' ({indexed_count} chunks)"
        )
        return IndexingResult(
            source_path=source_path,
            collection=collection_name,
            status="indexed",
        )
    
    def close(self) -> None:
        """Close connection to Qdrant."""
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.debug("Qdrant connection closed")
