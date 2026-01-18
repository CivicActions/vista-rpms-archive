"""Document extraction pipeline for Vista RPMS Archive."""

__version__ = "0.1.0"

from .embedder import Embedder
from .router import Router
from .qdrant_client import QdrantIndexer, get_content_hash, get_point_id
from .types import (
    Config,
    ExtractionResult,
    IndexingResult,
    ProcessingSummary,
    QdrantConfig,
    RoutingRule,
)

__all__ = [
    "Config",
    "Embedder",
    "ExtractionResult",
    "IndexingResult",
    "ProcessingSummary",
    "QdrantConfig",
    "QdrantIndexer",
    "Router",
    "RoutingRule",
    "get_content_hash",
    "get_point_id",
]
