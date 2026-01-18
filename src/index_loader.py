"""Index loader for parsing and filtering index.json from GCS."""

import logging
from typing import Iterator, Optional

from .gcs_client import GCSClient
from .types import IndexEntry, OFFICE_MIME_TYPES, ARCHIVE_MIME_TYPES, OFFICE_EXTENSIONS

logger = logging.getLogger(__name__)


class IndexLoader:
    """Loads and filters file entries from index.json."""
    
    def __init__(self, gcs_client: GCSClient):
        """Initialize index loader.
        
        Args:
            gcs_client: GCS client for downloading index.json
        """
        self._gcs_client = gcs_client
        self._index_data: Optional[dict] = None
        self._entries: Optional[list[IndexEntry]] = None
    
    def load(self) -> None:
        """Load index.json from GCS."""
        logger.info("Loading index.json from GCS...")
        self._index_data = self._gcs_client.download_index_json()
        
        # Parse file entries
        files = self._index_data.get("files", [])
        self._entries = [IndexEntry.from_dict(f) for f in files]
        
        summary = self._index_data.get("summary", {})
        logger.info(
            f"Loaded index: {summary.get('total_files', len(self._entries))} files, "
            f"{summary.get('total_size_human', 'unknown size')}"
        )
    
    @property
    def total_files(self) -> int:
        """Total number of files in index."""
        return len(self._entries) if self._entries else 0
    
    @property
    def entries(self) -> list[IndexEntry]:
        """All index entries."""
        if self._entries is None:
            self.load()
        return self._entries or []
    
    def filter_office_documents(self) -> list[IndexEntry]:
        """Filter entries to only office documents.
        
        Returns:
            List of IndexEntry objects that are office documents
        """
        office_docs = [e for e in self.entries if is_office_document(e.mime_type)]
        logger.info(f"Found {len(office_docs)} office documents out of {self.total_files} total files")
        return office_docs
    
    def filter_archives_with_office_docs(self) -> list[IndexEntry]:
        """Filter entries to archives containing office documents.
        
        Returns:
            List of IndexEntry objects that are archives with office docs inside
        """
        archives = [
            e for e in self.entries 
            if is_archive(e.mime_type) and e.has_office_documents_in_archive()
        ]
        logger.info(f"Found {len(archives)} archives containing office documents")
        return archives
    
    def iter_office_documents(self) -> Iterator[IndexEntry]:
        """Iterate over office document entries.
        
        Yields:
            IndexEntry objects for office documents
        """
        for entry in self.entries:
            if is_office_document(entry.mime_type):
                yield entry
    
    def get_summary(self) -> dict:
        """Get index summary information.
        
        Returns:
            Summary dictionary from index.json
        """
        if self._index_data is None:
            self.load()
        return self._index_data.get("summary", {}) if self._index_data else {}


def is_office_document(mime_type: str) -> bool:
    """Check if a MIME type represents an office document.
    
    Args:
        mime_type: MIME type string
    
    Returns:
        True if the MIME type is a supported office document format
    """
    return mime_type in OFFICE_MIME_TYPES


def is_archive(mime_type: str) -> bool:
    """Check if a MIME type represents an archive.
    
    Note: Returns False for ZIP-based office formats (DOCX, XLSX, PPTX)
    even though they are technically ZIP files.
    
    Args:
        mime_type: MIME type string
    
    Returns:
        True if the MIME type is a supported archive format
    """
    # Explicitly exclude office MIME types that are technically ZIP-based
    if mime_type in OFFICE_MIME_TYPES:
        return False
    return mime_type in ARCHIVE_MIME_TYPES


def has_office_extension(filename: str) -> bool:
    """Check if a filename has an office document extension.
    
    Used for filtering files inside archives where MIME type detection
    is not available.
    
    Args:
        filename: File name or path
    
    Returns:
        True if the filename has an office document extension
    """
    lower_name = filename.lower()
    return any(lower_name.endswith(ext) for ext in OFFICE_EXTENSIONS)
