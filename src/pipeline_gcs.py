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
from .chunker import chunk_document, chunk_source_code, chunk_text_fallback
from .extractor import Extractor, create_extractor
from .file_classifier import (
    ClassificationResult,
    FileCategory,
    classify_file,
    detect_image_mime,
    is_indexable_category,
    is_source_category,
)
from .gcs_client import GCSClient
from .qdrant_client import QdrantIndexer, get_content_hash
from .types import Config
from .url_resolver import resolve_source_url

logger = logging.getLogger(__name__)


def _short_path(path: str) -> str:
    """Shorten a path for display by removing the 'source/' prefix."""
    if path.startswith('source/'):
        return path[7:]
    return path


def _get_rss_gb() -> float:
    """Return current process RSS in GB, or 0.0 on error."""
    try:
        return psutil.Process(os.getpid()).memory_info().rss / (1024 ** 3)
    except Exception:
        return 0.0


def _log_memory_usage() -> None:
    """Log current memory usage at INFO level."""
    try:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem_percent = process.memory_percent()
        logger.info(
            f"Memory usage: {mem_info.rss / 1024 / 1024:.1f} MB "
            f"({mem_percent:.1f}% of system)"
        )
    except Exception:
        pass  # psutil not available or error


def _force_malloc_trim() -> None:
    """Ask glibc to return freed native memory to the OS.

    Python's pymalloc and C libraries (ONNX, httpx, libpdfium) allocate
    through glibc malloc.  By default glibc hoards freed pages for reuse,
    which shows up as ever-growing RSS even when Python objects have been
    collected.  ``malloc_trim(0)`` forces glibc to release as many pages
    as it can via ``madvise(MADV_DONTNEED)``.
    """
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except Exception:
        pass


def _rss_mb() -> int:
    """Return current process RSS in MB."""
    try:
        return psutil.Process(os.getpid()).memory_info().rss // (1024 * 1024)
    except Exception:
        return 0


class _RSSMonitor:
    """Background thread that logs RSS every N seconds.
    
    Helps pinpoint exactly WHEN memory explosions occur,
    even during blocking operations (semaphore waits, proc.wait(), etc.).
    """
    
    def __init__(self, interval: float = 5.0):
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._last_mb = 0
    
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="rss-monitor")
        self._thread.start()
    
    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
    
    @staticmethod
    def _top_anon_maps(n: int = 5) -> str:
        """Return the top N anonymous memory regions by RSS from /proc/self/smaps."""
        try:
            regions = []
            with open("/proc/self/smaps") as f:
                addr = ""
                rss_kb = 0
                anon_kb = 0
                for line in f:
                    if line[0] != ' ' and '-' in line[:20]:
                        if addr and rss_kb > 102400:  # > 100 MB
                            regions.append((rss_kb, anon_kb, addr.strip()))
                        parts = line.split()
                        addr = parts[0]
                        rss_kb = 0
                        anon_kb = 0
                    elif line.startswith("Rss:"):
                        rss_kb = int(line.split()[1])
                    elif line.startswith("Anonymous:"):
                        anon_kb = int(line.split()[1])
            if addr and rss_kb > 102400:
                regions.append((rss_kb, anon_kb, addr.strip()))
            regions.sort(reverse=True)
            parts = []
            for rss_kb, anon_kb, addr in regions[:n]:
                parts.append(f"{addr} RSS={rss_kb//1024}MB anon={anon_kb//1024}MB")
            return "; ".join(parts) if parts else "no large regions"
        except Exception as e:
            return f"error: {e}"

    def _run(self):
        snapshot_taken = False
        while not self._stop.is_set():
            mb = _rss_mb()
            delta = mb - self._last_mb if self._last_mb else 0
            # Log every time if RSS > 5 GB, or if delta > 500 MB
            if mb > 5000 or abs(delta) > 500:
                # Get active thread names for context
                active = [t.name for t in threading.enumerate()
                          if t.is_alive() and t.name not in ("MainThread", "rss-monitor")]
                logger.warning(
                    f"RSS monitor: {mb} MB (delta={delta:+d} MB), "
                    f"threads={len(active)}: {', '.join(active[:8])}"
                )
                # Dump top anon maps when RSS crosses 10 GB for the first time,
                # then again every 20 GB to see growth pattern
                if mb > 10000 and (not snapshot_taken or delta > 20000):
                    maps_info = self._top_anon_maps(10)
                    logger.warning(f"RSS top anon maps: {maps_info}")
                    snapshot_taken = True
            self._last_mb = mb
            self._stop.wait(self._interval)


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
    skipped_skip_list: int = 0  # In skip list file
    skipped_binary: int = 0   # Binary files
    skipped_small_image: int = 0  # Images below min_image_docling_size
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
    
    def mark_skipped_skip_list(self) -> None:
        """Mark a file as skipped (in skip list file)."""
        with self._lock:
            self.skipped_skip_list += 1
    
    def mark_skipped_binary(self) -> None:
        """Mark a file as skipped (binary/unprocessable)."""
        with self._lock:
            self.skipped_binary += 1
    
    def mark_skipped_small_image(self) -> None:
        """Mark a file as skipped (image below min_image_docling_size)."""
        with self._lock:
            self.skipped_small_image += 1
    
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
                f"skip_list={self.skipped_skip_list} "
                f"skip_binary={self.skipped_binary} "
                f"skip_small_img={self.skipped_small_image} "
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
                f"Skipped (skip list):      {self.skipped_skip_list}\n"
                f"Skipped (binary/other):   {self.skipped_binary}\n"
                f"Skipped (small images):   {self.skipped_small_image}\n"
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
        skip_list_path: Optional[Path] = None,
    ) -> None:
        """Initialize GCS pipeline.
        
        Args:
            config: Pipeline configuration.
            gcs_client: GCS client (created from config if not provided).
            extractor: Document extractor (created if not provided).
            indexer: Qdrant indexer (created if not provided).
            skip_list_path: Optional path to a skip-list file. If provided,
                paths listed in the file are skipped and newly completed
                paths are appended for crash-safe resume.
        """
        self.config = config
        
        # Initialize GCS client
        self.gcs_client = gcs_client or GCSClient(
            source_bucket=config.source_bucket,
            cache_bucket=config.cache_bucket,
            source_prefix=config.source_prefix,
            cache_prefix=config.cache_prefix,
        )
        
        # Initialize extractor (for docling conversion — runs in subprocesses)
        self.extractor = extractor or create_extractor(
            max_pages=config.max_pages,
            do_ocr=False,
            recycle_after=getattr(config, 'docling_recycle_after', 50),
            max_concurrent=getattr(config, 'max_concurrent_docling', 2),
            conversion_timeout=getattr(config, 'docling_conversion_timeout', 600),
        )
        
        # Initialize Qdrant indexer
        self._indexer = indexer
        
        # Progress tracking
        self.progress = ProgressTracker()
        
        # Semaphore to limit concurrent large-file processing (prevents OOM
        # when multiple >32 MB files are downloaded & chunked simultaneously).
        self._large_file_sem = threading.Semaphore(
            config.max_concurrent_large if hasattr(config, 'max_concurrent_large') else 1
        )
        
        # Error log file
        self._error_file: Optional[Path] = None
        if config.error_file:
            self._error_file = Path(config.error_file)
        
        # Skip list: paths to skip (already processed in a prior run) and
        # file handle for appending newly completed paths.
        self._skip_set: set[str] = set()
        self._skip_list_path = skip_list_path
        self._skip_list_lock = threading.Lock()
        self._skip_list_fh = None
        if skip_list_path:
            self._load_skip_list(skip_list_path)
    
    # -----------------------------------------------------------------
    # Skip list helpers
    # -----------------------------------------------------------------

    def _load_skip_list(self, path: Path) -> None:
        """Load skip list from file and open it for appending."""
        if path.exists():
            with open(path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        self._skip_set.add(line)
            logger.info(f"Loaded {len(self._skip_set)} paths from skip list: {path}")
        else:
            logger.info(f"Skip list file does not exist yet, will create: {path}")
        # Open for append so every completed path is persisted immediately
        self._skip_list_fh = open(path, 'a')

    def _in_skip_list(self, source_path: str) -> bool:
        """Check whether *source_path* (prefix-stripped) is in the skip set."""
        return _short_path(source_path) in self._skip_set

    def _skip_fast(self, source_path: str) -> bool:
        """Cheap pre-filter: skip directories, index.json, and skip-list items.

        Returns True if the blob should be skipped entirely (no thread
        dispatch, no gc.collect, no Qdrant call).
        """
        if source_path.endswith('/'):
            return True
        if source_path.endswith('index.json'):
            return True
        if self._in_skip_list(source_path):
            self.progress.mark_skipped_skip_list()
            self.progress.mark_processed()
            return True
        return False

    def _record_completed(self, source_path: str) -> None:
        """Record a successfully processed path in the skip list.

        Thread-safe; flushes to disk immediately for crash safety.
        """
        short = _short_path(source_path)
        self._skip_set.add(short)
        if self._skip_list_fh is not None:
            with self._skip_list_lock:
                self._skip_list_fh.write(short + '\n')
                self._skip_list_fh.flush()

    def _close_skip_list(self) -> None:
        """Flush and close the skip list file handle."""
        if self._skip_list_fh is not None:
            self._skip_list_fh.close()
            self._skip_list_fh = None

    def _is_small_image(self, content: bytes, source_path: str) -> bool:
        """Return True if *content* is an image file below the docling size threshold.

        Tiny images (icons, sprites, UI theme assets) waste significant memory
        when run through docling OCR and almost never produce useful chunks.
        """
        min_size = getattr(self.config, 'min_image_docling_size', 50 * 1024)
        if len(content) >= min_size:
            return False
        mime = detect_image_mime(content[:8192], source_path)
        return mime is not None

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
            logger.info(
                f"Workers: {self.config.workers}, max_pending: {self.config.max_pending}, "
                f"max_concurrent_large: {getattr(self.config, 'max_concurrent_large', 1)}, "
                f"max_concurrent_docling: {getattr(self.config, 'max_concurrent_docling', 4)}, "
                f"max_source_size: {getattr(self.config, 'max_source_size', 10*1024*1024) / 1024 / 1024:.0f} MB, "
                f"docling_recycle_after: {getattr(self.config, 'docling_recycle_after', 10)}, "
                f"min_image_docling_size: {getattr(self.config, 'min_image_docling_size', 50*1024) / 1024:.0f} KB"
            )
            _log_memory_usage()
            
            # Start background RSS monitor for diagnostics
            rss_monitor = _RSSMonitor(interval=5.0)
            rss_monitor.start()
            
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
        finally:
            self._close_skip_list()
    
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
            # Fast skip-list pre-filter (avoids thread dispatch + gc.collect overhead)
            if self._skip_fast(blob.name):
                continue

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
                    # Fast skip-list pre-filter (avoids thread dispatch + gc overhead)
                    if self._skip_fast(blob.name):
                        continue
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
                        
                        # Run garbage collection + malloc_trim periodically
                        if done_count % 50 == 0:
                            gc.collect()
                            _force_malloc_trim()
                            _log_memory_usage()
                    
                    logger.debug(f"Tasks completed: {done_count}, pending: {len(pending)}")
                    
                    # Submit more tasks up to max_pending
                    for blob in blob_iter:
                        # Fast skip-list pre-filter
                        if self._skip_fast(blob.name):
                            continue
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
        
        # Skip list check (safety net — normally filtered by _skip_fast before dispatch)
        if self._in_skip_list(source_path):
            logger.debug(f"[{_short_path(source_path)}] Skipped (in skip list)")
            self.progress.mark_skipped_skip_list()
            self.progress.mark_processed()
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
        # concurrently. Chunking via index_chunks handles the Qdrant payload
        # size limit. Classification only needs the first few KB.
        MAX_INMEMORY_BYTES = 32 * 1024 * 1024  # 32MB
        file_size = blob.size or 0
        
        if file_size > MAX_INMEMORY_BYTES:
            logger.info(
                f"[{_short_path(source_path)}] Large file "
                f"({file_size / 1024 / 1024:.1f} MB), using temp file"
            )
            # Acquire semaphore to limit concurrent large-file memory usage
            self._large_file_sem.acquire()
            try:
                self._process_large_blob(blob, source_path, file_size, dry_run=dry_run)
            finally:
                self._large_file_sem.release()
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
            if self._is_archive_mime(content, source_path):
                self._process_archive(blob, content, source_path, dry_run=dry_run)
            else:
                logger.info(f"[{_short_path(source_path)}] Skipped ({classification.reason})")
                self.progress.mark_skipped_binary()
                self._record_completed(source_path)
            self.progress.mark_processed()
            return
        
        if not is_indexable_category(classification.category):
            logger.info(f"[{_short_path(source_path)}] Skipped ({classification.reason})")
            self.progress.mark_skipped_binary()
            self._record_completed(source_path)
            self.progress.mark_processed()
            return
        
        # Process the file based on category
        if is_source_category(classification.category):
            # Guard: skip source files larger than max_source_size.
            # Giant .zwr / data-dump files cause extreme memory amplification
            # during chunking/embedding (30 MB text → thousands of chunks →
            # batch embed can use 10+ GB).
            max_src = getattr(self.config, 'max_source_size', 10 * 1024 * 1024)
            file_size = blob.size or len(content)
            if file_size > max_src:
                logger.info(
                    f"[{_short_path(source_path)}] Skipped — source file "
                    f"({file_size / 1024 / 1024:.1f} MB) exceeds max_source_size "
                    f"({max_src / 1024 / 1024:.0f} MB)"
                )
                self.progress.mark_skipped_binary()
                self._record_completed(source_path)
                del content
                self.progress.mark_processed()
                return

            # Source code - index directly without docling
            self._process_source_file(
                source_path=source_path,
                content=content,
                classification=classification,
                file_size=file_size,
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
        content from disk for indexing. Chunking via index_chunks
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
                if self._is_archive_mime(header, source_path):
                    # Re-read full content for archive processing
                    with open(temp_path, 'rb') as f:
                        content = f.read()
                    self._process_archive(blob, content, source_path, dry_run=dry_run)
                    del content
                else:
                    logger.info(f"[{_short_path(source_path)}] Skipped ({classification.reason})")
                    self.progress.mark_skipped_binary()
                    self._record_completed(source_path)
                return
            
            if not is_indexable_category(classification.category):
                logger.info(f"[{_short_path(source_path)}] Skipped ({classification.reason})")
                self.progress.mark_skipped_binary()
                self._record_completed(source_path)
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
                # Guard: skip source files larger than max_source_size.
                # Giant .zwr / data-dump files are not useful code for RAG and
                # cause extreme memory amplification during chunking.
                max_src = getattr(self.config, 'max_source_size', 10 * 1024 * 1024)
                if file_size > max_src:
                    logger.info(
                        f"[{_short_path(source_path)}] Skipped — source file "
                        f"({file_size / 1024 / 1024:.1f} MB) exceeds max_source_size "
                        f"({max_src / 1024 / 1024:.0f} MB)"
                    )
                    self.progress.mark_skipped_binary()
                    self._record_completed(source_path)
                    del text_content
                    gc.collect()
                    return
                # Pass text directly to avoid encode→decode round-trip that
                # triples memory for large files.
                self._process_source_file_text(
                    source_path=source_path,
                    text_content=text_content,
                    classification=classification,
                    file_size=file_size,
                    dry_run=dry_run,
                )
                del text_content
                gc.collect()
                return
            else:
                # Large doc file — extract DoclingDocument via temp path, chunk, index
                cache_path = self.gcs_client.cache_path_for_source(source_path)
                
                if not self.indexer:
                    return
                
                collection_name = self.indexer.router.route_with_category(source_path, is_source_code=False)
                
                # Try to extract DoclingDocument
                doc = None
                markdown_content = None
                try:
                    # For large files the content is already on disk as text_content
                    # but may still be a binary doc that we read as text (lossy)
                    # Check if it's UTF-8 clean  
                    with open(temp_path, 'rb') as f:
                        raw = f.read(512)
                    try:
                        raw.decode('utf-8', errors='strict')
                        # Text doc — use text_content directly
                        markdown_content = text_content
                    except UnicodeDecodeError:
                        pass
                except Exception:
                    pass
                
                if markdown_content is None:
                    # Skip tiny images from docling — they waste memory.
                    # (Unlikely for large blobs, but defensive.)
                    min_img_size = getattr(self.config, 'min_image_docling_size', 50 * 1024)
                    if file_size < min_img_size:
                        with open(temp_path, 'rb') as f:
                            header_bytes = f.read(8192)
                        if detect_image_mime(header_bytes, source_path) is not None:
                            logger.debug(
                                f"[{_short_path(source_path)}] Skipped — image too small for docling OCR"
                            )
                            self.progress.mark_skipped_small_image()
                            self._record_completed(source_path)
                            del text_content
                            gc.collect()
                            return

                    # Binary doc — extract with docling
                    try:
                        start_time = time.time()
                        doc = self.extractor.extract_to_document(temp_path)
                        elapsed = time.time() - start_time
                        logger.debug(f"[{_short_path(source_path)}] Docling extraction took {elapsed:.1f}s")
                        # Cache DoclingDocument JSON
                        if not dry_run:
                            try:
                                self.gcs_client.upload_docling_json(source_path, doc.model_dump_json())
                            except Exception as e:
                                logger.warning(f"[{_short_path(source_path)}] Failed to cache DoclingDocument: {e}")
                        # Get markdown
                        markdown_content = doc.export_to_markdown()
                        # Cache markdown
                        if not dry_run:
                            try:
                                self.gcs_client.upload_markdown(cache_path, markdown_content)
                            except Exception as e:
                                logger.warning(f"[{_short_path(source_path)}] Failed to cache markdown: {e}")
                    except Exception as e:
                        logger.error(f"[{_short_path(source_path)}] Docling extraction failed: {e}")
                        self._log_error(source_path, f"Docling extraction failed: {e}")
                        self.progress.mark_extraction_failed()
                        return
                
                # Resolve URL, hash, chunk, index
                source_url = resolve_source_url(source_path, content=markdown_content)
                content_hash = get_content_hash(markdown_content)
                
                try:
                    if doc is not None:
                        chunks = chunk_document(
                            doc=doc,
                            source_path=source_path,
                            source_url=source_url,
                            content_hash=content_hash,
                        )
                    else:
                        chunks = chunk_text_fallback(
                            text=markdown_content,
                            source_path=source_path,
                            source_url=source_url,
                            content_hash=content_hash,
                        )
                except Exception as e:
                    logger.error(f"[{_short_path(source_path)}] Chunking failed: {e}")
                    self.progress.mark_extraction_failed()
                    return
                
                try:
                    result = self.indexer.index_chunks(
                        chunks=chunks,
                        collection_name=collection_name,
                        force=self.config.force,
                        dry_run=dry_run,
                        file_size=file_size,
                        original_format=Path(source_path).suffix,
                        cache_path=cache_path,
                    )
                    if result.status == "indexed":
                        self.progress.mark_indexed()
                        logger.info(f"[{_short_path(source_path)}] Indexed to {result.collection} ({len(chunks)} chunks)")
                        self._record_completed(source_path)
                    elif result.status == "skipped":
                        self.progress.mark_skipped_indexed()
                        self._record_completed(source_path)
                    else:
                        self.progress.mark_index_failed()
                        logger.warning(f"[{_short_path(source_path)}] Index failed: {result.error}")
                except Exception as e:
                    logger.error(f"[{_short_path(source_path)}] Indexing error: {e}")
                    self.progress.mark_index_failed()
                finally:
                    # Release large object references to help GC
                    del chunks, markdown_content
                    if doc is not None:
                        del doc
                    gc.collect()
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Large file processing failed: {e}")
            self._log_error(source_path, f"Large file processing failed: {e}")
            self.progress.mark_extraction_failed()
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    # Extensions that use ZIP container format but should NOT be
    # extracted as archives (Java archives, Android packages, etc.).
    _SKIP_ARCHIVE_EXTENSIONS = frozenset({
        '.jar', '.war', '.ear',  # Java archives
        '.apk', '.aab',          # Android packages
        '.whl', '.egg',          # Python packages
    })

    def _is_archive_mime(self, content: bytes, source_path: str = "") -> bool:
        """Check if content is an archive by magic bytes.

        Returns False for ZIP-based containers that should be skipped
        (JAR, WAR, APK, etc.) even though they share the ZIP magic bytes.
        """
        # ZIP magic: PK\x03\x04
        if content.startswith(b'PK\x03\x04'):
            # Exclude Office documents (DOCX, XLSX, PPTX)
            if b'word/' in content[:2000] or b'xl/' in content[:2000] or b'ppt/' in content[:2000]:
                return False
            # Exclude non-archive ZIP containers (.jar, .war, .apk, etc.)
            if source_path:
                ext = source_path[source_path.rfind('.'):].lower() if '.' in source_path else ''
                if ext in self._SKIP_ARCHIVE_EXTENSIONS:
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
        
        Applies memory-safety guards to prevent OOM when an archive contains
        thousands of files (e.g. GOLD.zip with 5765 DESCRIBE TYPE*.json):
        
        - ``max_archive_members`` logs a warning when an archive exceeds this
          many files (all files are still processed).
        - ``max_source_size`` is enforced for source-code members.
        - ``gc.collect()`` runs every 50 members to reclaim native memory.
        - Each extracted temp file is deleted immediately after processing.
        - The in-memory archive bytes are written to temp instead of
          re-downloading from GCS.
        
        Args:
            blob: GCS Blob of the archive.
            content: Archive content bytes.
            archive_path: GCS path of the archive.
            dry_run: If True, don't write to Qdrant or GCS cache.
        """
        logger.info(f"[{_short_path(archive_path)}] Processing archive")
        
        max_members = getattr(self.config, 'max_archive_members', 2000)
        max_src = getattr(self.config, 'max_source_size', 10 * 1024 * 1024)
        
        # Write archive bytes to a temp file from memory (avoids redundant
        # GCS download — ``content`` is already in RAM).
        temp_path = None
        extracted_count = 0
        member_count = 0
        
        try:
            import tempfile as _tempfile
            suffix = None
            if "." in archive_path:
                suffix = "." + archive_path.rsplit(".", 1)[-1]
            fd = _tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="archive_")
            temp_path = Path(fd.name)
            fd.write(content)
            fd.close()

            # Release the in-memory archive bytes now that they're on disk.
            del content
            
            with ArchiveExtractor() as extractor:
                for rel_path, extracted_path, file_content in extractor.extract_all_files(temp_path):
                    member_count += 1

                    # --- warn if archive is unusually large ---
                    if member_count == max_members:
                        logger.warning(
                            f"[{_short_path(archive_path)}] Large archive: "
                            f"reached {max_members} members (max_archive_members "
                            f"threshold) — continuing to process all files"
                        )

                    # --- periodic memory management (every 10 members) ---
                    if member_count % 10 == 0:
                        gc.collect()
                        _force_malloc_trim()
                        rss_gb = _get_rss_gb()
                        logger.info(
                            f"[{_short_path(archive_path)}] Archive progress: "
                            f"{member_count} members, {extracted_count} indexed, "
                            f"RSS={rss_gb:.1f} GB"
                        )
                        # Circuit breaker: abort archive if RSS exceeds ceiling
                        max_rss = getattr(self.config, 'max_rss_gb', 0)
                        if max_rss > 0 and rss_gb > max_rss:
                            logger.critical(
                                f"[{_short_path(archive_path)}] RSS {rss_gb:.1f} GB "
                                f"exceeds max_rss_gb={max_rss} GB — aborting archive "
                                f"to prevent OOM (processed {extracted_count}/{member_count} members)"
                            )
                            self._cleanup_extracted_file(extracted_path)
                            break

                    # Construct full source path: archive_name/relative_path
                    full_source_path = f"{archive_path}/{rel_path}"
                    
                    # Skip list check (before Qdrant)
                    if self._in_skip_list(full_source_path):
                        logger.debug(f"[{_short_path(full_source_path)}] Skipped (in skip list)")
                        self.progress.mark_skipped_skip_list()
                        self._cleanup_extracted_file(extracted_path)
                        continue
                    
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
                            self._cleanup_extracted_file(extracted_path)
                            continue
                    
                    # Classify the extracted file
                    classification = classify_file(rel_path, file_content)
                    
                    if not is_indexable_category(classification.category):
                        logger.debug(f"[{_short_path(full_source_path)}] Skipped (non-indexable)")
                        self._cleanup_extracted_file(extracted_path)
                        continue
                    
                    # Process based on category
                    if is_source_category(classification.category):
                        # Guard: skip source files exceeding max_source_size (same
                        # guard as _process_large_blob, prevents data-dump memory
                        # amplification during chunking/embedding).
                        file_size = len(file_content)
                        if file_size > max_src:
                            logger.info(
                                f"[{_short_path(full_source_path)}] Skipped — archive source "
                                f"({file_size / 1024 / 1024:.1f} MB) exceeds "
                                f"max_source_size ({max_src / 1024 / 1024:.0f} MB)"
                            )
                            self.progress.mark_skipped_binary()
                            self._record_completed(full_source_path)
                            self._cleanup_extracted_file(extracted_path)
                            continue

                        self._process_source_file(
                            source_path=full_source_path,
                            content=file_content,
                            classification=classification,
                            file_size=file_size,
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

                    # --- incremental cleanup: remove temp file immediately ---
                    self._cleanup_extracted_file(extracted_path)
            
            self.progress.mark_archive_processed(extracted_count)
            logger.info(f"[{_short_path(archive_path)}] Archive complete: {extracted_count} files")
            
        except Exception as e:
            logger.error(f"[{_short_path(archive_path)}] Archive extraction failed: {e}")
            self._log_error(archive_path, f"Archive extraction failed: {e}")
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _cleanup_extracted_file(path: Path) -> None:
        """Remove an extracted temp file if it still exists."""
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
    
    def _process_source_file(
        self,
        source_path: str,
        content: bytes,
        classification: ClassificationResult,
        file_size: int,
        dry_run: bool = False,
    ) -> None:
        """Process a source code file (direct indexing without docling).
        
        Uses code-aware chunking (MUMPS label boundaries) and the new
        index_chunks() API.
        
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
        
        if not self.indexer:
            logger.warning(f"[{_short_path(source_path)}] No indexer available")
            return
        
        # Resolve source URL (pass content for httrack comment detection)
        source_url = resolve_source_url(source_path, content=text_content)
        content_hash = get_content_hash(text_content)
        
        # Determine collection
        collection_name = self.indexer.router.route_with_category(source_path, is_source_code=True)
        
        # Chunk using code-aware chunker
        try:
            chunks = chunk_source_code(
                text=text_content,
                source_path=source_path,
                source_url=source_url,
                content_hash=content_hash,
            )
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Chunking failed: {e}")
            self.progress.mark_extraction_failed()
            return
        
        logger.debug(
            f"[{_short_path(source_path)}] Chunked into {len(chunks)} chunks "
            f"(chunker={chunks[0].metadata.get('chunker', '?') if chunks else '?'})"
        )
        
        # Index chunks
        try:
            result = self.indexer.index_chunks(
                chunks=chunks,
                collection_name=collection_name,
                force=self.config.force,
                dry_run=dry_run,
                file_size=file_size,
                original_format=Path(source_path).suffix,
                cache_path="",  # No cache for source files
            )
            
            if result.status == "indexed":
                self.progress.mark_indexed()
                logger.info(f"[{_short_path(source_path)}] Indexed to {result.collection} ({len(chunks)} chunks)")
                self._record_completed(source_path)
            elif result.status == "skipped":
                self.progress.mark_skipped_indexed()
                self._record_completed(source_path)
            else:
                self.progress.mark_index_failed()
                logger.warning(f"[{_short_path(source_path)}] Index failed: {result.error}")
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Indexing error: {e}")
            self.progress.mark_index_failed()
    
    def _process_source_file_text(
        self,
        source_path: str,
        text_content: str,
        classification: ClassificationResult,
        file_size: int,
        dry_run: bool = False,
    ) -> None:
        """Process a source code file from already-decoded text.

        Same as _process_source_file but accepts a str instead of bytes
        to avoid an encode→decode round-trip that triples peak memory for
        large files coming through _process_large_blob.

        Args:
            source_path: GCS source path.
            text_content: Already-decoded UTF-8 text content.
            classification: Classification result.
            file_size: File size in bytes.
            dry_run: If True, don't write to Qdrant.
        """
        if not self.indexer:
            logger.warning(f"[{_short_path(source_path)}] No indexer available")
            return

        # Resolve source URL (pass content for httrack comment detection)
        source_url = resolve_source_url(source_path, content=text_content)
        content_hash = get_content_hash(text_content)

        # Determine collection
        collection_name = self.indexer.router.route_with_category(source_path, is_source_code=True)

        # Chunk using code-aware chunker
        try:
            chunks = chunk_source_code(
                text=text_content,
                source_path=source_path,
                source_url=source_url,
                content_hash=content_hash,
            )
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Chunking failed: {e}")
            self.progress.mark_extraction_failed()
            return

        logger.debug(
            f"[{_short_path(source_path)}] Chunked into {len(chunks)} chunks "
            f"(chunker={chunks[0].metadata.get('chunker', '?') if chunks else '?'})"
        )

        # Index chunks
        try:
            result = self.indexer.index_chunks(
                chunks=chunks,
                collection_name=collection_name,
                force=self.config.force,
                dry_run=dry_run,
                file_size=file_size,
                original_format=Path(source_path).suffix,
                cache_path="",  # No cache for source files
            )

            if result.status == "indexed":
                self.progress.mark_indexed()
                logger.info(f"[{_short_path(source_path)}] Indexed to {result.collection} ({len(chunks)} chunks)")
                self._record_completed(source_path)
            elif result.status == "skipped":
                self.progress.mark_skipped_indexed()
                self._record_completed(source_path)
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
        
        New flow:
        1. Try to load cached DoclingDocument JSON, else extract via docling
        2. Cache DoclingDocument JSON and markdown to GCS
        3. Resolve source URL
        4. Chunk via HybridChunker (falls back to text-fallback)
        5. Index chunks via index_chunks()
        
        Args:
            blob: GCS Blob object.
            source_path: GCS source path.
            content: File content bytes.
            classification: Classification result.
            dry_run: If True, don't write to Qdrant or GCS cache.
        """
        cache_path = self.gcs_client.cache_path_for_source(source_path)
        file_size = blob.size or len(content)
        
        if not self.indexer:
            return
        
        # Determine collection
        collection_name = self.indexer.router.route_with_category(source_path, is_source_code=False)
        
        # --- Step 1: Obtain DoclingDocument or markdown text ---
        doc = None  # DoclingDocument if available
        markdown_content = None
        
        # Check for cached DoclingDocument JSON first
        if not self.config.force:
            try:
                cached_json = self.gcs_client.download_docling_json(source_path)
                if cached_json:
                    from docling_core.types import DoclingDocument
                    doc = DoclingDocument.model_validate_json(cached_json)
                    logger.debug(f"[{_short_path(source_path)}] Using cached DoclingDocument")
            except Exception as e:
                logger.debug(f"[{_short_path(source_path)}] DoclingDocument cache miss: {e}")
        
        if doc is None:
            # Try text decode first (plain text / HTML doesn't need docling)
            try:
                text_content = content.decode('utf-8', errors='strict')
                # Plain text / HTML text → use text fallback path
                markdown_content = text_content
                # Cache markdown
                if not dry_run:
                    try:
                        self.gcs_client.upload_markdown(cache_path, markdown_content)
                    except Exception as e:
                        logger.warning(f"[{_short_path(source_path)}] Failed to cache text: {e}")
            except UnicodeDecodeError:
                # Skip tiny images from docling — they waste memory and produce
                # no useful text (icons, sprites, UI theme assets).
                if self._is_small_image(content, source_path):
                    logger.debug(
                        f"[{_short_path(source_path)}] Skipped — image too small for docling OCR "
                        f"({len(content)} bytes < {getattr(self.config, 'min_image_docling_size', 50*1024)} bytes)"
                    )
                    self.progress.mark_skipped_small_image()
                    self._record_completed(source_path)
                    return

                # Binary document → extract via docling
                rss_before_extract = _rss_mb()
                doc = self._extract_docling_document(
                    source_path=source_path,
                    content=content,
                    blob=blob,
                    cache_path=cache_path,
                    dry_run=dry_run,
                )
                rss_after_extract = _rss_mb()
                if rss_after_extract - rss_before_extract > 500:
                    logger.warning(
                        f"[{_short_path(source_path)}] _extract_docling_document RSS: "
                        f"{rss_before_extract}→{rss_after_extract} MB (+{rss_after_extract-rss_before_extract} MB)"
                    )
                if doc is None:
                    self.progress.mark_extraction_failed()
                    return
        
        # --- Step 2: Resolve source URL ---
        source_url = resolve_source_url(source_path, content=markdown_content)
        
        # --- Step 3: Chunk ---
        rss_pre_chunk = _rss_mb()
        if markdown_content is not None:
            content_hash = get_content_hash(markdown_content)
        elif doc is not None:
            # Use the markdown export for content hash
            markdown_content = doc.export_to_markdown()
            rss_after_md = _rss_mb()
            if rss_after_md - rss_pre_chunk > 200:
                logger.warning(
                    f"[{_short_path(source_path)}] export_to_markdown RSS: "
                    f"{rss_pre_chunk}→{rss_after_md} MB (+{rss_after_md-rss_pre_chunk} MB)"
                )
            content_hash = get_content_hash(markdown_content)
            # Also cache the markdown
            if not dry_run:
                try:
                    self.gcs_client.upload_markdown(cache_path, markdown_content)
                except Exception as e:
                    logger.warning(f"[{_short_path(source_path)}] Failed to cache markdown: {e}")
        else:
            self.progress.mark_extraction_failed()
            return
        
        try:
            if doc is not None:
                chunks = chunk_document(
                    doc=doc,
                    source_path=source_path,
                    source_url=source_url,
                    content_hash=content_hash,
                )
            else:
                chunks = chunk_text_fallback(
                    text=markdown_content,
                    source_path=source_path,
                    source_url=source_url,
                    content_hash=content_hash,
                )
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Chunking failed: {e}")
            self.progress.mark_extraction_failed()
            return
        
        rss_post_chunk = _rss_mb()
        logger.debug(
            f"[{_short_path(source_path)}] Chunked into {len(chunks)} chunks "
            f"(chunker={chunks[0].metadata.get('chunker', '?') if chunks else '?'})"
        )
        if rss_post_chunk - rss_pre_chunk > 200:
            logger.warning(
                f"[{_short_path(source_path)}] chunk RSS: "
                f"{rss_pre_chunk}→{rss_post_chunk} MB (+{rss_post_chunk-rss_pre_chunk} MB, "
                f"{len(chunks)} chunks)"
            )
        
        # --- Step 4: Index ---
        try:
            result = self.indexer.index_chunks(
                chunks=chunks,
                collection_name=collection_name,
                force=self.config.force,
                dry_run=dry_run,
                file_size=file_size,
                original_format=Path(source_path).suffix,
                cache_path=cache_path,
            )
            
            if result.status == "indexed":
                self.progress.mark_indexed()
                logger.info(f"[{_short_path(source_path)}] Indexed to {result.collection} ({len(chunks)} chunks)")
                self._record_completed(source_path)
            elif result.status == "skipped":
                self.progress.mark_skipped_indexed()
                self._record_completed(source_path)
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
        
        if not self.indexer:
            return
        
        # Determine collection
        collection_name = self.indexer.router.route_with_category(source_path, is_source_code=False)
        
        doc = None
        markdown_content = None
        
        # Check if text-like (can be indexed directly) or needs docling
        try:
            text_content = content.decode('utf-8', errors='strict')
            markdown_content = text_content
        except UnicodeDecodeError:
            # Skip tiny images from docling — they waste memory and produce
            # no useful text (icons, sprites, UI theme assets).
            if self._is_small_image(content, source_path):
                logger.debug(
                    f"[{_short_path(source_path)}] Skipped — image too small for docling OCR "
                    f"({len(content)} bytes < {getattr(self.config, 'min_image_docling_size', 50*1024)} bytes)"
                )
                self.progress.mark_skipped_small_image()
                self._record_completed(source_path)
                return

            # Binary document (PDF, DOC, etc.) - extract DoclingDocument via docling
            try:
                doc = self.extractor.extract_to_document(extracted_path)
                # Cache DoclingDocument JSON
                if not dry_run:
                    try:
                        self.gcs_client.upload_docling_json(source_path, doc.model_dump_json())
                    except Exception as e:
                        logger.warning(f"[{_short_path(source_path)}] Failed to cache DoclingDocument: {e}")
            except Exception as e:
                logger.error(f"[{_short_path(source_path)}] Docling extraction failed: {e}")
                self._log_error(source_path, f"Docling extraction failed: {e}")
                self.progress.mark_extraction_failed()
                return
        
        # Resolve source URL
        source_url = resolve_source_url(source_path, content=markdown_content)
        
        # Compute content hash and get markdown
        if doc is not None:
            markdown_content = doc.export_to_markdown()
            content_hash = get_content_hash(markdown_content)
            # Cache markdown
            if not dry_run:
                try:
                    self.gcs_client.upload_markdown(cache_path, markdown_content)
                except Exception as e:
                    logger.warning(f"[{_short_path(source_path)}] Failed to cache markdown: {e}")
        else:
            content_hash = get_content_hash(markdown_content)
        
        # Chunk
        try:
            if doc is not None:
                chunks = chunk_document(
                    doc=doc,
                    source_path=source_path,
                    source_url=source_url,
                    content_hash=content_hash,
                )
            else:
                chunks = chunk_text_fallback(
                    text=markdown_content,
                    source_path=source_path,
                    source_url=source_url,
                    content_hash=content_hash,
                )
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Chunking failed: {e}")
            self.progress.mark_extraction_failed()
            return
        
        # Index
        try:
            result = self.indexer.index_chunks(
                chunks=chunks,
                collection_name=collection_name,
                force=self.config.force,
                dry_run=dry_run,
                file_size=file_size,
                original_format=Path(source_path).suffix,
                cache_path=cache_path,
            )
            
            if result.status == "indexed":
                self.progress.mark_indexed()
                logger.info(f"[{_short_path(source_path)}] Indexed to {result.collection} ({len(chunks)} chunks)")
                self._record_completed(source_path)
            elif result.status == "skipped":
                self.progress.mark_skipped_indexed()
                self._record_completed(source_path)
            else:
                self.progress.mark_index_failed()
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Indexing error: {e}")
            self.progress.mark_index_failed()
    
    def _extract_docling_document(
        self,
        source_path: str,
        content: bytes,
        blob: storage.Blob,
        cache_path: str,
        dry_run: bool = False,
    ):
        """Extract a DoclingDocument from a binary blob.
        
        Downloads the blob to a temp file, runs docling extraction, caches
        the DoclingDocument JSON and markdown to GCS.
        
        Args:
            source_path: GCS source path.
            content: File content bytes (unused except for context).
            blob: GCS Blob object.
            cache_path: GCS cache path for markdown.
            dry_run: If True, don't write to GCS cache.
        
        Returns:
            DoclingDocument or None on failure.
        """
        temp_path = None
        try:
            rss0 = _get_rss_gb()
            temp_path = self.gcs_client.download_blob_to_temp(blob)
            start_time = time.time()
            doc = self.extractor.extract_to_document(temp_path)
            elapsed = time.time() - start_time
            rss1 = _get_rss_gb()
            
            # Cache DoclingDocument JSON
            if not dry_run:
                try:
                    doc_json = doc.model_dump_json()
                    json_mb = len(doc_json) / 1024 / 1024
                    self.gcs_client.upload_docling_json(source_path, doc_json)
                    del doc_json
                    logger.info(
                        f"[{_short_path(source_path)}] Uploaded DoclingDocument JSON "
                        f"to {cache_path}.docling.json ({json_mb:.1f} MB)"
                    )
                except Exception as e:
                    json_mb = 0
                    logger.warning(f"[{_short_path(source_path)}] Failed to cache DoclingDocument JSON: {e}")
                
                # Also cache markdown
                try:
                    markdown = doc.export_to_markdown()
                    self.gcs_client.upload_markdown(cache_path, markdown)
                    del markdown
                except Exception as e:
                    logger.warning(f"[{_short_path(source_path)}] Failed to cache markdown: {e}")
            else:
                json_mb = 0

            rss2 = _get_rss_gb()
            logger.info(
                f"[{_short_path(source_path)}] Docling pipeline trace: "
                f"extract={elapsed:.1f}s, RSS before={rss0:.1f} after_extract={rss1:.1f} "
                f"after_cache={rss2:.1f} GB"
            )
            
            return doc
        except Exception as e:
            logger.error(f"[{_short_path(source_path)}] Docling extraction failed: {e}")
            self._log_error(source_path, f"Docling extraction failed: {e}")
            return None
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
    
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
