"""Archive extraction utilities for ZIP and TAR archives."""

import logging
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# Keep IndexEntry import as optional for backward compatibility during transition
try:
    from .index_loader import has_office_extension
    from .types import ArchiveEntry, IndexEntry
    HAS_INDEX_LOADER = True
except ImportError:
    HAS_INDEX_LOADER = False
    # Define a stub for has_office_extension when index_loader is removed
    def has_office_extension(name: str) -> bool:
        """Check if file has an office document extension."""
        ext = Path(name).suffix.lower()
        return ext in {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.rtf', '.html', '.htm'}

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
    
    def extract_office_files_by_list(
        self,
        archive_path: Path,
        office_files: list[str],
    ) -> Iterator[tuple[str, Path]]:
        """Extract specific office documents from an archive by filename list.
        
        Args:
            archive_path: Path to downloaded archive file.
            office_files: List of file paths within archive to extract.
        
        Yields:
            Tuple of (relative_path_in_archive, extracted_file_path)
        """
        if not self._temp_dir:
            raise RuntimeError("ArchiveExtractor must be used as context manager")
        
        if not office_files:
            logger.debug(f"No office files to extract from {archive_path}")
            return
        
        logger.debug(f"Extracting {len(office_files)} office files from {archive_path}")
        
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
    
    def extract_all_files(
        self,
        archive_path: Path,
    ) -> Iterator[tuple[str, Path, bytes]]:
        """Extract all files from an archive (scanning archive directly).
        
        This method does NOT depend on index.json metadata - it scans the
        archive directly to find all files. Used by the GCS-first pipeline
        architecture.
        
        Args:
            archive_path: Path to downloaded archive file.
        
        Yields:
            Tuple of (relative_path_in_archive, extracted_file_path, content_bytes)
            for each non-directory file in the archive.
        """
        if not self._temp_dir:
            raise RuntimeError("ArchiveExtractor must be used as context manager")
        
        lower_path = str(archive_path).lower()
        
        if lower_path.endswith(".zip"):
            yield from self._extract_all_from_zip(archive_path)
        elif lower_path.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
            yield from self._extract_all_from_tar(archive_path)
        else:
            # Try ZIP first, then TAR
            try:
                yield from self._extract_all_from_zip(archive_path)
            except zipfile.BadZipFile:
                try:
                    yield from self._extract_all_from_tar(archive_path)
                except tarfile.TarError:
                    logger.error(f"Cannot extract {archive_path}: unknown archive format")
    
    def _extract_all_from_zip(
        self,
        archive_path: Path,
    ) -> Iterator[tuple[str, Path, bytes]]:
        """Extract all files from a ZIP archive.
        
        Args:
            archive_path: Path to ZIP file.
        
        Yields:
            Tuple of (relative_path, extracted_path, content_bytes).
        """
        try:
            with zipfile.ZipFile(archive_path, 'r') as zf:
                for member in zf.namelist():
                    # Skip directories
                    if member.endswith('/'):
                        continue
                    
                    # Skip hidden files and common non-content files
                    if self._should_skip_archive_member(member):
                        continue
                    
                    # Create safe extraction path
                    safe_name = self._safe_filename(member)
                    extract_path = self._temp_dir / safe_name
                    extract_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    try:
                        # Extract and read content
                        with zf.open(member) as source:
                            content = source.read()
                            with open(extract_path, 'wb') as target:
                                target.write(content)
                        
                        yield (member, extract_path, content)
                    
                    except Exception as e:
                        logger.warning(f"Failed to extract {member} from ZIP: {e}")
        
        except zipfile.BadZipFile as e:
            logger.error(f"Invalid ZIP file {archive_path}: {e}")
            raise
    
    def _extract_all_from_tar(
        self,
        archive_path: Path,
    ) -> Iterator[tuple[str, Path, bytes]]:
        """Extract all files from a TAR archive.
        
        Args:
            archive_path: Path to TAR file.
        
        Yields:
            Tuple of (relative_path, extracted_path, content_bytes).
        """
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
                    # Skip directories
                    if member.isdir():
                        continue
                    
                    # Skip hidden files and common non-content files
                    if self._should_skip_archive_member(member.name):
                        continue
                    
                    # Create safe extraction path
                    safe_name = self._safe_filename(member.name)
                    extract_path = self._temp_dir / safe_name
                    extract_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    try:
                        # Extract and read content
                        source = tf.extractfile(member)
                        if source is None:
                            continue
                        
                        content = source.read()
                        source.close()
                        
                        with open(extract_path, 'wb') as target:
                            target.write(content)
                        
                        yield (member.name, extract_path, content)
                    
                    except Exception as e:
                        logger.warning(f"Failed to extract {member.name} from TAR: {e}")
        
        except tarfile.TarError as e:
            logger.error(f"Invalid TAR file {archive_path}: {e}")
            raise
    
    def _should_skip_archive_member(self, name: str) -> bool:
        """Check if an archive member should be skipped.
        
        Filters out OS metadata files, hidden files, and other non-content.
        
        Args:
            name: File name/path within archive.
        
        Returns:
            True if file should be skipped.
        """
        # Get basename for checks
        basename = Path(name).name
        
        # Skip hidden files
        if basename.startswith('.'):
            return True
        
        # Skip macOS resource forks
        if '__MACOSX' in name or basename.startswith('._'):
            return True
        
        # Skip Windows metadata
        if basename in ('Thumbs.db', 'desktop.ini'):
            return True
        
        # Skip common non-content patterns
        skip_patterns = {
            'zone.identifier',  # Windows zone identifier
            '.ds_store',
        }
        if basename.lower() in skip_patterns:
            return True
        
        return False
    
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
