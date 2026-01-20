"""GCS client wrapper with connection pooling for document extraction pipeline."""

import logging
import tempfile
from pathlib import Path
from typing import Optional

from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError, NotFound

logger = logging.getLogger(__name__)


class GCSClient:
    """GCS client wrapper with connection pooling and retry logic."""
    
    def __init__(
        self,
        source_bucket: str,
        cache_bucket: str,
        source_prefix: str = "",
        cache_prefix: str = "cache/",
    ):
        """Initialize GCS client.
        
        Args:
            source_bucket: Bucket containing source files
            cache_bucket: Bucket for cached markdown output
            source_prefix: Prefix path within source bucket
            cache_prefix: Prefix path within cache bucket
        """
        # Create ONE client instance - handles connection pooling internally
        self._client = storage.Client()
        
        self._source_bucket_name = source_bucket
        self._cache_bucket_name = cache_bucket
        self._source_prefix = source_prefix.rstrip("/") + "/" if source_prefix else ""
        self._cache_prefix = cache_prefix.rstrip("/") + "/" if cache_prefix else ""
        
        # Get bucket references
        self._source_bucket = self._client.bucket(source_bucket)
        self._cache_bucket = self._client.bucket(cache_bucket)
    
    def cache_path_for_source(self, source_path: str) -> str:
        """Compute the cache path for a source file.
        
        Args:
            source_path: Relative path of source file (from index.json)
        
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
            logger.debug(f"Downloaded {source_path} to {temp_path}")
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
        logger.debug(f"Uploaded markdown to {cache_path}")
    
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
