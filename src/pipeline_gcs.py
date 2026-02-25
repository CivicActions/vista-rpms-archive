"""GCS-first document processing pipeline.

This pipeline iterates directly over GCS files, using Qdrant as the sole
source of truth for tracking indexed files. No local SQLite or index.json.
"""

import atexit
import gc
import logging
import os
import psutil
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from google.cloud import storage

from .archive_extractor import ArchiveExtractor
from .extractor import Extractor, create_extractor
from .file_classifier import (
    ClassificationResult,
    FileCategory,
    classify_file,
    is_indexable_category,
    is_source_category,
)
from .gcs_client import GCSClient
from .qdrant_client import QdrantIndexer
from .types import Config, ExtractionResult, IndexingResult, ARCHIVE_MIME_TYPES, OFFICE_MIME_TYPES

logger = logging.getLogger(__name__)


def _short_path(path: str) -> str:
    """Shorten a path for display by removing the 'source/' prefix."""
    if path.startswith('source/'):
        return path[7:]
    return path


def _log_memory_usage() -> None:
    """Log current memory usage."""
    try:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem_percent = process.memory_percent()
        logger.debug(
            f"Memory usage: {mem_info.rss / 1024 / 1024:.1f} MB "
            f"({mem_percent:.1f}% of system)"
        )
    except Exception:
        pass  # psutil not available or error


def _install_crash_handlers() -> None:
    """Install atexit and signal handlers to log unexpected terminations.
    
    Helps diagnose OOM kills and other silent exits by ensuring
    a final log message is written whenever possible.
    """
    _crash_handlers_state = {"clean_exit": False}

    def _atexit_handler():
        if not _crash_handlers_state["clean_exit"]:
            logger.error(
                "UNEXPECTED EXIT: Pipeline did not complete normally. "
                "This is likely an OOM kill (check dmesg or /var/log/kern.log)."
            )
            _log_memory_usage()
            logging.shutdown()

    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.error(f"SIGNAL RECEIVED: {sig_name} ({signum}) — shutting down")
        _log_memory_usage()
        logging.shutdown()
        # Re-raise with default handler so exit code is correct
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    atexit.register(_atexit_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    return _crash_handlers_state


@dataclass
class ProgressTracker:
    """Thread-safe progress tracking for pipeline processing."""
    
    total_blobs: int = 0
    processed: int = 0
    skipped_indexed: int = 0  # Already in Qdrant
    skipped_binary: int = 0   # Binary files
    skipped_cached: int = 0   # Cached but not indexed (shouldn't happen in new arch)
    indexed: int = 0
    index_failed: int = 0
    extraction_failed: int = 0
    archives_processed: int = 0
    archive_files_extracted: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _start_time: float = field(default_factory=time.time)
    
    def mark_processed(self) -> None:
        """Mark a blob as processed."""
        with self._lock:
            self.processed += 1
    
    def mark_skipped_indexed(self) -> None:
        """Mark a file as skipped (already indexed in Qdrant)."""
        with self._lock:
            self.skipped_indexed += 1
    
    def mark_skipped_binary(self) -> None:
        """Mark a file as skipped (binary/unprocessable)."""
        with self._lock:
            self.skipped_binary += 1
    
    def mark_indexed(self) -> None:
        """Mark a file as successfully indexed."""
        with self._lock:
            self.indexed += 1
    
    def mark_index_failed(self) -> None:
        """Mark an indexing failure."""
        with self._lock:
            self.index_failed += 1
    
    def mark_extraction_failed(self) -> None:
        """Mark an extraction failure."""
        with self._lock:
            self.extraction_failed += 1
    
    def mark_archive_processed(self, file_count: int) -> None:
        """Mark an archive as processed."""
        with self._lock:
            self.archives_processed += 1
            self.archive_files_extracted += file_count
    
    def get_progress_str(self) -> str:
        """Get formatted progress string for logging."""
        with self._lock:
            elapsed = time.time() - self._start_time
            rate = self.processed / elapsed if elapsed > 0 else 0
            return (
                f"[{self.processed}/{self.total_blobs}] "
                f"indexed={self.indexed} skip_indexed={self.skipped_indexed} "
                f"skip_binary={self.skipped_binary} "
                f"idx_fail={self.index_failed} ext_fail={self.extraction_failed} "
                f"archives={self.archives_processed} "
                f"({rate:.1f} files/sec)"
            )
    
    def get_summary(self) -> str:
        """Get final summary string."""
        with self._lock:
            elapsed = time.time() - self._start_time
            return (
                f"\n{'='*60}\n"
                f"Pipeline Run Summary\n"
                f"{'='*60}\n"
                f"Total GCS blobs scanned:  {self.total_blobs}\n"
                f"Files processed:          {self.processed}\n"
                f"Successfully indexed:     {self.indexed}\n"
                f"Skipped (already indexed):{self.skipped_indexed}\n"
                f"Skipped (binary/other):   {self.skipped_binary}\n"
                f"Index failures:           {self.index_failed}\n"
                f"Extraction failures:      {self.extraction_failed}\n"
                f"Archives processed:       {self.archives_processed}\n"
                f"Files from archives:      {self.archive_files_extracted}\n"
                f"Total time:               {elapsed:.1f}s\n"
                f"{'='*60}"
            )


class GCSPipeline:
    """GCS-first document processing pipeline.
    
    Iterates directly over GCS bucket contents, classifies each file,
    and indexes to the appropriate Qdrant collection. Uses Qdrant for
    skip detection instead of local tracking.
    """
    
    def __init__(
        self,
        config: Config,
        gcs_client: Optional[GCSClient] = None,
        extractor: Optional[Extractor] = None,
        indexer: Optional[QdrantIndexer] = None,
    ) -> None:
        """Initialize GCS pipeline.
        
        Args:
            config: Pipeline configuration.
            gcs_client: GCS client (created from config if not provided).
            extractor: Document extractor (created if not provided).
            indexer: Qdrant indexer (created if not provided).
        """
        self.config = config
        
        # Initialize GCS client
        self.gcs_client = gcs_client or GCSClient(
            source_bucket=config.source_bucket,
            cache_bucket=config.cache_bucket,
            source_prefix=config.source_prefix,
            cache_prefix=config.cache_prefix,
        )
        
        # Initialize extractor (for docling conversion)
        self.extractor = extractor or create_extractor(
            max_pages=config.max_pages,
            do_ocr=False,
        )
        
        # Initialize Qdrant indexer
        self._indexer = indexer
        
        # Progress tracking
        self.progress = ProgressTracker()
        
        # Error log file
        self._error_file: Optional[Path] = None
        if config.error_file:
            self._error_file = Path(config.error_file)
    
    @property
    def indexer(self) -> Optional[QdrantIndexer]:
        """Lazy-initialize Qdrant indexer on first access."""
        if self._indexer is None:
            try:
                self._indexer = QdrantIndexer(config=self.config.qdrant)
            except Exception as e:
                logger.error(f"Failed to initialize Qdrant indexer: {e}")
                raise
        return self._indexer
    
    def run(
        self,
        prefix: Optional[str] = None,
        dry_run: bool = False,
        limit: Optional[int] = None,
        parallel: bool = True,
    ) -> ProgressTracker:
        """Run the pipeline over all GCS files.
        
        Args:
            prefix: Optional GCS prefix to filter files.
            dry_run: If True, don't write to Qdrant or GCS cache.
            limit: Optional limit on number of files to process.
            parallel: If True, process files in parallel using thread pool.
        
        Returns:
            ProgressTracker with run statistics.
        """
        try:
            logger.info(f"Starting GCS pipeline run (dry_run={dry_run}, limit={limit}, parallel={parallel})")
            _log_memory_usage()
            
            # Install crash handlers to diagnose OOM kills and other silent exits
            crash_state = _install_crash_handlers()
            
            # Warn if max_pending is too high for memory constraints
            if parallel and self.config.workers > 1:
                max_memory_estimate_mb = (self.config.workers * self.config.max_pending * 10)  # ~10MB per pending task estimate
                if max_memory_estimate_mb > 5000:  # > 5GB
                    logger.warning(
                        f"High memory usage expected: workers={self.config.workers}, "
                        f"max_pending={self.config.max_pending} (~{max_memory_estimate_mb}MB). "
                        f"If OOM occurs, reduce max_pending or workers in config."
                    )
            
            # Preload embedding model before starting parallel workers
            # to avoid race conditions during model download
            if not dry_run and self.indexer:
                self.indexer.preload()
            
            # Count blobs via streaming iterator to avoid loading all blob
            # objects into memory (526K+ blobs can consume tens of GB of RAM).
            blob_iter = self.gcs_client.list_blobs(prefix=prefix)
            
            if limit:
                # With a limit, it's safe to collect into a small list
                blobs_to_process = []
                for blob in blob_iter:
                    blobs_to_process.append(blob)
                    if len(blobs_to_process) >= limit:
                        break
                self.progress.total_blobs = len(blobs_to_process)
                blob_iter = iter(blobs_to_process)
            else:
                # Without a limit, count blobs first by streaming (only names, not full objects)
                logger.info("Counting blobs in bucket (streaming)...")
                count = 0
                for _ in self.gcs_client.list_blobs(prefix=prefix):
                    count += 1
                    if count % 100000 == 0:
                        logger.info(f"  ...counted {count} blobs so far")
                self.progress.total_blobs = count
                logger.info(f"Found {count} blobs to process")
                _log_memory_usage()
                # Re-create iterator for actual processing
                blob_iter = self.gcs_client.list_blobs(prefix=prefix)
            
            logger.info(f"Total blobs to process: {self.progress.total_blobs}")
            
            if self.progress.total_blobs == 0:
                logger.info("No blobs to process")
                crash_state["clean_exit"] = True
                return self.progress
            
            # Process blobs
            logger.info(f"Starting blob processing (parallel={parallel})")
            if parallel and self.config.workers > 1:
                self._run_parallel(blob_iter, dry_run=dry_run)
            else:
                self._run_sequential(blob_iter, dry_run=dry_run)
            
            logger.info("Blob processing complete")
            
            # Final summary
            logger.info(self.progress.get_summary())
            
            crash_state["clean_exit"] = True
            return self.progress
        except Exception as e:
            logger.error(f"Pipeline run failed: {e}", exc_info=True)
            raise
    
    def _run_sequential(
        self,
        blobs: Iterator[storage.Blob],
        dry_run: bool = False,
    ) -> None:
        """Process blobs sequentially.
        
        Args:
            blobs: Iterator of blobs to process.
            dry_run: If True, don't write to Qdrant or GCS cache.
        """
        for i, blob in enumerate(blobs):
            try:
                self._process_blob(blob, dry_run=dry_run)
            except Exception as e:
                logger.error(f"Error processing blob {blob.name}: {e}")
                self._log_error(blob.name, str(e))
                self.progress.mark_extraction_failed()
            
            # Log progress every 100 files
            if (i + 1) % 100 == 0:
                logger.info(self.progress.get_progress_str())
    
    def _run_parallel(
        self,
        blobs: Iterator[storage.Blob],
        dry_run: bool = False,
    ) -> None:
        """Process blobs in parallel with backpressure control.
        
        Uses ThreadPoolExecutor with max_pending to control memory usage.
        Accepts an iterator to avoid holding all blob objects in memory.
        
        Args:
            blobs: Iterator of blobs to process.
            dry_run: If True, don't write to Qdrant or GCS cache.
        """
        workers = self.config.workers
        max_pending = self.config.max_pending
        
        logger.info(
            f"Processing {self.progress.total_blobs} files with {workers} workers "
            f"(max_pending={max_pending})"
        )
        
        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                # Track pending futures for backpressure
                pending: dict = {}
                blob_iter = iter(blobs)
                done_count = 0
                
                # Submit initial batch up to max_pending
                for blob in blob_iter:
                    if len(pending) >= max_pending:
                        break
                    future = executor.submit(self._process_blob_with_error_handling, blob, dry_run)
                    pending[future] = blob
                
                logger.debug(f"Initial batch submitted: {len(pending)} tasks")
                
                # Process results and submit more as capacity allows
                while pending:
                    # Wait for at least one to complete
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    
                    for future in done:
                        blob = pending.pop(future)
                        done_count += 1
                        
                        try:
                            future.result()  # Raises if there was an exception
                        except Exception as e:
                            # This shouldn't happen since _process_blob_with_error_handling
                            # catches exceptions, but just in case
                            logger.error(f"[{_short_path(blob.name)}] Executor error: {e}")
                        
                        # Log progress periodically with memory tracking
                        if done_count % 100 == 0:
                            logger.info(self.progress.get_progress_str())
                            _log_memory_usage()
                        
                        # Run garbage collection periodically
                        if done_count % 500 == 0:
                            logger.debug("Running garbage collection...")
                            gc.collect()
                            _log_memory_usage()
                    
                    logger.debug(f"Tasks completed: {done_count}, pending: {len(pending)}")
                    
                    # Submit more tasks up to max_pending
                    for blob in blob_iter:
                        if len(pending) >= max_pending:
                            break
                        future = executor.submit(self._process_blob_with_error_handling, blob, dry_run)
                        pending[future] = blob
                
                logger.info(f"All parallel tasks completed: {done_count} processed")
            
            logger.info(f"Executor shutdown complete: {self.progress.get_progress_str()}")
        except Exception as e:
            logger.error(f"Parallel processing failed: {e}", exc_info=True)
            raise
    
    def _process_blob_with_error_handling(
        self,
        blob: storage.Blob,
        dry_run: bool = False,
    ) -> None:
        """Process a blob with error handling for parallel execution.
        
        Wraps _process_blob to catch and log errors without raising,
        so that parallel workers continue processing other files.
        
        Args:
            blob: GCS Blob to process.
            dry_run: If True, don't write to Qdrant or GCS cache.
        """
        try:
            self._process_blob(blob, dry_run=dry_run)
        except Exception as e:
            logger.error(f"[{_short_path(blob.name)}] Processing error: {e}")
            self._log_error(blob.name, str(e))
            self.progress.mark_extraction_failed()
        finally:
            # Always mark as processed and attempt cleanup
            self.progress.mark_processed()
            # Hint to Python to clean up unused objects
            gc.collect()

    
    def _process_blob(self, blob: storage.Blob, dry_run: bool = False) -> None:
        """Process a single GCS blob.
        
        Flow:
        1. Download content and classify
        2. If archive -> extract and process contents
        3. If documentation -> check cache, docling, index
        4. If source code -> direct index
        5. If binary -> skip
        
        Args:
            blob: GCS Blob object to process.
            dry_run: If True, don't write to Qdrant or GCS cache.
        """
        source_path = blob.name
        
        # Skip directories (blobs ending with /)
        if source_path.endswith('/'):
            return
        
        # Skip index.json (legacy manifest file)
        if source_path.endswith('index.json'):
            logger.debug(f"[{_short_path(source_path)}] Skipping legacy index.json")
            return
        
        # Check if already indexed in Qdrant (skip detection)
        # Route first to determine target collection, then check only that
        # collection instead of all collections (reduces HTTP requests from
        # N collections to 1 per blob).
        # Skip this check in dry-run mode to avoid needing Qdrant connection
        if not dry_run and not self.config.force and self.indexer:
            # Pre-route: check both doc and source collection variants
            base_collection = self.indexer.router.route(source_path)
            source_collection = f"{base_collection}-source"
            try:
                exists, collection = self.indexer.exists_by_path(
                    source_path,
                    collection_name=base_collection,
                )
                if not exists:
                    exists, collection = self.indexer.exists_by_path(
                        source_path,
                        collection_name=source_collection,
                    )
                if exists:
                    logger.info(f"[{_short_path(source_path)}] Skipped (indexed in {collection})")
                    self.progress.mark_skipped_indexed()
                    self.progress.mark_processed()
                    return
            except Exception as e:
                logger.warning(f"[{_short_path(source_path)}] Skip check failed: {e}")
                # Continue processing if skip check fails
        
        # For large files, download to temp file instead of loading entirely
        # into memory. This prevents OOM when multiple workers hit large files
        # concurrently. Chunking in index_document handles the Qdrant payload
        # size limit. Classification only needs the first few KB.
        MAX_INMEMORY_BYTES = 32 * 1024 * 1024  # 32MB
        file_size = blob.size or 0
        
        if file_size > MAX_INMEMORY_BYTES:
            logger.info(
                f"[{_short_path(source_path)}] Large file "
                f"({file_size / 1024 / 1024:.1f} MB), using temp file"
            )
            self._process_large_blob(blob, source_path, file_size, dry_run=dry_run)
            self.progress.mark_processed()
            return
        
        # Download blob content for classification
        try:
            content = self.gcs_client.download_blob_content(blob)
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Download failed: {e}")
            self._log_error(source_path, f"Download failed: {e}")
            self.progress.mark_extraction_failed()
            self.progress.mark_processed()
            return
        
        # Classify the file
        classification = classify_file(source_path, content)
        logger.debug(f"[{_short_path(source_path)}] Classified: {classification.category.value} ({classification.reason})")
        
        # Handle based on category
        if classification.category == FileCategory.BINARY:
            # Check if it's an archive (ZIP/TAR) - these are "binary" but contain files
            if self._is_archive_mime(content):
                self._process_archive(blob, content, source_path, dry_run=dry_run)
            else:
                logger.info(f"[{_short_path(source_path)}] Skipped ({classification.reason})")
                self.progress.mark_skipped_binary()
            self.progress.mark_processed()
            return
        
        if not is_indexable_category(classification.category):
            logger.info(f"[{_short_path(source_path)}] Skipped ({classification.reason})")
            self.progress.mark_skipped_binary()
            self.progress.mark_processed()
            return
        
        # Process the file based on category
        if is_source_category(classification.category):
            # Source code - index directly without docling
            self._process_source_file(
                source_path=source_path,
                content=content,
                classification=classification,
                file_size=blob.size or len(content),
                dry_run=dry_run,
            )
        else:
            # Documentation - may need docling processing
            self._process_doc_file(
                blob=blob,
                source_path=source_path,
                content=content,
                classification=classification,
                dry_run=dry_run,
            )
        
        self.progress.mark_processed()
    
    def _process_large_blob(
        self,
        blob: storage.Blob,
        source_path: str,
        file_size: int,
        dry_run: bool = False,
    ) -> None:
        """Process a large blob via temp file to avoid holding it in memory.
        
        Downloads to disk, classifies from the first bytes, and reads
        content from disk for indexing. Chunking in index_document
        handles splitting into Qdrant-compatible payloads.
        
        Args:
            blob: GCS Blob object.
            source_path: GCS source path.
            file_size: File size in bytes.
            dry_run: If True, don't write to Qdrant or GCS cache.
        """
        temp_path = None
        try:
            temp_path = self.gcs_client.download_blob_to_temp(blob)
            
            # Read first 8KB for classification
            with open(temp_path, 'rb') as f:
                header = f.read(8192)
            
            classification = classify_file(source_path, header)
            logger.debug(
                f"[{_short_path(source_path)}] Classified (large): "
                f"{classification.category.value} ({classification.reason})"
            )
            
            if classification.category == FileCategory.BINARY:
                if self._is_archive_mime(header):
                    # Re-read full content for archive processing
                    with open(temp_path, 'rb') as f:
                        content = f.read()
                    self._process_archive(blob, content, source_path, dry_run=dry_run)
                    del content
                else:
                    logger.info(f"[{_short_path(source_path)}] Skipped ({classification.reason})")
                    self.progress.mark_skipped_binary()
                return
            
            if not is_indexable_category(classification.category):
                logger.info(f"[{_short_path(source_path)}] Skipped ({classification.reason})")
                self.progress.mark_skipped_binary()
                return
            
            # Read full content from disk (not from GCS again)
            try:
                with open(temp_path, 'r', encoding='utf-8', errors='replace') as f:
                    text_content = f.read()
            except Exception as e:
                logger.error(f"[{_short_path(source_path)}] Failed to read temp file: {e}")
                self.progress.mark_extraction_failed()
                return
            
            if is_source_category(classification.category):
                self._process_source_file(
                    source_path=source_path,
                    content=text_content.encode('utf-8', errors='replace'),
                    classification=classification,
                    file_size=file_size,
                    dry_run=dry_run,
                )
            else:
                # Large doc file — use docling via temp path
                cache_path = self.gcs_client.cache_path_for_source(source_path)
                markdown = self._extract_with_docling(
                    source_path=source_path,
                    temp_path=temp_path,
                    cache_path=cache_path,
                    dry_run=dry_run,
                )
                if markdown is None:
                    self.progress.mark_extraction_failed()
                    return
                
                if dry_run:
                    logger.info(f"[{_short_path(source_path)}] [DRY RUN] Would index large doc")
                    self.progress.mark_indexed()
                    return
                
                if not self.indexer:
                    return
                
                try:
                    result = self.indexer.index_document(
                        source_path=source_path,
                        content=markdown,
                        cache_path=cache_path,
                        file_size=file_size,
                        original_format=Path(source_path).suffix,
                        is_source_code=False,
                    )
                    if result.status == "indexed":
                        self.progress.mark_indexed()
                        logger.info(f"[{_short_path(source_path)}] Indexed to {result.collection}")
                    elif result.status == "skipped":
                        self.progress.mark_skipped_indexed()
                    else:
                        self.progress.mark_index_failed()
                        logger.warning(f"[{_short_path(source_path)}] Index failed: {result.error}")
                except Exception as e:
                    logger.error(f"[{_short_path(source_path)}] Indexing error: {e}")
                    self.progress.mark_index_failed()
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Large file processing failed: {e}")
            self._log_error(source_path, f"Large file processing failed: {e}")
            self.progress.mark_extraction_failed()
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def _is_archive_mime(self, content: bytes) -> bool:
        """Check if content is an archive by magic bytes."""
        # ZIP magic: PK\x03\x04
        if content.startswith(b'PK\x03\x04'):
            # But exclude Office documents (DOCX, XLSX, PPTX)
            # They also start with PK but contain specific signatures
            if b'word/' in content[:2000] or b'xl/' in content[:2000] or b'ppt/' in content[:2000]:
                return False
            return True
        
        # TAR magic: various
        if content.startswith(b'\x1f\x8b'):  # gzip
            return True
        if content[257:262] == b'ustar':  # tar
            return True
        if content.startswith(b'BZh'):  # bzip2
            return True
        
        return False
    
    def _process_archive(
        self,
        blob: storage.Blob,
        content: bytes,
        archive_path: str,
        dry_run: bool = False,
    ) -> None:
        """Process an archive file by extracting and processing its contents.
        
        Args:
            blob: GCS Blob of the archive.
            content: Archive content bytes.
            archive_path: GCS path of the archive.
            dry_run: If True, don't write to Qdrant or GCS cache.
        """
        logger.info(f"[{_short_path(archive_path)}] Processing archive")
        
        # Download archive to temp file for extraction
        temp_path = None
        extracted_count = 0
        
        try:
            temp_path = self.gcs_client.download_blob_to_temp(blob)
            
            with ArchiveExtractor() as extractor:
                for rel_path, extracted_path, file_content in extractor.extract_all_files(temp_path):
                    # Construct full source path: archive_name/relative_path
                    full_source_path = f"{archive_path}/{rel_path}"
                    
                    # Check if already indexed — route first to avoid checking all collections
                    if not self.config.force and self.indexer:
                        base_coll = self.indexer.router.route(full_source_path)
                        exists, _ = self.indexer.exists_by_path(full_source_path, collection_name=base_coll)
                        if not exists:
                            exists, _ = self.indexer.exists_by_path(
                                full_source_path, collection_name=f"{base_coll}-source"
                            )
                        if exists:
                            logger.debug(f"[{_short_path(full_source_path)}] Skipped (indexed)")
                            self.progress.mark_skipped_indexed()
                            continue
                    
                    # Classify the extracted file
                    classification = classify_file(rel_path, file_content)
                    
                    if not is_indexable_category(classification.category):
                        logger.debug(f"[{_short_path(full_source_path)}] Skipped (non-indexable)")
                        continue
                    
                    # Process based on category
                    if is_source_category(classification.category):
                        self._process_source_file(
                            source_path=full_source_path,
                            content=file_content,
                            classification=classification,
                            file_size=len(file_content),
                            dry_run=dry_run,
                        )
                    else:
                        # Documentation file from archive
                        self._process_doc_file_from_bytes(
                            source_path=full_source_path,
                            content=file_content,
                            classification=classification,
                            extracted_path=extracted_path,
                            dry_run=dry_run,
                        )
                    
                    extracted_count += 1
            
            self.progress.mark_archive_processed(extracted_count)
            logger.info(f"[{_short_path(archive_path)}] Archive complete: {extracted_count} files")
            
        except Exception as e:
            logger.error(f"[{_short_path(archive_path)}] Archive extraction failed: {e}")
            self._log_error(archive_path, f"Archive extraction failed: {e}")
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
    
    def _process_source_file(
        self,
        source_path: str,
        content: bytes,
        classification: ClassificationResult,
        file_size: int,
        dry_run: bool = False,
    ) -> None:
        """Process a source code file (direct indexing without docling).
        
        Args:
            source_path: GCS source path.
            content: File content bytes.
            classification: Classification result.
            file_size: File size in bytes.
            dry_run: If True, don't write to Qdrant.
        """
        # Decode content
        try:
            text_content = content.decode('utf-8', errors='replace')
        except Exception as e:
            logger.warning(f"[{_short_path(source_path)}] Failed to decode: {e}")
            self.progress.mark_extraction_failed()
            return
        
        # Index to source collection
        if dry_run:
            logger.info(f"[{_short_path(source_path)}] [DRY RUN] Would index source")
            self.progress.mark_indexed()
            return
        
        if not self.indexer:
            logger.warning(f"[{_short_path(source_path)}] No indexer available")
            return
        
        try:
            # Source files don't use cache - index directly
            result = self.indexer.index_document(
                source_path=source_path,
                content=text_content,
                cache_path="",  # No cache for source files
                file_size=file_size,
                original_format=Path(source_path).suffix,
                is_source_code=True,
            )
            
            if result.status == "indexed":
                self.progress.mark_indexed()
                logger.info(f"[{_short_path(source_path)}] Indexed to {result.collection}")
            elif result.status == "skipped":
                self.progress.mark_skipped_indexed()
            else:
                self.progress.mark_index_failed()
                logger.warning(f"[{_short_path(source_path)}] Index failed: {result.error}")
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Indexing error: {e}")
            self.progress.mark_index_failed()
    
    def _process_doc_file(
        self,
        blob: storage.Blob,
        source_path: str,
        content: bytes,
        classification: ClassificationResult,
        dry_run: bool = False,
    ) -> None:
        """Process a documentation file (with GCS cache and docling).
        
        Args:
            blob: GCS Blob object.
            source_path: GCS source path.
            content: File content bytes.
            classification: Classification result.
            dry_run: If True, don't write to Qdrant or GCS cache.
        """
        cache_path = self.gcs_client.cache_path_for_source(source_path)
        file_size = blob.size or len(content)
        
        # Get markdown content (from cache or via processing)
        markdown_content = self._get_or_create_markdown(
            source_path=source_path,
            content=content,
            blob=blob,
            cache_path=cache_path,
            dry_run=dry_run,
        )
        
        if markdown_content is None:
            self.progress.mark_extraction_failed()
            return
        
        # Index the markdown
        if dry_run:
            logger.info(f"[{_short_path(source_path)}] [DRY RUN] Would index doc")
            self.progress.mark_indexed()
            return
        
        if not self.indexer:
            return
        
        try:
            result = self.indexer.index_document(
                source_path=source_path,
                content=markdown_content,
                cache_path=cache_path,
                file_size=file_size,
                original_format=Path(source_path).suffix,
                is_source_code=False,
            )
            
            if result.status == "indexed":
                self.progress.mark_indexed()
                logger.info(f"[{_short_path(source_path)}] Indexed to {result.collection}")
            elif result.status == "skipped":
                self.progress.mark_skipped_indexed()
            else:
                self.progress.mark_index_failed()
                logger.warning(f"[{_short_path(source_path)}] Index failed: {result.error}")
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Indexing error: {e}")
            self.progress.mark_index_failed()
    
    def _process_doc_file_from_bytes(
        self,
        source_path: str,
        content: bytes,
        classification: ClassificationResult,
        extracted_path: Path,
        dry_run: bool = False,
    ) -> None:
        """Process a documentation file extracted from an archive.
        
        Args:
            source_path: Full source path (archive/relative_path).
            content: File content bytes.
            classification: Classification result.
            extracted_path: Path to extracted temp file.
            dry_run: If True, don't write to Qdrant or GCS cache.
        """
        cache_path = self.gcs_client.cache_path_for_source(source_path)
        file_size = len(content)
        
        # Check if text-like (can be indexed directly) or needs docling
        try:
            text_content = content.decode('utf-8', errors='strict')
            # Text file - index directly
            markdown_content = text_content
        except UnicodeDecodeError:
            # Binary document (PDF, DOC, etc.) - needs docling
            markdown_content = self._extract_with_docling(
                source_path=source_path,
                temp_path=extracted_path,
                cache_path=cache_path,
                dry_run=dry_run,
            )
            if markdown_content is None:
                self.progress.mark_extraction_failed()
                return
        
        # Index the markdown
        if dry_run:
            logger.info(f"[{_short_path(source_path)}] [DRY RUN] Would index archive doc")
            self.progress.mark_indexed()
            return
        
        if not self.indexer:
            return
        
        try:
            result = self.indexer.index_document(
                source_path=source_path,
                content=markdown_content,
                cache_path=cache_path,
                file_size=file_size,
                original_format=Path(source_path).suffix,
                is_source_code=False,
            )
            
            if result.status == "indexed":
                self.progress.mark_indexed()
                logger.info(f"[{_short_path(source_path)}] Indexed to {result.collection}")
            elif result.status == "skipped":
                self.progress.mark_skipped_indexed()
            else:
                self.progress.mark_index_failed()
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Indexing error: {e}")
            self.progress.mark_index_failed()
    
    def _get_or_create_markdown(
        self,
        source_path: str,
        content: bytes,
        blob: storage.Blob,
        cache_path: str,
        dry_run: bool = False,
    ) -> Optional[str]:
        """Get markdown content from cache or create via processing.
        
        Args:
            source_path: GCS source path.
            content: File content bytes.
            blob: GCS Blob object.
            cache_path: GCS cache path for markdown.
            dry_run: If True, don't write to GCS cache.
        
        Returns:
            Markdown content string, or None on failure.
        """
        # Check GCS cache first
        if not self.config.force and self.gcs_client.cache_exists_by_path(cache_path):
            try:
                cached_content = self.gcs_client.read_cached_markdown(cache_path)
                logger.debug(f"[{_short_path(source_path)}] Using cached markdown")
                return cached_content
            except Exception as e:
                logger.warning(f"[{_short_path(source_path)}] Failed to read cache: {e}")
        
        # Check if text file (no docling needed)
        try:
            text_content = content.decode('utf-8', errors='strict')
            # Plain text - return directly (and cache)
            if not dry_run:
                try:
                    self.gcs_client.upload_markdown(cache_path, text_content)
                except Exception as e:
                    logger.warning(f"[{_short_path(source_path)}] Failed to cache text: {e}")
            return text_content
        except UnicodeDecodeError:
            pass  # Binary document, needs docling
        
        # Binary document - needs docling extraction
        temp_path = None
        try:
            temp_path = self.gcs_client.download_blob_to_temp(blob)
            markdown = self._extract_with_docling(
                source_path=source_path,
                temp_path=temp_path,
                cache_path=cache_path,
                dry_run=dry_run,
            )
            return markdown
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
    
    def _extract_with_docling(
        self,
        source_path: str,
        temp_path: Path,
        cache_path: str,
        dry_run: bool = False,
    ) -> Optional[str]:
        """Extract markdown from binary document using docling.
        
        Args:
            source_path: GCS source path.
            temp_path: Path to temp file.
            cache_path: GCS cache path.
            dry_run: If True, don't write to GCS cache.
        
        Returns:
            Markdown content, or None on failure.
        """
        try:
            start_time = time.time()
            markdown = self.extractor.extract_to_markdown(temp_path)
            elapsed = time.time() - start_time
            logger.debug(f"[{_short_path(source_path)}] Docling extraction took {elapsed:.1f}s")
            
            # Cache the result
            if not dry_run:
                try:
                    self.gcs_client.upload_markdown(cache_path, markdown)
                except Exception as e:
                    logger.warning(f"[{_short_path(source_path)}] Failed to cache markdown: {e}")
            
            return markdown
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Docling extraction failed: {e}")
            self._log_error(source_path, f"Docling extraction failed: {e}")
            return None
    
    def _log_error(self, source_path: str, error: str) -> None:
        """Log error to file."""
        if not self._error_file:
            return
        
        timestamp = datetime.now().isoformat()
        log_line = f"{timestamp} | FAILED | {source_path} | {error}\n"
        
        try:
            with open(self._error_file, "a") as f:
                f.write(log_line)
        except Exception as e:
            logger.warning(f"Failed to write to error log: {e}")
