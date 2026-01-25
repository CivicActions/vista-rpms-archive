"""GCS-first document processing pipeline for Vista RPMS Archive.

This pipeline iterates directly over GCS bucket contents, classifies files,
and indexes them to appropriate Qdrant collections. Uses Qdrant as the sole
source of truth for tracking (no local SQLite or index.json).
"""

__version__ = "0.2.0"

from .embedder import Embedder
from .file_classifier import (
    ClassificationResult,
    FileCategory,
    classify_file,
    is_indexable_category,
    is_source_category,
)
from .gcs_client import GCSClient
from .pipeline_gcs import GCSPipeline, ProgressTracker
from .qdrant_client import QdrantIndexer, get_content_hash, get_point_id
from .router import Router
from .types import (
    Config,
    ExtractionResult,
    IndexingResult,
    ProcessingSummary,
    QdrantConfig,
    RoutingRule,
)

__all__ = [
    # Pipeline
    "GCSPipeline",
    "ProgressTracker",
    # Classification
    "ClassificationResult",
    "FileCategory",
    "classify_file",
    "is_indexable_category",
    "is_source_category",
    # Clients
    "Embedder",
    "GCSClient",
    "QdrantIndexer",
    "Router",
    # Types
    "Config",
    "ExtractionResult",
    "IndexingResult",
    "ProcessingSummary",
    "QdrantConfig",
    "RoutingRule",
    # Utilities
    "get_content_hash",
    "get_point_id",
]
