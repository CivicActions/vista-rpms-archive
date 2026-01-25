"""Logging configuration for the pipeline.

Provides:
- Console output with progress summaries
- Full log file for all messages
- Separate error log file
- Filtered httpx/urllib3 noise
- Standardized log format with file paths
"""

import logging
import sys
from pathlib import Path
from typing import Optional


class PathContextFilter(logging.Filter):
    """Filter that adds current_path context to log records."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        # Ensure current_path attribute exists
        if not hasattr(record, 'current_path'):
            record.current_path = ''
        return True


class HttpxFilter(logging.Filter):
    """Filter to simplify httpx log messages by removing verbose URLs."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == 'httpx':
            # Simplify HTTP request logs
            msg = record.getMessage()
            if 'HTTP Request:' in msg:
                # Extract method and status only
                # Format: "HTTP Request: GET https://... "HTTP/1.1 200 OK""
                parts = msg.split('"')
                if len(parts) >= 3:
                    method_url = parts[0].replace('HTTP Request:', '').strip()
                    method = method_url.split()[0] if method_url else 'REQUEST'
                    status = parts[-2] if len(parts) > 2 else ''
                    # Extract endpoint from URL
                    if '/' in method_url:
                        url_parts = method_url.split('/')
                        # Get last meaningful path segment
                        endpoint = '/'.join(url_parts[-2:]) if len(url_parts) > 1 else url_parts[-1]
                        record.msg = f"Qdrant {method} /{endpoint} {status}"
                    else:
                        record.msg = f"Qdrant {method} {status}"
                    record.args = ()
        return True


class Urllib3Filter(logging.Filter):
    """Filter to reduce urllib3 connection pool warnings."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == 'urllib3.connectionpool':
            # Suppress connection pool full warnings (expected with parallel processing)
            if 'Connection pool is full' in record.getMessage():
                return False
        return True


class ConsoleSummaryHandler(logging.StreamHandler):
    """Handler that tracks processed items and outputs periodic summaries."""
    
    def __init__(self, summary_interval: int = 50):
        super().__init__(sys.stdout)
        self.summary_interval = summary_interval
        self._item_count = 0
        self._progress_tracker = None
    
    def set_progress_tracker(self, tracker) -> None:
        """Set the progress tracker for summary output."""
        self._progress_tracker = tracker
    
    def emit(self, record: logging.LogRecord) -> None:
        # Count items being processed (look for key log patterns)
        msg = record.getMessage()
        if any(x in msg for x in ['Indexed', 'Skipped (', 'Skipped binary', 'Skipped non-indexable']):
            self._item_count += 1
            
            # Output summary every N items
            if self._item_count % self.summary_interval == 0 and self._progress_tracker:
                # Don't emit the regular message, emit summary instead
                super().emit(record)
                self._emit_summary()
                return
        
        super().emit(record)
    
    def _emit_summary(self) -> None:
        """Emit a progress summary to console."""
        if self._progress_tracker:
            summary = self._progress_tracker.get_progress_str()
            # Create a synthetic log record for the summary
            record = logging.LogRecord(
                name='pipeline.progress',
                level=logging.INFO,
                pathname='',
                lineno=0,
                msg=f"\n{'='*60}\nProgress: {summary}\n{'='*60}",
                args=(),
                exc_info=None,
            )
            record.current_path = ''
            super().emit(record)


class FileOnlyFilter(logging.Filter):
    """Filter that excludes progress summary lines from file output."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # Exclude progress summaries and separator lines from file
        if record.name == 'pipeline.progress':
            return False
        if '=' * 30 in msg:  # Separator lines
            return False
        return True


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    error_file: Optional[Path] = None,
    summary_interval: int = 50,
) -> ConsoleSummaryHandler:
    """Configure logging with console, file, and error handlers.
    
    Args:
        level: Log level for console output.
        log_file: Path for full log file (all levels).
        error_file: Path for error-only log file.
        summary_interval: How often to output progress summaries to console.
    
    Returns:
        The console handler (for setting progress tracker later).
    """
    # Clear any existing handlers
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)  # Capture everything, filter per handler
    
    # Standard format for file logs (includes path context)
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # Compact format for console
    console_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    
    # Console handler with summary support
    console_handler = ConsoleSummaryHandler(summary_interval=summary_interval)
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    console_handler.setFormatter(console_format)
    console_handler.addFilter(PathContextFilter())
    console_handler.addFilter(HttpxFilter())
    console_handler.addFilter(Urllib3Filter())
    root.addHandler(console_handler)
    
    # Full log file handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_format)
        file_handler.addFilter(PathContextFilter())
        file_handler.addFilter(FileOnlyFilter())  # No progress summaries
        root.addHandler(file_handler)
    
    # Error log file handler
    if error_file:
        error_file.parent.mkdir(parents=True, exist_ok=True)
        error_handler = logging.FileHandler(error_file, mode='a', encoding='utf-8')
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_format)
        error_handler.addFilter(PathContextFilter())
        root.addHandler(error_handler)
    
    # Reduce noise from third-party libraries
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('google.auth').setLevel(logging.WARNING)
    logging.getLogger('google.cloud').setLevel(logging.WARNING)
    
    return console_handler


def get_path_logger(base_logger: logging.Logger, path: str) -> logging.LoggerAdapter:
    """Get a logger adapter that includes the file path in all messages.
    
    Args:
        base_logger: The base logger to adapt.
        path: The file path to include in messages.
    
    Returns:
        LoggerAdapter that prefixes messages with the path.
    """
    # Shorten path for readability - remove common prefix
    short_path = path
    if short_path.startswith('source/'):
        short_path = short_path[7:]  # Remove 'source/' prefix
    
    return logging.LoggerAdapter(base_logger, {'current_path': short_path})
