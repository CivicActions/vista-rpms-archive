"""GCS client wrapper with thread-safe connection handling for document extraction pipeline.

The google-cloud-storage library's ``storage.Client`` uses ``httplib2.Http``
internally for HTTP transport, which is NOT thread-safe (see
https://googleapis.github.io/google-api-python-client/docs/thread_safety.html).
Sharing a single client across 16 worker threads causes memory corruption,
leaked connections, and unbounded buffer growth (observed as 60+ GB RSS
spikes with no corresponding Python objects).

This module uses ``threading.local()`` to give each worker thread its own
``storage.Client`` instance, ensuring no HTTP transport is shared.
"""

import logging
import tempfile
import threading
from pathlib import Path
from typing import Iterator, Optional

from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError, NotFound

logger = logging.getLogger(__name__)


class GCSClient:
    """Thread-safe GCS client wrapper.

    Each thread gets its own ``storage.Client`` via ``threading.local()``,
    preventing the httplib2 thread-safety issues that cause memory leaks.
    """

    def __init__(
        self,
        source_bucket: str,
        cache_bucket: str,
        source_prefix: str = "",
        cache_prefix: str = "cache/",
    ):
        self._source_bucket_name = source_bucket
        self._cache_bucket_name = cache_bucket
        self._source_prefix = source_prefix.rstrip("/") + "/" if source_prefix else ""
        self._cache_prefix = cache_prefix.rstrip("/") + "/" if cache_prefix else ""
        self._local = threading.local()

    def _get_client(self) -> storage.Client:
        """Return a thread-local storage.Client instance."""
        client = getattr(self._local, "client", None)
        if client is None:
            client = storage.Client()
            self._local.client = client
        return client

    @property
    def _source_bucket(self) -> storage.Bucket:
        return self._get_client().bucket(self._source_bucket_name)

    @property
    def _cache_bucket(self) -> storage.Bucket:
        return self._get_client().bucket(self._cache_bucket_name)

    def _rebind_blob(self, blob: storage.Blob) -> storage.Blob:
        """Re-bind a blob to this thread's storage.Client.

        Blob objects returned by list_blobs() carry a reference to the
        client that created them (the main thread's client).  Calling
        methods like download_as_bytes() on those blobs from a worker
        thread would use the main thread's HTTP session, which is not
        thread-safe.  This method creates a new Blob bound to the
        current thread's client.
        """
        return self._source_bucket.blob(blob.name)
    
    def cache_path_for_source(self, source_path: str) -> str:
        """Compute the cache path for a source file.
        
        Args:
            source_path: Relative path of source file (without bucket)
        
        Returns:
            Full GCS path for cached markdown file
        """
        # Remove source prefix if present
        if self._source_prefix and source_path.startswith(self._source_prefix):
            relative_path = source_path[len(self._source_prefix):]
        else:
            relative_path = source_path
        
        # Add .md extension and cache prefix
        return f"{self._cache_prefix}{relative_path}.md"
    
    def list_blobs(
        self,
        prefix: Optional[str] = None,
    ) -> Iterator[storage.Blob]:
        """Iterate over all blobs in source bucket.
        
        Supports the new GCS-first architecture where we iterate over all
        files directly rather than relying on an index.json manifest.
        
        Args:
            prefix: Optional prefix to filter blobs. If None, uses the
                    source_prefix configured at init time.
        
        Yields:
            storage.Blob objects for each file in the bucket.
        """
        effective_prefix = prefix if prefix is not None else self._source_prefix
        # Remove trailing slash for listing if present, as GCS handles it
        if effective_prefix and effective_prefix.endswith('/'):
            effective_prefix = effective_prefix[:-1]
        
        logger.info(f"Listing blobs in gs://{self._source_bucket_name}/{effective_prefix or ''}")
        yield from self._source_bucket.list_blobs(prefix=effective_prefix or None)
    
    def download_blob_content(self, blob: storage.Blob) -> bytes:
        """Download blob content as bytes without creating a temp file.
        
        Re-binds the blob to the current thread's client for safety.
        
        Args:
            blob: GCS Blob object to download.
        
        Returns:
            Raw file content as bytes.
        
        Raises:
            GoogleCloudError: If download fails.
        """
        try:
            local_blob = self._rebind_blob(blob)
            content = local_blob.download_as_bytes()
            size_kb = len(content) / 1024
            if size_kb > 1024:
                logger.info(f"Downloaded {blob.name} ({size_kb/1024:.1f} MB)")
            elif size_kb > 100:
                logger.info(f"Downloaded {blob.name} ({size_kb:.0f} KB)")
            else:
                logger.debug(f"Downloaded {blob.name} ({size_kb:.1f} KB)")
            return content
        except GoogleCloudError as e:
            logger.error(f"Failed to download blob {blob.name}: {e}")
            raise
    
    def download_blob_to_temp(self, blob: storage.Blob) -> Path:
        """Download a blob to a temporary file.
        
        Similar to download_to_temp but takes a Blob object directly.
        
        Args:
            blob: GCS Blob object to download.
        
        Returns:
            Path to temporary file (caller must delete after use).
        
        Raises:
            GoogleCloudError: If download fails.
        """
        # Determine suffix from blob name
        suffix = None
        if "." in blob.name:
            suffix = "." + blob.name.rsplit(".", 1)[-1]
        
        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
            prefix="gcs_blob_"
        )
        temp_path = Path(temp_file.name)
        temp_file.close()
        
        try:
            local_blob = self._rebind_blob(blob)
            local_blob.download_to_filename(str(temp_path))
            size_kb = temp_path.stat().st_size / 1024
            if size_kb > 1024:
                logger.info(f"Downloaded {blob.name} to temp ({size_kb/1024:.1f} MB)")
            elif size_kb > 100:
                logger.info(f"Downloaded {blob.name} to temp ({size_kb:.0f} KB)")
            else:
                logger.debug(f"Downloaded {blob.name} to temp ({size_kb:.1f} KB)")
            return temp_path
        except Exception:
            # Clean up temp file on error
            temp_path.unlink(missing_ok=True)
            raise
    
    def cache_exists(self, source_path: str) -> bool:
        """Check if cached markdown exists for a source file.
        
        Args:
            source_path: Relative path of source file
        
        Returns:
            True if cached file exists
        """
        cache_path = self.cache_path_for_source(source_path)
        blob = self._cache_bucket.blob(cache_path)
        try:
            return blob.exists(timeout=60)
        except GoogleCloudError as e:
            logger.warning(f"Error checking cache existence for {source_path}: {e}")
            return False
    
    def cache_exists_by_path(self, cache_path: str) -> bool:
        """Check if a specific cache path exists.
        
        Unlike cache_exists(), this takes an already-computed cache path
        (useful for archive contents where path includes archive prefix).
        
        Args:
            cache_path: Full cache path (with prefix, without bucket)
        
        Returns:
            True if cached file exists
        """
        blob = self._cache_bucket.blob(cache_path)
        try:
            return blob.exists(timeout=60)
        except GoogleCloudError as e:
            logger.warning(f"Error checking cache existence for {cache_path}: {e}")
            return False
    
    def download_to_temp(self, source_path: str, suffix: Optional[str] = None) -> Path:
        """Download a source file to a temporary file.
        
        Args:
            source_path: Relative path of source file
            suffix: Optional file suffix (e.g., ".pdf")
        
        Returns:
            Path to temporary file (caller must delete after use)
        
        Raises:
            NotFound: If source file doesn't exist
            GoogleCloudError: For other GCS errors
        """
        # Build full blob path
        blob_path = f"{self._source_prefix}{source_path}" if self._source_prefix else source_path
        blob = self._source_bucket.blob(blob_path)
        
        # Create temp file with appropriate suffix
        if suffix is None and "." in source_path:
            suffix = "." + source_path.rsplit(".", 1)[-1]
        
        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
            prefix="extract_"
        )
        temp_path = Path(temp_file.name)
        temp_file.close()
        
        try:
            blob.download_to_filename(str(temp_path))
            size_kb = temp_path.stat().st_size / 1024
            if size_kb > 1024:
                logger.info(f"Downloaded {source_path} to temp ({size_kb/1024:.1f} MB)")
            elif size_kb > 100:
                logger.info(f"Downloaded {source_path} to temp ({size_kb:.0f} KB)")
            else:
                logger.debug(f"Downloaded {source_path} to temp ({size_kb:.1f} KB)")
            return temp_path
        except Exception:
            # Clean up temp file on error
            temp_path.unlink(missing_ok=True)
            raise
    
    def upload_markdown(self, cache_path: str, content: str) -> None:
        """Upload markdown content to cache.
        
        Args:
            cache_path: Destination path in cache bucket
            content: Markdown content to upload
        """
        blob = self._cache_bucket.blob(cache_path)
        blob.upload_from_string(
            content,
            content_type="text/markdown",
        )
        size_kb = len(content.encode('utf-8')) / 1024
        if size_kb > 100:
            logger.info(f"Uploaded markdown to {cache_path} ({size_kb:.0f} KB)")
        else:
            logger.debug(f"Uploaded markdown to {cache_path} ({size_kb:.1f} KB)")
    
    def read_cached_markdown(self, cache_path: str) -> str:
        """Read cached markdown content from GCS.
        
        Args:
            cache_path: Cache path (without bucket name)
        
        Returns:
            Markdown content as string
        
        Raises:
            NotFound: If cached file doesn't exist
            GoogleCloudError: For other GCS errors
        """
        blob = self._cache_bucket.blob(cache_path)
        content = blob.download_as_text()
        logger.debug(f"Read cached markdown from {cache_path}")
        return content
    
    def download_index_json(self) -> dict:
        """Download and parse index.json from source bucket root.
        
        Returns:
            Parsed index.json content
        
        Raises:
            NotFound: If index.json doesn't exist
            GoogleCloudError: For other GCS errors
        """
        blob_path = f"{self._source_prefix}index.json" if self._source_prefix else "index.json"
        blob = self._source_bucket.blob(blob_path)
        
        try:
            content = blob.download_as_text()
            import json
            return json.loads(content)
        except NotFound:
            logger.error(f"index.json not found at gs://{self._source_bucket_name}/{blob_path}")
            raise
    
    def download_archive_to_temp(self, source_path: str) -> Path:
        """Download an archive file to a temporary directory for extraction.
        
        Args:
            source_path: Relative path of archive file
        
        Returns:
            Path to temporary file
        """
        # Determine suffix from path
        suffix = None
        lower_path = source_path.lower()
        if lower_path.endswith(".zip"):
            suffix = ".zip"
        elif lower_path.endswith(".tar.gz") or lower_path.endswith(".tgz"):
            suffix = ".tar.gz"
        elif lower_path.endswith(".tar"):
            suffix = ".tar"
        
        return self.download_to_temp(source_path, suffix=suffix)

    def docling_json_cache_path(self, source_path: str) -> str:
        """Compute the cache path for a DoclingDocument JSON file.
        
        Args:
            source_path: Relative path of source file (without bucket)
        
        Returns:
            Full GCS path for cached DoclingDocument JSON file.
        """
        # Remove source prefix if present
        if self._source_prefix and source_path.startswith(self._source_prefix):
            relative_path = source_path[len(self._source_prefix):]
        else:
            relative_path = source_path
        
        return f"{self._cache_prefix}{relative_path}.docling.json"

    def upload_docling_json(self, source_path: str, json_str: str) -> str:
        """Upload serialized DoclingDocument JSON to GCS cache.
        
        Args:
            source_path: GCS source path (used to derive cache path).
            json_str: Serialized DoclingDocument JSON string.
        
        Returns:
            GCS cache path of the uploaded JSON.
        """
        cache_path = self.docling_json_cache_path(source_path)
        blob = self._cache_bucket.blob(cache_path)
        blob.upload_from_string(
            json_str,
            content_type="application/json",
        )
        size_kb = len(json_str.encode("utf-8")) / 1024
        if size_kb > 1024:
            logger.info(f"Uploaded DoclingDocument JSON to {cache_path} ({size_kb/1024:.1f} MB)")
        else:
            logger.debug(f"Uploaded DoclingDocument JSON to {cache_path} ({size_kb:.1f} KB)")
        return cache_path

    def download_docling_json(self, source_path: str) -> str | None:
        """Download cached DoclingDocument JSON from GCS.
        
        Args:
            source_path: GCS source path (used to derive cache path).
        
        Returns:
            JSON string if cache exists, None if not cached.
        """
        cache_path = self.docling_json_cache_path(source_path)
        blob = self._cache_bucket.blob(cache_path)
        try:
            if not blob.exists(timeout=60):
                return None
            content = blob.download_as_text()
            logger.debug(f"Read cached DoclingDocument JSON from {cache_path}")
            return content
        except NotFound:
            return None
        except GoogleCloudError as e:
            logger.warning(f"Error reading DoclingDocument JSON cache for {source_path}: {e}")
            return None
