#!/usr/bin/env python3
"""CLI entry point for document extraction pipeline.

Usage:
    python extract.py --config config.toml
    python extract.py --config config.toml --workers 4
    python extract.py --config config.toml --dry-run
    python extract.py --config config.toml --stats-only
"""

import logging
import sys

from src.config import create_arg_parser, load_config
from src.pipeline import Pipeline


def setup_logging(level: str) -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> int:
    """Main entry point.
    
    Returns:
        Exit code (0 for success, 1 for error)
    """
    # Parse arguments
    parser = create_arg_parser()
    args = parser.parse_args()
    
    # Load config
    try:
        config = load_config(args)
    except SystemExit:
        return 1
    
    # Setup logging
    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)
    
    logger.info("Document Extraction Pipeline")
    logger.info(f"Source: gs://{config.source_bucket}/{config.source_prefix}")
    logger.info(f"Cache: gs://{config.cache_bucket}/{config.cache_prefix}")
    logger.info(f"Workers: {config.workers}, Max pending: {config.max_pending}")
    
    # Check for operation modes
    dry_run = getattr(args, "dry_run", False)
    stats_only = getattr(args, "stats_only", False)
    path_prefix = getattr(args, "prefix", None)
    no_archives = getattr(args, "no_archives", False)
    limit = getattr(args, "limit", None)
    
    if dry_run:
        logger.info("DRY RUN MODE - no files will be processed")
    if stats_only:
        logger.info("STATS ONLY MODE - showing cache coverage")
    if path_prefix:
        logger.info(f"FILTER: Only processing files matching prefix: {path_prefix}")
    if no_archives:
        logger.info("SKIP ARCHIVES: Only processing top-level files")
    if limit:
        logger.info(f"LIMIT: Processing at most {limit} files")
    
    # Create and run pipeline
    try:
        pipeline = Pipeline(config)
        summary = pipeline.run(
            parallel=config.workers > 1,
            dry_run=dry_run,
            stats_only=stats_only,
            include_archives=not no_archives,
            path_prefix=path_prefix,
            limit=limit,
        )
        
        # Exit with error code if any files failed
        if summary.failed > 0:
            logger.warning(f"{summary.failed} files failed extraction")
            return 1
        
        return 0
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
