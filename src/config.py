"""Configuration loading for document extraction pipeline."""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from .types import Config, QdrantConfig, RoutingRule

logger = logging.getLogger(__name__)

# Python 3.11+ has tomllib in stdlib, earlier versions need tomli
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def load_config_file(config_path: Path) -> dict:
    """Load configuration from TOML file.
    
    Args:
        config_path: Path to TOML config file
    
    Returns:
        Parsed configuration dictionary
    
    Raises:
        FileNotFoundError: If config file doesn't exist
        tomllib.TOMLDecodeError: If config file is invalid
    """
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def create_arg_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser.
    
    Returns:
        Configured ArgumentParser
    """
    parser = argparse.ArgumentParser(
        description="Extract office documents to markdown cache",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with config file
  python extract.py --config config.toml
  
  # Override workers
  python extract.py --config config.toml --workers 4
  
  # Dry run (show what would be processed)
  python extract.py --config config.toml --dry-run
  
  # Show cache statistics only
  python extract.py --config config.toml --stats-only
""",
    )
    
    # Config file (required unless all GCS settings provided via CLI)
    parser.add_argument(
        "--config", "-c",
        type=Path,
        help="Path to TOML configuration file",
    )
    
    # GCS settings (can override config file)
    parser.add_argument(
        "--source-bucket",
        help="GCS bucket containing source files",
    )
    parser.add_argument(
        "--source-prefix",
        help="Prefix path within source bucket",
    )
    parser.add_argument(
        "--cache-bucket",
        help="GCS bucket for cached output",
    )
    parser.add_argument(
        "--cache-prefix",
        help="Prefix path within cache bucket",
    )
    
    # Processing settings
    parser.add_argument(
        "--workers", "-w",
        type=int,
        help=f"Number of parallel workers (default: CPU count = {os.cpu_count()})",
    )
    parser.add_argument(
        "--max-pending",
        type=int,
        help="Maximum concurrent tasks (default: 20)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="Per-file timeout in seconds (default: 120)",
    )
    
    # Extraction settings
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Maximum pages per document (default: 500)",
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        help="Maximum file size in bytes (default: 50MB)",
    )
    
    # Logging
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    
    # Operation modes
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without processing",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Show cache coverage statistics only",
    )
    
    # Filtering options
    parser.add_argument(
        "--prefix",
        help="Only process files matching this prefix (e.g., data/reports/2025/)",
    )
    parser.add_argument(
        "--no-archives",
        action="store_true",
        help="Skip archive processing (only process top-level files)",
    )
    parser.add_argument(
        "--limit", "-n",
        type=int,
        help="Limit number of files to process (useful for testing)",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force re-conversion and re-indexing even if cached",
    )
    
    return parser


def merge_config(file_config: dict, args: argparse.Namespace) -> Config:
    """Merge config file values with CLI arguments (CLI takes precedence).
    
    Args:
        file_config: Configuration from TOML file
        args: Parsed CLI arguments
    
    Returns:
        Merged Config object
    """
    # Extract sections from file config
    gcs = file_config.get("gcs", {})
    processing = file_config.get("processing", {})
    extraction = file_config.get("extraction", {})
    logging_cfg = file_config.get("logging", {})
    qdrant_cfg = file_config.get("qdrant", {})
    
    # Build Qdrant config with environment variable overrides
    qdrant_url = os.environ.get("QDRANT_URL") or qdrant_cfg.get("url", "http://localhost:6333")
    qdrant_api_key = os.environ.get("QDRANT_API_KEY") or qdrant_cfg.get("api_key", "")
    
    # Parse routing rules from config
    routing_rules = []
    for rule in qdrant_cfg.get("routing", []):
        if "pattern" in rule and "collection" in rule:
            routing_rules.append(RoutingRule(
                pattern=rule["pattern"],
                collection=rule["collection"],
            ))
    
    qdrant = QdrantConfig(
        url=qdrant_url,
        api_key=qdrant_api_key,
        default_collection=qdrant_cfg.get("default_collection", "vista"),
        embedding_model=qdrant_cfg.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2"),
        routing=routing_rules,
    )
    
    # Build config with CLI override (CLI args take precedence if not None)
    return Config(
        # GCS settings
        source_bucket=args.source_bucket or gcs.get("source_bucket", ""),
        cache_bucket=args.cache_bucket or gcs.get("cache_bucket", ""),
        source_prefix=args.source_prefix if args.source_prefix is not None else gcs.get("source_prefix", ""),
        cache_prefix=args.cache_prefix if args.cache_prefix is not None else gcs.get("cache_prefix", "cache/"),
        
        # Processing settings
        workers=args.workers if args.workers is not None else processing.get("workers", os.cpu_count() or 4),
        max_pending=args.max_pending if args.max_pending is not None else processing.get("max_pending", 20),
        timeout=args.timeout if args.timeout is not None else processing.get("timeout", 120),
        
        # Extraction settings
        max_pages=args.max_pages if args.max_pages is not None else extraction.get("max_pages", 500),
        max_file_size=args.max_file_size if args.max_file_size is not None else extraction.get("max_file_size", 50 * 1024 * 1024),
        max_source_size=extraction.get("max_source_size", 10 * 1024 * 1024),
        max_concurrent_large=processing.get("max_concurrent_large", 1),
        max_archive_members=extraction.get("max_archive_members", 2000),
        docling_recycle_after=extraction.get("docling_recycle_after", 10),
        max_concurrent_docling=extraction.get("max_concurrent_docling", 4),
        min_image_docling_size=extraction.get("min_image_docling_size", 50 * 1024),
        max_rss_gb=processing.get("max_rss_gb", 0),
        docling_conversion_timeout=extraction.get("docling_conversion_timeout", 600),
        
        # Logging settings
        log_level=args.log_level if args.log_level is not None else logging_cfg.get("level", "INFO"),
        error_file=logging_cfg.get("error_file", "extraction_errors.log"),
        
        # Qdrant settings
        qdrant=qdrant,
        
        # Operation flags
        force=args.force if hasattr(args, "force") else False,
    )


def validate_config(config: Config) -> list[str]:
    """Validate configuration and return list of errors.
    
    Args:
        config: Configuration to validate
    
    Returns:
        List of error messages (empty if valid)
    """
    errors = []
    
    if not config.source_bucket:
        errors.append("source_bucket is required")
    if not config.cache_bucket:
        errors.append("cache_bucket is required")
    if config.workers < 1:
        errors.append("workers must be at least 1")
    if config.max_pending < 1:
        errors.append("max_pending must be at least 1")
    if config.timeout < 1:
        errors.append("timeout must be at least 1 second")
    if config.max_file_size < 1:
        errors.append("max_file_size must be at least 1 byte")
    
    return errors


def load_config(args: Optional[argparse.Namespace] = None) -> Config:
    """Load configuration from file and CLI arguments.
    
    Args:
        args: Parsed CLI arguments (if None, parses sys.argv)
    
    Returns:
        Validated Config object
    
    Raises:
        SystemExit: If configuration is invalid
    """
    if args is None:
        parser = create_arg_parser()
        args = parser.parse_args()
    
    # Load config file if provided
    file_config = {}
    if args.config:
        if not args.config.exists():
            logger.error(f"Config file not found: {args.config}")
            sys.exit(1)
        try:
            file_config = load_config_file(args.config)
            logger.info(f"Loaded config from {args.config}")
        except Exception as e:
            logger.error(f"Error loading config file: {e}")
            sys.exit(1)
    
    # Merge with CLI args
    config = merge_config(file_config, args)
    
    # Validate
    errors = validate_config(config)
    if errors:
        for error in errors:
            logger.error(f"Config error: {error}")
        sys.exit(1)
    
    return config
