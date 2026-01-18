"""Archive extraction utilities for ZIP and TAR archives."""

import logging
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator, Optional

from .index_loader import has_office_extension
from .types import ArchiveEntry, IndexEntry

logger = logging.getLogger(__name__)


class ArchiveExtractor:
    """Handles extraction of files from ZIP and TAR archives."""
    
    def __init__(self, temp_dir: Optional[Path] = None):
        """Initialize archive extractor.
        
        Args:
            temp_dir: Base directory for temporary extraction. If None, uses system temp.
        """
        self._temp_base = temp_dir
        self._temp_dir: Optional[Path] = None
    
    def __enter__(self) -> "ArchiveExtractor":
        """Context manager entry - create temp directory."""
        self._temp_dir = Path(tempfile.mkdtemp(
            prefix="archive_extract_",
            dir=self._temp_base,
        ))
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - clean up temp directory."""
        if self._temp_dir and self._temp_dir.exists():
            try:
                shutil.rmtree(self._temp_dir)
                logger.debug(f"Cleaned up temp directory: {self._temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp directory {self._temp_dir}: {e}")
    
    def extract_office_files(
        self,
        archive_path: Path,
        entry: IndexEntry,
    ) -> Iterator[tuple[str, Path]]:
        """Extract office documents from an archive.
        
        Uses index.json archive_contents to determine which files to extract,
        rather than scanning the archive ourselves.
        
        Args:
            archive_path: Path to downloaded archive file
            entry: IndexEntry with archive_contents metadata
        
        Yields:
            Tuple of (relative_path_in_archive, extracted_file_path)
        """
        if not self._temp_dir:
            raise RuntimeError("ArchiveExtractor must be used as context manager")
        
        # Get list of office files to extract from index metadata
        if not entry.archive_contents:
            logger.debug(f"No archive_contents for {entry.path}")
            return
        
        office_files = [
            ae.name for ae in entry.archive_contents
            if not ae.is_dir and has_office_extension(ae.name)
        ]
        
        if not office_files:
            logger.debug(f"No office files in archive {entry.path}")
            return
        
        logger.debug(f"Extracting {len(office_files)} office files from {entry.path}")
        
        # Extract based on archive type
        lower_path = str(archive_path).lower()
        
        if lower_path.endswith(".zip"):
            yield from self._extract_from_zip(archive_path, office_files)
        elif lower_path.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
            yield from self._extract_from_tar(archive_path, office_files)
        else:
            # Try ZIP first, then TAR
            try:
                yield from self._extract_from_zip(archive_path, office_files)
            except zipfile.BadZipFile:
                try:
                    yield from self._extract_from_tar(archive_path, office_files)
                except tarfile.TarError:
                    logger.error(f"Cannot extract {archive_path}: unknown archive format")
    
    def _extract_from_zip(
        self,
        archive_path: Path,
        target_files: list[str],
    ) -> Iterator[tuple[str, Path]]:
        """Extract specific files from a ZIP archive.
        
        Args:
            archive_path: Path to ZIP file
            target_files: List of file names/paths to extract
        
        Yields:
            Tuple of (relative_path_in_archive, extracted_file_path)
        """
        target_set = set(target_files)
        
        try:
            with zipfile.ZipFile(archive_path, 'r') as zf:
                for member in zf.namelist():
                    # Check if this file should be extracted
                    if member not in target_set:
                        continue
                    
                    # Skip directories
                    if member.endswith('/'):
                        continue
                    
                    # Create safe extraction path
                    safe_name = self._safe_filename(member)
                    extract_path = self._temp_dir / safe_name
                    extract_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    try:
                        # Extract single file
                        with zf.open(member) as source:
                            with open(extract_path, 'wb') as target:
                                shutil.copyfileobj(source, target)
                        
                        yield (member, extract_path)
                    
                    except Exception as e:
                        logger.warning(f"Failed to extract {member} from ZIP: {e}")
        
        except zipfile.BadZipFile as e:
            logger.error(f"Invalid ZIP file {archive_path}: {e}")
            raise
    
    def _extract_from_tar(
        self,
        archive_path: Path,
        target_files: list[str],
    ) -> Iterator[tuple[str, Path]]:
        """Extract specific files from a TAR archive.
        
        Args:
            archive_path: Path to TAR file (can be .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz)
            target_files: List of file names/paths to extract
        
        Yields:
            Tuple of (relative_path_in_archive, extracted_file_path)
        """
        target_set = set(target_files)
        
        # Determine mode based on extension
        lower_path = str(archive_path).lower()
        if lower_path.endswith('.gz') or lower_path.endswith('.tgz'):
            mode = 'r:gz'
        elif lower_path.endswith('.bz2'):
            mode = 'r:bz2'
        elif lower_path.endswith('.xz'):
            mode = 'r:xz'
        else:
            mode = 'r'
        
        try:
            with tarfile.open(archive_path, mode) as tf:
                for member in tf.getmembers():
                    # Check if this file should be extracted
                    if member.name not in target_set:
                        continue
                    
                    # Skip directories
                    if member.isdir():
                        continue
                    
                    # Create safe extraction path
                    safe_name = self._safe_filename(member.name)
                    extract_path = self._temp_dir / safe_name
                    extract_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    try:
                        # Extract single file
                        source = tf.extractfile(member)
                        if source is None:
                            continue
                        
                        with open(extract_path, 'wb') as target:
                            shutil.copyfileobj(source, target)
                        source.close()
                        
                        yield (member.name, extract_path)
                    
                    except Exception as e:
                        logger.warning(f"Failed to extract {member.name} from TAR: {e}")
        
        except tarfile.TarError as e:
            logger.error(f"Invalid TAR file {archive_path}: {e}")
            raise
    
    def _safe_filename(self, name: str) -> str:
        """Create a safe filename for extraction.
        
        Prevents path traversal attacks and normalizes path separators.
        
        Args:
            name: Original file name/path from archive
        
        Returns:
            Safe filename suitable for local extraction
        """
        # Normalize path separators
        safe = name.replace('\\', '/')
        
        # Remove leading slashes and parent traversals
        parts = []
        for part in safe.split('/'):
            if part and part != '.' and part != '..':
                parts.append(part)
        
        return '/'.join(parts) if parts else 'unnamed'
