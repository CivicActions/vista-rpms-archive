"""Data classes and type definitions for document extraction pipeline."""

from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# MIME Type Constants
# =============================================================================

# Office document MIME types - these are extracted using docling
# Note: DOCX, XLSX, PPTX are ZIP-based but are NOT treated as archives
OFFICE_MIME_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "application/msword",  # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.ms-excel",  # .xls
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-powerpoint",  # .ppt
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "text/rtf",  # .rtf
    "text/html",  # .html, .htm
})

# Office document file extensions (for filtering inside archives)
OFFICE_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".html", ".htm"
})

# Archive MIME types - these contain files to extract
# EXCLUDES office MIME types even though DOCX/XLSX/PPTX are technically ZIP files
ARCHIVE_MIME_TYPES: frozenset[str] = frozenset({
    "application/zip",
    "application/x-tar",
    "application/gzip",
    "application/x-bzip2",
    "application/x-xz",
    "application/x-7z-compressed",
})


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ArchiveEntry:
    """Represents a file within an archive."""
    
    name: str
    size: int
    compressed_size: Optional[int] = None  # ZIP only
    is_dir: bool = False


@dataclass
class IndexEntry:
    """Represents a file record from index.json."""
    
    path: str
    absolute_path: str
    size_bytes: int
    mime_type: str
    extension: Optional[str] = None
    modified: Optional[str] = None
    archive_contents: Optional[list[ArchiveEntry]] = None
    archive_file_count: Optional[int] = None
    
    @classmethod
    def from_dict(cls, data: dict) -> "IndexEntry":
        """Create IndexEntry from dictionary (parsed JSON)."""
        archive_contents = None
        if "archive_contents" in data and data["archive_contents"]:
            archive_contents = [
                ArchiveEntry(
                    name=entry.get("name", ""),
                    size=entry.get("size", 0),
                    compressed_size=entry.get("compressed_size"),
                    is_dir=entry.get("is_dir", False),
                )
                for entry in data["archive_contents"]
                if "error" not in entry
            ]
        
        return cls(
            path=data["path"],
            absolute_path=data.get("absolute_path", data["path"]),
            size_bytes=data.get("size_bytes", 0),
            mime_type=data.get("mime_type", "application/octet-stream"),
            extension=data.get("extension"),
            modified=data.get("modified"),
            archive_contents=archive_contents,
            archive_file_count=data.get("archive_file_count"),
        )
    
    def is_office_document(self) -> bool:
        """Check if this entry is an office document based on MIME type."""
        return self.mime_type in OFFICE_MIME_TYPES
    
    def is_archive(self) -> bool:
        """Check if this entry is an archive (excludes office ZIP formats)."""
        return self.mime_type in ARCHIVE_MIME_TYPES
    
    def has_office_documents_in_archive(self) -> bool:
        """Check if archive contains any office documents (by extension)."""
        if not self.archive_contents:
            return False
        return any(
            not entry.is_dir and any(
                entry.name.lower().endswith(ext) for ext in OFFICE_EXTENSIONS
            )
            for entry in self.archive_contents
        )


@dataclass
class ExtractionResult:
    """Outcome of processing a single file."""
    
    source_path: str
    cache_path: Optional[str] = None
    success: bool = False
    skipped: bool = False
    error: Optional[str] = None
    processing_time_ms: int = 0


@dataclass
class RoutingRule:
    """Configuration for routing documents to Qdrant collections."""
    
    pattern: str  # Substring to match in source_path
    collection: str  # Target collection name


@dataclass
class IndexingResult:
    """Outcome of indexing a single document in Qdrant."""
    
    source_path: str
    collection: str
    status: str  # "indexed", "skipped", "failed"
    error: Optional[str] = None


@dataclass
class QdrantConfig:
    """Qdrant-specific configuration."""
    
    url: str = "http://localhost:6333"
    api_key: str = ""
    default_collection: str = "vista"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    routing: list[RoutingRule] = field(default_factory=list)


@dataclass
class Config:
    """Runtime configuration."""
    
    # GCS settings
    source_bucket: str
    cache_bucket: str
    source_prefix: str = ""
    cache_prefix: str = "cache/"
    
    # Processing settings
    workers: int = field(default_factory=lambda: __import__("os").cpu_count() or 4)
    max_pending: int = 20
    timeout: int = 120
    
    # Extraction settings
    max_pages: int = 500
    max_file_size: int = 50 * 1024 * 1024  # 50MB
    
    # Logging settings
    log_level: str = "INFO"
    error_file: str = "extraction_errors.log"
    
    # Qdrant settings
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)
    
    # Operation flags
    force: bool = False  # Force re-conversion and re-indexing even if cached


@dataclass
class ProcessingSummary:
    """Summary of a processing run."""
    
    total_files: int = 0
    filtered_files: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    total_time_ms: int = 0
    
    # Indexing statistics
    indexed: int = 0
    index_skipped: int = 0
    index_failed: int = 0
    
    def __str__(self) -> str:
        lines = [
            "Summary:",
            f"  Total files in index: {self.total_files}",
            f"  Office documents found: {self.filtered_files}",
            f"  Processed: {self.processed}",
            f"  Skipped (cached): {self.skipped}",
            f"  Failed: {self.failed}",
        ]
        # Include indexing stats if any indexing occurred
        if self.indexed > 0 or self.index_skipped > 0 or self.index_failed > 0:
            lines.extend([
                f"  Indexed: {self.indexed}",
                f"  Index skipped: {self.index_skipped}",
                f"  Index failed: {self.index_failed}",
            ])
        lines.append(f"  Total time: {self.total_time_ms / 1000:.1f}s")
        return "\n".join(lines)
