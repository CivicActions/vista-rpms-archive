"""Main processing pipeline for document extraction."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .archive_extractor import ArchiveExtractor
from .extractor import Extractor, create_extractor
from .gcs_client import GCSClient
from .index_loader import IndexLoader, has_office_extension
from .qdrant_client import QdrantIndexer
from .types import Config, ExtractionResult, IndexEntry, IndexingResult, ProcessingSummary

logger = logging.getLogger(__name__)


@dataclass
class ProgressTracker:
    """Thread-safe progress tracking for parallel processing."""
    
    total: int = 0
    to_process: int = 0
    cached: int = 0
    processed: int = 0
    failed: int = 0
    # Indexing stats
    indexed: int = 0
    index_skipped: int = 0
    index_failed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def mark_cached(self) -> None:
        """Mark a file as already cached (skipped)."""
        with self._lock:
            self.cached += 1
    
    def mark_processed(self, success: bool) -> None:
        """Mark a file as processed."""
        with self._lock:
            if success:
                self.processed += 1
            else:
                self.failed += 1
    
    def mark_indexed(self, status: str) -> None:
        """Mark indexing result (indexed/skipped/failed)."""
        with self._lock:
            if status == "indexed":
                self.indexed += 1
            elif status == "skipped":
                self.index_skipped += 1
            else:  # failed
                self.index_failed += 1
    
    def get_progress_str(self) -> str:
        """Get formatted progress string."""
        with self._lock:
            done = self.processed + self.failed
            parts = [
                f"[{done}/{self.to_process}]",
                f"processed={self.processed}",
                f"failed={self.failed}",
                f"cached={self.cached}",
            ]
            # Include indexing stats if any indexing occurred
            if self.indexed > 0 or self.index_skipped > 0 or self.index_failed > 0:
                parts.extend([
                    f"indexed={self.indexed}",
                    f"idx_skip={self.index_skipped}",
                ])
            return " ".join(parts)


class Pipeline:
    """Document extraction pipeline."""
    
    def __init__(
        self,
        config: Config,
        gcs_client: Optional[GCSClient] = None,
        extractor: Optional[Extractor] = None,
        indexer: Optional[QdrantIndexer] = None,
        enable_indexing: bool = True,
    ):
        """Initialize pipeline.
        
        Args:
            config: Pipeline configuration
            gcs_client: GCS client (created from config if not provided)
            extractor: Document extractor (created from config if not provided)
            indexer: Qdrant indexer (created from config if not provided)
            enable_indexing: Whether to enable Qdrant indexing (can be disabled for testing)
        """
        self.config = config
        self.enable_indexing = enable_indexing
        
        # Initialize GCS client
        self.gcs_client = gcs_client or GCSClient(
            source_bucket=config.source_bucket,
            cache_bucket=config.cache_bucket,
            source_prefix=config.source_prefix,
            cache_prefix=config.cache_prefix,
        )
        
        # Initialize extractor
        self.extractor = extractor or create_extractor(
            max_pages=config.max_pages,
            do_ocr=False,  # Always disabled per spec
        )
        
        # Initialize Qdrant indexer (lazy connection)
        self._indexer = indexer
        self._indexer_connected = False
        
        # Initialize index loader
        self.index_loader = IndexLoader(self.gcs_client)
        
        # Progress tracking
        self.progress = ProgressTracker()
        
        # Error logging
        self._error_file: Optional[Path] = None
        if config.error_file:
            self._error_file = Path(config.error_file)
    
    @property
    def indexer(self) -> Optional[QdrantIndexer]:
        """Lazy-initialize and return the Qdrant indexer."""
        if not self.enable_indexing:
            return None
        
        if self._indexer is None:
            try:
                self._indexer = QdrantIndexer(config=self.config.qdrant)
                self._indexer_connected = True
            except Exception as e:
                logger.warning(f"Failed to initialize Qdrant indexer: {e}")
                logger.warning("Indexing will be skipped for this run")
                self.enable_indexing = False
                return None
        
        return self._indexer
    
    def _log_error(self, source_path: str, error: str) -> None:
        """Log extraction error to file."""
        if not self._error_file:
            return
        
        timestamp = datetime.now().isoformat()
        log_line = f"{timestamp} | FAILED | {source_path} | {error}\n"
        
        try:
            with open(self._error_file, "a") as f:
                f.write(log_line)
        except Exception as e:
            logger.warning(f"Failed to write to error log: {e}")
    
    def _index_document(
        self,
        source_path: str,
        cache_path: str,
        markdown_content: str,
        file_size: int,
        original_format: str,
    ) -> Optional[IndexingResult]:
        """Index a document into Qdrant after successful conversion.
        
        Args:
            source_path: GCS source path of the document
            cache_path: GCS cache path of the converted markdown
            markdown_content: The markdown content to index
            file_size: Original file size in bytes
            original_format: Original file extension (.pdf, .html, etc.)
        
        Returns:
            IndexingResult if indexing was attempted, None if indexing is disabled
        """
        if not self.enable_indexing or self.indexer is None:
            return None
        
        try:
            result = self.indexer.index_document(
                source_path=source_path,
                content=markdown_content,
                cache_path=cache_path,
                file_size=file_size,
                original_format=original_format,
                force=self.config.force,
            )
            
            # Track indexing progress
            self.progress.mark_indexed(result.status)
            
            if result.status == "failed":
                logger.warning(
                    f"Indexing failed for {source_path}: {result.error}"
                )
            elif result.status == "indexed":
                logger.debug(
                    f"Indexed {source_path} to collection '{result.collection}'"
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Unexpected indexing error for {source_path}: {e}")
            self.progress.mark_indexed("failed")
            return IndexingResult(
                source_path=source_path,
                collection="unknown",
                status="failed",
                error=str(e),
            )

    def process_single_file(self, entry: IndexEntry) -> ExtractionResult:
        """Process a single file: download -> extract -> upload.
        
        Args:
            entry: Index entry to process
        
        Returns:
            ExtractionResult with outcome
        """
        start_time = time.time()
        source_path = entry.path
        
        # Check cache first (skip if force flag is set)
        if not self.config.force and self.gcs_client.cache_exists(source_path):
            logger.debug(f"Skipped (cached): {source_path}")
            return ExtractionResult(
                source_path=source_path,
                cache_path=self.gcs_client.cache_path_for_source(source_path),
                success=True,
                skipped=True,
                processing_time_ms=int((time.time() - start_time) * 1000),
            )
        
        # Check file size
        if entry.size_bytes > self.config.max_file_size:
            error = f"File too large: {entry.size_bytes} bytes > {self.config.max_file_size}"
            logger.warning(f"{source_path}: {error}")
            self._log_error(source_path, error)
            return ExtractionResult(
                source_path=source_path,
                success=False,
                error=error,
                processing_time_ms=int((time.time() - start_time) * 1000),
            )
        
        temp_path: Optional[Path] = None
        try:
            # Download to temp file
            temp_path = self.gcs_client.download_to_temp(source_path)
            
            # Extract to markdown
            markdown = self.extractor.extract_to_markdown(temp_path)
            
            # Upload to cache
            cache_path = self.gcs_client.cache_path_for_source(source_path)
            self.gcs_client.upload_markdown(cache_path, markdown)
            
            # Index document into Qdrant (if enabled)
            original_format = entry.extension or Path(source_path).suffix
            self._index_document(
                source_path=source_path,
                cache_path=cache_path,
                markdown_content=markdown,
                file_size=entry.size_bytes,
                original_format=original_format,
            )
            
            processing_time = int((time.time() - start_time) * 1000)
            logger.info(f"Processed: {source_path} ({processing_time}ms)")
            
            return ExtractionResult(
                source_path=source_path,
                cache_path=cache_path,
                success=True,
                skipped=False,
                processing_time_ms=processing_time,
            )
        
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.error(f"Failed: {source_path} - {error}")
            self._log_error(source_path, error)
            
            return ExtractionResult(
                source_path=source_path,
                success=False,
                error=error,
                processing_time_ms=int((time.time() - start_time) * 1000),
            )
        
        finally:
            # Clean up temp file
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
    
    def process_archive(self, entry: IndexEntry) -> list[ExtractionResult]:
        """Process all office documents within an archive.
        
        Downloads the archive, extracts office documents, converts to markdown,
        and uploads with archive path prefix (e.g., docs.zip/report.pdf.md).
        
        Args:
            entry: Archive IndexEntry with archive_contents metadata
        
        Returns:
            List of ExtractionResult for each file processed in the archive
        """
        results = []
        start_time = time.time()
        archive_path = entry.path
        
        # Skip archives with no office documents
        if not entry.has_office_documents_in_archive():
            logger.debug(f"Skipped archive (no office docs): {archive_path}")
            return results
        
        archive_temp_path: Optional[Path] = None
        try:
            # Download archive to temp file
            archive_temp_path = self.gcs_client.download_archive_to_temp(archive_path)
            
            # Extract and process office files
            with ArchiveExtractor() as extractor:
                for inner_path, extracted_path in extractor.extract_office_files(
                    archive_temp_path, entry
                ):
                    file_start = time.time()
                    
                    # Build cache path: <cache_prefix>/<archive_path>/<inner_path>.md
                    cache_path = f"{self.config.cache_prefix}{archive_path}/{inner_path}.md"
                    
                    # Check if already cached (skip if force flag is set)
                    if not self.config.force and self.gcs_client.cache_exists_by_path(cache_path):
                        logger.debug(f"Skipped (cached): {archive_path}/{inner_path}")
                        results.append(ExtractionResult(
                            source_path=f"{archive_path}/{inner_path}",
                            cache_path=cache_path,
                            success=True,
                            skipped=True,
                            processing_time_ms=int((time.time() - file_start) * 1000),
                        ))
                        continue
                    
                    try:
                        # Extract to markdown
                        markdown = self.extractor.extract_to_markdown(extracted_path)
                        
                        # Upload to cache
                        self.gcs_client.upload_markdown(cache_path, markdown)
                        
                        # Index document into Qdrant (if enabled)
                        source_full_path = f"{archive_path}/{inner_path}"
                        original_format = Path(inner_path).suffix
                        # Get file size from archive entry if available
                        file_size = 0
                        if entry.archive_contents:
                            for ae in entry.archive_contents:
                                if ae.name == inner_path:
                                    file_size = ae.size
                                    break
                        
                        self._index_document(
                            source_path=source_full_path,
                            cache_path=cache_path,
                            markdown_content=markdown,
                            file_size=file_size,
                            original_format=original_format,
                        )
                        
                        processing_time = int((time.time() - file_start) * 1000)
                        logger.info(f"Processed: {archive_path}/{inner_path} ({processing_time}ms)")
                        
                        results.append(ExtractionResult(
                            source_path=f"{archive_path}/{inner_path}",
                            cache_path=cache_path,
                            success=True,
                            skipped=False,
                            processing_time_ms=processing_time,
                        ))
                    
                    except Exception as e:
                        error = f"{type(e).__name__}: {e}"
                        logger.error(f"Failed: {archive_path}/{inner_path} - {error}")
                        self._log_error(f"{archive_path}/{inner_path}", error)
                        
                        results.append(ExtractionResult(
                            source_path=f"{archive_path}/{inner_path}",
                            success=False,
                            error=error,
                            processing_time_ms=int((time.time() - file_start) * 1000),
                        ))
        
        except Exception as e:
            error = f"Archive extraction failed: {type(e).__name__}: {e}"
            logger.error(f"Failed: {archive_path} - {error}")
            self._log_error(archive_path, error)
            
            results.append(ExtractionResult(
                source_path=archive_path,
                success=False,
                error=error,
                processing_time_ms=int((time.time() - start_time) * 1000),
            ))
        
        finally:
            # Clean up archive temp file
            if archive_temp_path and archive_temp_path.exists():
                try:
                    archive_temp_path.unlink()
                except Exception:
                    pass
        
        return results
    
    def _process_with_tracking(self, entry: IndexEntry) -> ExtractionResult:
        """Process a file and update progress tracking."""
        result = self.process_single_file(entry)
        
        if result.skipped:
            self.progress.mark_cached()
        else:
            self.progress.mark_processed(result.success)
        
        return result
    
    def run_sequential(
        self,
        entries: list[IndexEntry],
    ) -> Iterator[ExtractionResult]:
        """Process entries sequentially (for debugging or small batches).
        
        Args:
            entries: List of entries to process
        
        Yields:
            ExtractionResult for each processed entry
        """
        self.progress = ProgressTracker(
            total=len(entries),
            to_process=len(entries),
        )
        
        for i, entry in enumerate(entries):
            result = self._process_with_tracking(entry)
            
            if (i + 1) % 10 == 0 or i == len(entries) - 1:
                logger.info(self.progress.get_progress_str())
            
            yield result
    
    def run_parallel(
        self,
        entries: list[IndexEntry],
    ) -> Iterator[ExtractionResult]:
        """Process entries in parallel with backpressure control.
        
        Args:
            entries: List of entries to process
        
        Yields:
            ExtractionResult for each processed entry
        """
        self.progress = ProgressTracker(
            total=len(entries),
            to_process=len(entries),
        )
        
        if not entries:
            return
        
        logger.info(
            f"Processing {len(entries)} files with {self.config.workers} workers "
            f"(max_pending={self.config.max_pending})"
        )
        
        # Preload embedding model before starting parallel workers
        # to avoid race conditions during model download
        if self.indexer is not None:
            try:
                self.indexer.preload()
            except Exception as e:
                logger.warning(f"Failed to preload embedding model: {e}")
        
        with ThreadPoolExecutor(max_workers=self.config.workers) as executor:
            # Track pending futures for backpressure
            pending: dict = {}
            entry_iter = iter(entries)
            done_count = 0
            
            # Submit initial batch up to max_pending
            for entry in entry_iter:
                if len(pending) >= self.config.max_pending:
                    break
                future = executor.submit(self._process_with_tracking, entry)
                pending[future] = entry
            
            # Process results and submit more as capacity allows
            while pending:
                # Wait for at least one to complete
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                
                for future in done:
                    entry = pending.pop(future)
                    done_count += 1
                    
                    try:
                        result = future.result()
                        yield result
                    except Exception as e:
                        logger.error(f"Unexpected error processing {entry.path}: {e}")
                        yield ExtractionResult(
                            source_path=entry.path,
                            success=False,
                            error=str(e),
                        )
                    
                    # Log progress periodically
                    if done_count % 10 == 0:
                        logger.info(self.progress.get_progress_str())
                
                # Submit more tasks up to max_pending
                for entry in entry_iter:
                    if len(pending) >= self.config.max_pending:
                        break
                    future = executor.submit(self._process_with_tracking, entry)
                    pending[future] = entry
        
        # Final progress log
        logger.info(f"Completed: {self.progress.get_progress_str()}")
    
    def run(
        self,
        parallel: bool = True,
        dry_run: bool = False,
        stats_only: bool = False,
        include_archives: bool = True,
        path_prefix: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> ProcessingSummary:
        """Run the extraction pipeline.
        
        Args:
            parallel: Whether to use parallel processing
            dry_run: If True, show what would be processed without processing
            stats_only: If True, only show cache statistics
            include_archives: If True, also process archives containing office documents
            path_prefix: If provided, only process files matching this prefix
            limit: If provided, limit the number of files to process
        
        Returns:
            ProcessingSummary with run statistics
        """
        start_time = time.time()
        
        # Load index
        self.index_loader.load()
        total_files = self.index_loader.total_files
        
        # Filter to office documents
        office_docs = self.index_loader.filter_office_documents()
        
        # Apply path prefix filter if specified
        if path_prefix:
            office_docs = [e for e in office_docs if e.path.startswith(path_prefix)]
            logger.info(f"Filtered to {len(office_docs)} files matching prefix '{path_prefix}'")
        
        # Apply limit if specified
        if limit and len(office_docs) > limit:
            logger.info(f"Limiting to first {limit} files (out of {len(office_docs)})")
            office_docs = office_docs[:limit]
        
        # Filter to archives with office documents
        archives_with_docs = []
        if include_archives:
            archives_with_docs = self.index_loader.filter_archives_with_office_docs()
            if path_prefix:
                archives_with_docs = [e for e in archives_with_docs if e.path.startswith(path_prefix)]
        
        # Initialize summary
        summary = ProcessingSummary(
            total_files=total_files,
            filtered_files=len(office_docs),
        )
        
        if stats_only:
            # Just count cached files
            cached = sum(1 for e in office_docs if self.gcs_client.cache_exists(e.path))
            summary.skipped = cached
            summary.total_time_ms = int((time.time() - start_time) * 1000)
            
            logger.info(f"Cache coverage: {cached}/{len(office_docs)} office documents cached")
            if include_archives:
                logger.info(f"Archives with office documents: {len(archives_with_docs)}")
            return summary
        
        if dry_run:
            # Show what would be processed
            to_process = [e for e in office_docs if not self.gcs_client.cache_exists(e.path)]
            logger.info(f"Dry run: {len(to_process)} office documents would be processed")
            for entry in to_process[:20]:  # Show first 20
                logger.info(f"  - {entry.path}")
            if len(to_process) > 20:
                logger.info(f"  ... and {len(to_process) - 20} more")
            
            if include_archives and archives_with_docs:
                logger.info(f"Dry run: {len(archives_with_docs)} archives with office documents would be processed")
                for entry in archives_with_docs[:10]:
                    doc_count = sum(
                        1 for ae in (entry.archive_contents or [])
                        if not ae.is_dir and has_office_extension(ae.name)
                    )
                    logger.info(f"  - {entry.path} ({doc_count} office docs)")
            
            summary.total_time_ms = int((time.time() - start_time) * 1000)
            return summary
        
        # Process office documents
        logger.info(f"Processing {len(office_docs)} office documents...")
        process_fn = self.run_parallel if parallel else self.run_sequential
        
        for result in process_fn(office_docs):
            if result.skipped:
                summary.skipped += 1
            elif result.success:
                summary.processed += 1
            else:
                summary.failed += 1
        
        # Process archives
        if include_archives and archives_with_docs:
            logger.info(f"Processing {len(archives_with_docs)} archives with office documents...")
            
            for archive_entry in archives_with_docs:
                archive_results = self.process_archive(archive_entry)
                
                for result in archive_results:
                    if result.skipped:
                        summary.skipped += 1
                    elif result.success:
                        summary.processed += 1
                    else:
                        summary.failed += 1
        
        # Update indexing statistics from progress tracker
        summary.indexed = self.progress.indexed
        summary.index_skipped = self.progress.index_skipped
        summary.index_failed = self.progress.index_failed
        
        summary.total_time_ms = int((time.time() - start_time) * 1000)
        
        # Close Qdrant connection if open
        if self._indexer is not None:
            self._indexer.close()
        
        logger.info(str(summary))
        return summary

