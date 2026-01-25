#!/usr/bin/env python3
"""GCS-first document processing pipeline CLI.

This pipeline iterates directly over GCS files, classifies them, and indexes
to appropriate Qdrant collections. Uses Qdrant as the sole source of truth
for tracking (no local SQLite or index.json).

Usage:
    python main.py --config config.toml
    python main.py --config config.toml --dry-run
    python main.py --config config.toml --limit 10
    python main.py --config config.toml --prefix "data/reports/"
"""

import argparse
import logging
import sys
from pathlib import Path

from src.config import load_config_file, merge_config, validate_config
from src.pipeline_gcs import GCSPipeline


def setup_logging(level: str) -> None:
    """Configure logging with the specified level."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def create_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="GCS-first document processing pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run pipeline with config file
  python main.py --config config.toml
  
  # Dry run (preview what would be processed)
  python main.py --config config.toml --dry-run
  
  # Process only first 100 files
  python main.py --config config.toml --limit 100
  
  # Process files under specific prefix
  python main.py --config config.toml --prefix "data/reports/2024/"
  
  # Force re-indexing of all files
  python main.py --config config.toml --force
  
Environment Variables:
  QDRANT_URL       - Qdrant server URL
  QDRANT_API_KEY   - Qdrant API key for authentication
""",
    )
    
    # Required config
    parser.add_argument(
        "--config", "-c",
        type=Path,
        required=True,
        help="Path to TOML configuration file",
    )
    
    # GCS overrides
    parser.add_argument(
        "--source-bucket",
        help="Override GCS source bucket",
    )
    parser.add_argument(
        "--cache-bucket",
        help="Override GCS cache bucket",
    )
    parser.add_argument(
        "--source-prefix",
        help="Override source prefix",
    )
    parser.add_argument(
        "--cache-prefix",
        help="Override cache prefix",
    )
    
    # Processing options
    parser.add_argument(
        "--prefix",
        help="Only process files under this GCS prefix",
    )
    parser.add_argument(
        "--limit", "-n",
        type=int,
        help="Limit number of files to process",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be processed without writing to Qdrant or cache",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force re-indexing even if files already exist in Qdrant",
    )
    
    # Advanced processing options (usually from config file)
    parser.add_argument(
        "--workers", "-w",
        type=int,
        help="Number of parallel workers (default: from config or CPU count)",
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
        default="INFO",
        help="Logging level (default: INFO)",
    )
    
    return parser


def main() -> int:
    """Main entry point for the pipeline CLI."""
    parser = create_parser()
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)
    
    # Load config file
    if not args.config.exists():
        logger.error(f"Config file not found: {args.config}")
        return 1
    
    try:
        file_config = load_config_file(args.config)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return 1
    
    # Merge with CLI args
    config = merge_config(file_config, args)
    
    # Validate config
    errors = validate_config(config)
    if errors:
        for error in errors:
            logger.error(f"Config error: {error}")
        return 1
    
    # Log configuration
    logger.info(f"Source bucket: gs://{config.source_bucket}/{config.source_prefix}")
    logger.info(f"Cache bucket: gs://{config.cache_bucket}/{config.cache_prefix}")
    logger.info(f"Qdrant URL: {config.qdrant.url}")
    if args.dry_run:
        logger.info("DRY RUN mode - no writes will be made")
    if args.force:
        logger.info("FORCE mode - will re-index all files")
    
    # Create and run pipeline
    try:
        pipeline = GCSPipeline(config=config)
        progress = pipeline.run(
            prefix=args.prefix,
            dry_run=args.dry_run,
            limit=args.limit,
        )
        
        # Return non-zero if there were failures
        if progress.extraction_failed > 0 or progress.index_failed > 0:
            return 1
        return 0
        
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        return 130
    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
