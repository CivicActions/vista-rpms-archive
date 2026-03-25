"""Document extractor using docling for office document conversion.

Runs each conversion in an isolated **subprocess** so that native memory
leaks (libpdfium, ONNX runtime, PyTorch) are fully reclaimed by the OS
when the child process exits.  This is the only reliable defence against
the unbounded native-memory growth observed with docling (see
docling#2559, docling#2788): a single 582 KB PDF was observed to cause
74 GB of irrecoverable native memory growth within the main process.

IMPORTANT: We use ``subprocess.Popen`` (not ``ProcessPoolExecutor``)
because PPE internally calls ``fork()`` on Linux.  With 16 active
threads in the parent, the COW pages get dirtied immediately and the
parent's RSS doubles on every fork — causing 9 GB → 28 GB → 66 GB
→ OOM.  ``subprocess.Popen`` uses ``posix_spawn`` / ``vfork+exec``
which does NOT copy the parent's address space.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from docling_core.types import DoclingDocument
from docling_core.types.doc import ImageRefMode

logger = logging.getLogger(__name__)

# Image placeholder used in markdown output
IMAGE_PLACEHOLDER = "<!-- image -->"

# Default number of conversions before recycling a thread-local converter.
DEFAULT_RECYCLE_AFTER = 50

# Timeout (seconds) for a single docling conversion in a subprocess.
# Pathological PDFs can run indefinitely; this kills the child process.
DEFAULT_CONVERSION_TIMEOUT = 600


# ── Subprocess worker script ────────────────────────────────────────
# Inlined as a string so we can run it via ``python -c`` without needing
# a separate file on disk.  The script reads its arguments from a JSON
# file, runs the conversion, and writes the DoclingDocument JSON to the
# output file.  Exit code 0 = success, non-zero = error (message on stderr).

_WORKER_SCRIPT = r'''
import json, sys, os, logging

# Suppress all docling/library logging to avoid flooding stderr.
# Errors are captured via exit code and the error file.
logging.disable(logging.CRITICAL)

args_path = sys.argv[1]
with open(args_path) as f:
    args = json.load(f)

file_path = args["file_path"]
do_ocr = args["do_ocr"]
output_path = args["output_path"]
error_path = args.get("error_path", "")

try:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = do_ocr
    pipeline_options.do_table_structure = True

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )
    result = converter.convert(file_path)
    doc_json = result.document.model_dump_json()
    with open(output_path, "w") as f:
        f.write(doc_json)
except Exception as e:
    # Write error to error file so parent can read it without pipes
    if error_path:
        with open(error_path, "w") as f:
            f.write(str(e)[:2000])
    sys.exit(1)
'''


class Extractor:
    """Document extractor using docling in subprocess isolation.

    Each conversion runs in a fresh child process via
    ``subprocess.Popen`` (NOT ``ProcessPoolExecutor``).  Popen uses
    ``posix_spawn`` / ``vfork+exec`` which does NOT copy the parent's
    address space, avoiding the catastrophic COW-page RSS explosion
    that occurs when ``fork()`` is called with many active threads.

    A threading semaphore still gates concurrency from the caller side
    so that at most ``max_concurrent`` conversions run at once.
    """

    def __init__(
        self,
        max_pages: int = 500,
        do_ocr: bool = False,
        image_mode: ImageRefMode = ImageRefMode.PLACEHOLDER,
        recycle_after: int = DEFAULT_RECYCLE_AFTER,
        max_concurrent: int = 2,
        conversion_timeout: int = DEFAULT_CONVERSION_TIMEOUT,
    ):
        self._max_pages = max_pages
        self._do_ocr = do_ocr
        self._image_mode = image_mode
        self._recycle_after = recycle_after
        self._max_concurrent = max_concurrent
        self._conversion_timeout = conversion_timeout
        self._semaphore = threading.Semaphore(max_concurrent)
        self._active_count = 0
        self._total_conversions = 0
        self._active_lock = threading.Lock()

    def _post_convert(self, file_name: str, elapsed: float) -> None:
        """Log conversion completion with RSS."""
        with self._active_lock:
            self._total_conversions += 1
            total = self._total_conversions
        try:
            import psutil
            rss_mb = psutil.Process().memory_info().rss / 1024 / 1024
            logger.info(
                f"Docling conversion #{total} complete "
                f"({file_name}, {elapsed:.1f}s, RSS={rss_mb:.0f} MB)"
            )
        except Exception:
            logger.info(
                f"Docling conversion #{total} complete "
                f"({file_name}, {elapsed:.1f}s)"
            )

    @staticmethod
    def _rss_mb() -> float:
        """Current process RSS in MB (0 on error)."""
        try:
            import psutil
            return psutil.Process().memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0

    def _convert_subprocess(self, file_path: Path) -> DoclingDocument:
        """Run a docling conversion in a subprocess and return the result.

        Uses subprocess.Popen with stdout/stderr sent to DEVNULL and
        close_fds=False so that CPython uses posix_spawn (not fork).
        Errors are communicated via a temp file, not stderr pipes —
        this prevents the parent from buffering potentially gigabytes
        of docling debug output in memory.
        """
        rss0 = self._rss_mb()

        # Create temp files for args, output, and errors
        args_fd = tempfile.NamedTemporaryFile(
            delete=False, suffix=".json", prefix="docling_args_", mode="w"
        )
        output_fd = tempfile.NamedTemporaryFile(
            delete=False, suffix=".json", prefix="docling_out_"
        )
        error_fd = tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", prefix="docling_err_"
        )
        args_path = Path(args_fd.name)
        output_path = Path(output_fd.name)
        error_path = Path(error_fd.name)
        output_fd.close()
        error_fd.close()

        try:
            # Write args for the worker script
            json.dump({
                "file_path": str(file_path),
                "do_ocr": self._do_ocr,
                "output_path": str(output_path),
                "error_path": str(error_path),
            }, args_fd)
            args_fd.close()

            # Launch child process.
            # stdout/stderr → DEVNULL: prevents the parent from buffering
            # potentially gigabytes of docling log/debug output in memory
            # (this was the cause of 59 GB RSS growth during communicate()).
            # close_fds=False: allows CPython to use posix_spawn instead of
            # fork()+exec(), avoiding COW page duplication entirely.
            proc = subprocess.Popen(
                [sys.executable, "-c", _WORKER_SCRIPT, str(args_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=False,
            )
            rss1 = self._rss_mb()
            logger.info(
                f"Subprocess spawned PID={proc.pid}, parent RSS={rss1} MB "
                f"(file={file_path.name})"
            )

            try:
                proc.wait(timeout=self._conversion_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise TimeoutError(
                    f"Docling conversion timed out after {self._conversion_timeout}s "
                    f"for {file_path.name}"
                )

            rss2 = self._rss_mb()

            if proc.returncode != 0:
                err_msg = ""
                try:
                    err_msg = error_path.read_text(errors="replace").strip()
                except Exception:
                    pass
                if not err_msg:
                    err_msg = f"exit code {proc.returncode}"
                if len(err_msg) > 500:
                    err_msg = err_msg[:500] + "..."
                raise RuntimeError(
                    f"Docling subprocess failed (exit {proc.returncode}): {err_msg}"
                )

            # Read and deserialize the DoclingDocument from the child's output
            out_size = output_path.stat().st_size
            with open(output_path, "r") as f:
                doc_json = f.read()
            if not doc_json:
                raise RuntimeError("Docling subprocess produced empty output")

            rss3 = self._rss_mb()
            doc = DoclingDocument.model_validate_json(doc_json)
            del doc_json  # free the raw JSON string
            rss4 = self._rss_mb()

            logger.info(
                f"Subprocess memory trace ({file_path.name}): "
                f"before={rss0:.0f} spawn={rss1:.0f} wait={rss2:.0f} "
                f"read={rss3:.0f} deserialize={rss4:.0f} MB, "
                f"output_json={out_size/1024/1024:.1f} MB"
            )
            return doc
        finally:
            args_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            error_path.unlink(missing_ok=True)

    def extract_to_markdown(self, file_path: Path) -> str:
        """Extract document content to markdown.

        The conversion runs in an isolated subprocess.

        Args:
            file_path: Path to document file (PDF, DOCX, etc.)

        Returns:
            Extracted content as markdown string

        Raises:
            Exception: If extraction fails or times out
        """
        logger.debug(f"Extracting {file_path}")

        self._semaphore.acquire()
        try:
            with self._active_lock:
                self._active_count += 1
                active = self._active_count
            logger.debug(
                f"Docling conversion started "
                f"({active}/{self._max_concurrent} active)"
            )
            convert_start = time.time()
            doc = self._convert_subprocess(file_path)
            elapsed = time.time() - convert_start
            self._post_convert(file_path.name, elapsed)
        finally:
            with self._active_lock:
                self._active_count -= 1
            self._semaphore.release()

        # Export to markdown with image placeholders
        markdown = doc.export_to_markdown(
            image_mode=self._image_mode,
            image_placeholder=IMAGE_PLACEHOLDER,
        )

        # Add metadata header
        header = f"<!-- Source: {file_path.name} -->\n\n"
        return header + markdown

    def extract_file(self, file_path: Path) -> str:
        """Convenience method - alias for extract_to_markdown."""
        return self.extract_to_markdown(file_path)

    def extract_to_document(self, file_path: Path) -> DoclingDocument:
        """Extract document and return the full DoclingDocument object.

        The conversion runs in an isolated subprocess.

        Args:
            file_path: Path to document file (PDF, DOCX, etc.)

        Returns:
            DoclingDocument with full structural information.

        Raises:
            Exception: If extraction fails or times out.
        """
        logger.debug(f"Extracting to DoclingDocument: {file_path}")

        self._semaphore.acquire()
        try:
            with self._active_lock:
                self._active_count += 1
                active = self._active_count
            logger.debug(
                f"Docling conversion started "
                f"({active}/{self._max_concurrent} active)"
            )
            convert_start = time.time()
            doc = self._convert_subprocess(file_path)
            elapsed = time.time() - convert_start
            self._post_convert(file_path.name, elapsed)
        finally:
            with self._active_lock:
                self._active_count -= 1
            self._semaphore.release()

        return doc

    def shutdown(self) -> None:
        """No-op (kept for API compat). No pool to shut down."""
        pass


def create_extractor(
    max_pages: int = 500,
    do_ocr: bool = False,
    recycle_after: int = DEFAULT_RECYCLE_AFTER,
    max_concurrent: int = 2,
    conversion_timeout: int = DEFAULT_CONVERSION_TIMEOUT,
) -> Extractor:
    """Create an Extractor instance with standard configuration.

    Args:
        max_pages: Maximum pages to extract
        do_ocr: Whether to perform OCR
        recycle_after: (kept for config compat, unused with subprocess isolation)
        max_concurrent: Maximum simultaneous docling conversions
        conversion_timeout: Timeout in seconds for a single conversion

    Returns:
        Configured Extractor instance
    """
    return Extractor(
        max_pages=max_pages,
        do_ocr=do_ocr,
        image_mode=ImageRefMode.PLACEHOLDER,
        recycle_after=recycle_after,
        max_concurrent=max_concurrent,
        conversion_timeout=conversion_timeout,
    )
