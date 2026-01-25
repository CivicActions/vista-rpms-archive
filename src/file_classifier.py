"""File classification for content-based routing.

Determines whether files are documentation, source code, or MUMPS-specific
based on content analysis rather than just file extension.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import python-magic for reliable MIME detection
try:
    import magic
    HAS_MAGIC = True
except ImportError:
    HAS_MAGIC = False
    logger.warning("python-magic not available, using fallback MIME detection")


class FileCategory(Enum):
    """High-level file category for routing decisions."""

    DOCUMENTATION = "documentation"  # -> vista/rpms collections
    SOURCE_CODE = "source"           # -> vista-source/rpms-source collections
    MUMPS_ROUTINE = "mumps_routine"  # -> vista-source/rpms-source (MUMPS source code)
    MUMPS_GLOBAL = "mumps_global"    # -> vista-source/rpms-source (MUMPS global export)
    DATA = "data"                    # -> vista-source/rpms-source (CSV, JSON data files)
    BINARY = "binary"                # Skip - not text-based
    UNKNOWN = "unknown"              # Try to process as documentation


@dataclass
class ClassificationResult:
    """Result of file classification."""

    category: FileCategory
    language: Optional[str] = None  # Detected programming language
    confidence: float = 1.0         # Confidence score (0-1)
    reason: str = ""                # Human-readable explanation


# Known documentation file extensions (process as docs)
DOC_EXTENSIONS = frozenset({
    '.md', '.markdown', '.rst', '.txt', '.text',
    '.adoc', '.asciidoc', '.wiki', '.textile',
    '.readme', '.license', '.notice', '.authors',
    '.changelog', '.changes', '.news', '.todo',
    '.faq', '.history', '.install', '.copying',
})

# Known source code extensions (route to source collections)
SOURCE_EXTENSIONS = frozenset({
    # MUMPS
    '.m', '.zwr', '.gsa', '.gbl', '.ro',
    # Common languages
    '.py', '.js', '.ts', '.jsx', '.tsx',
    '.java', '.c', '.cpp', '.h', '.hpp', '.cs',
    '.go', '.rs', '.rb', '.php', '.pl', '.pm',
    '.swift', '.kt', '.kts', '.scala', '.clj',
    '.sh', '.bash', '.zsh', '.fish', '.ps1',
    '.sql', '.r', '.lua', '.vim', '.el',
    '.hs', '.ml', '.fs', '.ex', '.exs',
    '.pas', '.dpr', '.bas', '.vb', '.vbs',
    # Web
    '.css', '.scss', '.sass', '.less',
    # Config as code
    '.yaml', '.yml', '.toml', '.ini', '.cfg',
    '.xml', '.xsl', '.xslt',
    # Build files
    '.make', '.cmake', '.gradle',
})

# Data file extensions (route to source collections)
DATA_EXTENSIONS = frozenset({
    '.csv', '.tsv', '.json', '.jsonl', '.ndjson',
    '.dat', '.data', '.log',
})

# MUMPS routine header patterns
# MUMPS routines typically start with: ROUTINENAME ;comment text
MUMPS_ROUTINE_PATTERNS = [
    # Standard MUMPS routine header: ROUTINENAME ;FACILITY/AUTHOR-DESCRIPTION
    re.compile(r'^[A-Z][A-Z0-9]{1,7}\s+;', re.MULTILINE),
    # Version header: ;;1.0;PACKAGE NAME;**patch list**
    re.compile(r'^[\t ]+;;[\d.]+;[^;]+;', re.MULTILINE),
    # MUMPS commands at start of line with space prefix
    re.compile(r'^[\t ]+[SNKDWRQIFGE]\s+', re.MULTILINE),
    # Common MUMPS patterns
    re.compile(r'\$\$[A-Z]+\^[A-Z0-9]+', re.MULTILINE),  # $$FUNC^ROUTINE
    re.compile(r'D\s+[A-Z]+\^[A-Z0-9]+', re.MULTILINE),  # D LABEL^ROUTINE
]

# MUMPS global export patterns (ZWR format, %GO/%RO format)
MUMPS_GLOBAL_PATTERNS = [
    # ZWR format header
    re.compile(r'^OSEHRA ZGO Export:', re.MULTILINE),
    re.compile(r'^\d{2}-[A-Z]{3}-\d{4}\s+\d{2}:\d{2}:\d{2}\s+ZWR', re.MULTILINE),
    # Standard %GO/%RO headers
    re.compile(r'^(%GO|%RO|ZWR)\s*$', re.MULTILINE),
    # Global reference patterns: ^GLOBALNAME(subscript)=value
    re.compile(r'^\^[A-Z%][A-Z0-9]*\([^)]+\)=', re.MULTILINE),
    # Simple global with just name
    re.compile(r'^\^[A-Z%][A-Z0-9]*\(0\)=', re.MULTILINE),
]

# Binary MIME types that should be skipped (not text-indexable)
BINARY_MIME_TYPES = frozenset({
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/zip',
    'application/x-tar',
    'application/gzip',
    'application/x-bzip2',
    'application/x-7z-compressed',
    'application/x-rar-compressed',
    'application/octet-stream',
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/webp',
    'image/bmp',
    'image/tiff',
    'audio/mpeg',
    'audio/wav',
    'video/mp4',
    'video/mpeg',
    'application/x-executable',
    'application/x-sharedlib',
    'application/x-mach-binary',
})

# Text MIME types that should be processed
TEXT_MIME_TYPES = frozenset({
    'text/plain',
    'text/html',
    'text/xml',
    'text/css',
    'text/javascript',
    'text/x-python',
    'text/x-c',
    'text/x-java',
    'text/x-script.python',
    'text/x-shellscript',
    'application/json',
    'application/xml',
    'application/javascript',
    'application/x-httpd-php',
})


def detect_mime_type(content: bytes, filepath: str = "") -> str:
    """Detect MIME type using python-magic or fallback heuristics.

    Args:
        content: File content bytes.
        filepath: Optional file path for extension hints.

    Returns:
        MIME type string.
    """
    if HAS_MAGIC:
        try:
            # Use python-magic for content-based detection
            mime = magic.Magic(mime=True)
            detected = mime.from_buffer(content[:8192])
            if detected:
                return detected
        except Exception as e:
            logger.debug(f"magic detection failed: {e}")

    # Fallback: Check magic bytes for common binary formats
    if content.startswith(b'%PDF'):
        return 'application/pdf'
    if content.startswith(b'PK\x03\x04'):
        # Could be ZIP, DOCX, XLSX, etc.
        # Check for Office Open XML signatures
        if b'word/' in content[:2000]:
            return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        if b'xl/' in content[:2000]:
            return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        if b'ppt/' in content[:2000]:
            return 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        return 'application/zip'
    if content.startswith(b'\xd0\xcf\x11\xe0'):  # OLE2 (old Office formats)
        return 'application/msword'  # Could also be XLS/PPT
    if content.startswith(b'\x89PNG'):
        return 'image/png'
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if content.startswith(b'GIF8'):
        return 'image/gif'
    if content.startswith(b'\x1f\x8b'):
        return 'application/gzip'
    if content.startswith(b'BZ'):
        return 'application/x-bzip2'
    if content.startswith(b'\x7fELF'):
        return 'application/x-executable'
    if content.startswith(b'\xca\xfe\xba\xbe') or content.startswith(b'\xcf\xfa\xed\xfe'):
        return 'application/x-mach-binary'

    # Check for text content
    try:
        sample = content[:4096].decode('utf-8', errors='strict')
        # Successfully decoded as UTF-8
        if sample.strip().startswith('<!DOCTYPE html') or sample.strip().startswith('<html'):
            return 'text/html'
        if sample.strip().startswith('<?xml'):
            return 'text/xml'
        if sample.strip().startswith('{') or sample.strip().startswith('['):
            return 'application/json'
        return 'text/plain'
    except UnicodeDecodeError:
        pass

    # Check for null bytes (binary indicator)
    if b'\x00' in content[:8192]:
        return 'application/octet-stream'

    return 'text/plain'


def is_binary_content(content: bytes, sample_size: int = 8192) -> bool:
    """Check if content appears to be binary (non-text).

    Uses python-magic if available, otherwise falls back to heuristics.

    Args:
        content: Raw file content (bytes).
        sample_size: Number of bytes to check.

    Returns:
        True if content appears to be binary.
    """
    # Use MIME detection first
    mime_type = detect_mime_type(content)

    # Check against known binary types
    if mime_type in BINARY_MIME_TYPES:
        return True

    # Check against known text types
    if mime_type in TEXT_MIME_TYPES or mime_type.startswith('text/'):
        return False

    # Fallback: Check for null bytes and encoding
    sample = content[:sample_size]
    # Binary files typically contain null bytes
    if b'\x00' in sample:
        return True
    # High ratio of non-printable characters suggests binary
    try:
        sample.decode('utf-8', errors='strict')
        # If it decodes cleanly, it's probably text
        return False
    except UnicodeDecodeError:
        # Try latin-1 as fallback
        try:
            sample.decode('latin-1')
            return False
        except Exception:
            return True


def classify_by_extension(filepath: str) -> Optional[ClassificationResult]:
    """Classify file based on extension alone.

    Used for quick classification of files with known extensions.

    Args:
        filepath: Path to file.

    Returns:
        ClassificationResult if extension is recognized, None otherwise.
    """
    path = Path(filepath)
    ext = path.suffix.lower()
    name_lower = path.name.lower()

    # Check for extensionless doc files by name
    if not ext and name_lower in {'readme', 'license', 'notice', 'authors',
                                   'changelog', 'changes', 'news', 'todo',
                                   'copying', 'install', 'contributing'}:
        return ClassificationResult(
            category=FileCategory.DOCUMENTATION,
            reason=f"Documentation file by name: {path.name}",
        )

    # MUMPS files
    if ext == '.m':
        return ClassificationResult(
            category=FileCategory.MUMPS_ROUTINE,
            language="MUMPS",
            reason="MUMPS routine by .m extension",
        )

    if ext in {'.zwr', '.gsa', '.gbl'}:
        return ClassificationResult(
            category=FileCategory.MUMPS_GLOBAL,
            language="MUMPS",
            reason=f"MUMPS global export by {ext} extension",
        )

    # Known doc extensions
    if ext in DOC_EXTENSIONS:
        return ClassificationResult(
            category=FileCategory.DOCUMENTATION,
            reason=f"Documentation by {ext} extension",
        )

    # Known source extensions
    if ext in SOURCE_EXTENSIONS:
        return ClassificationResult(
            category=FileCategory.SOURCE_CODE,
            reason=f"Source code by {ext} extension",
        )

    # Known data extensions
    if ext in DATA_EXTENSIONS:
        return ClassificationResult(
            category=FileCategory.DATA,
            reason=f"Data file by {ext} extension",
        )

    return None


def classify_mumps_content(content: str) -> Optional[ClassificationResult]:
    """Detect MUMPS source code or global exports from content.

    Args:
        content: Text content to analyze.

    Returns:
        ClassificationResult if MUMPS detected, None otherwise.
    """
    # Check for MUMPS global export patterns first (more specific)
    global_matches = sum(1 for p in MUMPS_GLOBAL_PATTERNS if p.search(content))
    if global_matches >= 2:
        return ClassificationResult(
            category=FileCategory.MUMPS_GLOBAL,
            language="MUMPS",
            confidence=min(1.0, global_matches / 3),
            reason=f"MUMPS global export detected ({global_matches} pattern matches)",
        )

    # Check caret density - global exports have many ^ characters
    if content.count('^') > len(content) / 50:  # More than 2% carets
        caret_lines = sum(1 for line in content.split('\n') if line.startswith('^'))
        if caret_lines > 5:
            return ClassificationResult(
                category=FileCategory.MUMPS_GLOBAL,
                language="MUMPS",
                confidence=0.9,
                reason=f"MUMPS global export detected (high caret density, {caret_lines} ^-lines)",
            )

    # Check for MUMPS routine patterns
    routine_matches = sum(1 for p in MUMPS_ROUTINE_PATTERNS if p.search(content))
    if routine_matches >= 2:
        return ClassificationResult(
            category=FileCategory.MUMPS_ROUTINE,
            language="MUMPS",
            confidence=min(1.0, routine_matches / 3),
            reason=f"MUMPS routine detected ({routine_matches} pattern matches)",
        )

    return None


def classify_source_vs_docs(content: str, filepath: str = "") -> ClassificationResult:
    """Classify text content as source code or documentation.

    Uses heuristics and optionally pygments for language detection.

    Args:
        content: Text content to analyze.
        filepath: Optional file path for extension hints.

    Returns:
        ClassificationResult with classification.
    """
    # Try MUMPS detection first
    mumps_result = classify_mumps_content(content)
    if mumps_result:
        return mumps_result

    # Analyze content for source code indicators
    lines = content.split('\n')[:100]  # Check first 100 lines

    source_indicators = 0
    doc_indicators = 0

    for line in lines:
        stripped = line.strip()

        # Source code indicators
        if stripped.startswith(('#!', '//', '/*', '*/', '"""', "'''")):
            source_indicators += 1
        if re.match(r'^(import|from|require|include|using|package)\s+', stripped):
            source_indicators += 2
        if re.match(r'^(def|class|function|func|fn|pub|private|public|static)\s+', stripped):
            source_indicators += 2
        if re.match(r'^(if|for|while|switch|case|try|catch|return)\s*[\({]?', stripped):
            source_indicators += 1
        if re.search(r'[{};]\s*$', stripped):  # Ends with brace/semicolon
            source_indicators += 1
        if re.search(r'=>|->|\|\||&&', stripped):  # Common operators
            source_indicators += 1

        # Documentation indicators
        if re.match(r'^#+\s+', stripped):  # Markdown headers
            doc_indicators += 2
        if re.match(r'^={3,}|^-{3,}|^\*{3,}', stripped):  # RST underlines
            doc_indicators += 1
        if re.match(r'^\*\s+|\d+\.\s+|^-\s+', stripped):  # Lists
            doc_indicators += 1
        if len(stripped) > 80 and ' ' in stripped and not any(c in stripped for c in '{}();='):
            doc_indicators += 1  # Long prose lines

    # Try pygments for more accurate detection
    try:
        from pygments.lexers import get_lexer_for_filename, guess_lexer
        from pygments.util import ClassNotFound

        lexer = None
        if filepath:
            try:
                lexer = get_lexer_for_filename(filepath)
            except ClassNotFound:
                pass

        if not lexer:
            try:
                lexer = guess_lexer(content[:4096])
            except ClassNotFound:
                pass

        if lexer:
            lexer_name = lexer.name.lower()

            # Check for doc formats
            doc_formats = {'markdown', 'restructuredtext', 'text only', 'plain text'}
            if any(fmt in lexer_name for fmt in doc_formats):
                return ClassificationResult(
                    category=FileCategory.DOCUMENTATION,
                    language=lexer.name,
                    confidence=0.8,
                    reason=f"Documentation detected by pygments ({lexer.name})",
                )

            # Recognized programming language
            return ClassificationResult(
                category=FileCategory.SOURCE_CODE,
                language=lexer.name,
                confidence=0.8,
                reason=f"Source code detected by pygments ({lexer.name})",
            )
    except ImportError:
        logger.debug("pygments not available, using heuristic detection only")
    except Exception as e:
        logger.debug(f"pygments detection failed: {e}")

    # Fall back to heuristic scoring
    if source_indicators > doc_indicators * 2:
        return ClassificationResult(
            category=FileCategory.SOURCE_CODE,
            confidence=0.6,
            reason=f"Source code by heuristics (score: {source_indicators} vs {doc_indicators})",
        )
    elif doc_indicators > source_indicators:
        return ClassificationResult(
            category=FileCategory.DOCUMENTATION,
            confidence=0.6,
            reason=f"Documentation by heuristics (score: {doc_indicators} vs {source_indicators})",
        )
    else:
        # Default to documentation for plain text
        return ClassificationResult(
            category=FileCategory.DOCUMENTATION,
            confidence=0.4,
            reason="Defaulting to documentation (inconclusive analysis)",
        )


def classify_file(filepath: str, content: Optional[bytes] = None) -> ClassificationResult:
    """Classify a file by content and/or extension.

    Priority:
    1. Detect MIME type to catch mis-named files (e.g., PDF saved as .html)
    2. Check if binary (skip)
    3. Check extension for known types
    4. Analyze content for MUMPS patterns
    5. Use pygments/heuristics for source vs docs

    Args:
        filepath: Path to file.
        content: Optional file content (reads from file if not provided).

    Returns:
        ClassificationResult with file category.
    """
    # Read content if not provided
    if content is None:
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"Failed to read file {filepath}: {e}")
            return ClassificationResult(
                category=FileCategory.UNKNOWN,
                reason=f"Failed to read file: {e}",
            )

    # FIRST: Detect MIME type to catch mis-named files
    # This catches PDFs saved as .html, binaries with .tmp extension, etc.
    mime_type = detect_mime_type(content, filepath)

    # If MIME indicates binary regardless of extension, mark as binary
    if mime_type in BINARY_MIME_TYPES:
        ext = Path(filepath).suffix.lower()
        return ClassificationResult(
            category=FileCategory.BINARY,
            reason=f"Binary file detected by MIME type ({mime_type}), extension: {ext}",
        )

    # Check for binary content using heuristics as fallback
    if is_binary_content(content):
        return ClassificationResult(
            category=FileCategory.BINARY,
            reason="Binary file detected by content analysis",
        )

    # Decode text content
    try:
        text_content = content.decode('utf-8', errors='replace')
    except Exception:
        try:
            text_content = content.decode('latin-1')
        except Exception as e:
            return ClassificationResult(
                category=FileCategory.UNKNOWN,
                reason=f"Failed to decode file: {e}",
            )

    # Check extension first (fast path)
    ext_result = classify_by_extension(filepath)

    # For .m files, verify it's actually MUMPS (not Matlab/Objective-C)
    if ext_result and ext_result.category == FileCategory.MUMPS_ROUTINE:
        mumps_check = classify_mumps_content(text_content[:4096])
        mumps_types = (FileCategory.MUMPS_ROUTINE, FileCategory.MUMPS_GLOBAL)
        if mumps_check and mumps_check.category in mumps_types:
            return mumps_check
        # If content doesn't look like MUMPS, fall through to content analysis

    # For other known extensions, trust the extension
    if ext_result and ext_result.category != FileCategory.MUMPS_ROUTINE:
        return ext_result

    # Content-based classification
    return classify_source_vs_docs(text_content[:8192], filepath)


def classify_text(content: str, filepath: str = "") -> ClassificationResult:
    """Classify already-decoded text content.

    Args:
        content: Text content.
        filepath: Optional path for extension hints.

    Returns:
        ClassificationResult with file category.
    """
    # Check extension first
    if filepath:
        ext_result = classify_by_extension(filepath)
        if ext_result and ext_result.category != FileCategory.MUMPS_ROUTINE:
            return ext_result

    # Content-based classification
    return classify_source_vs_docs(content[:8192], filepath)


def is_source_category(category: FileCategory) -> bool:
    """Check if a category should route to source collections.

    Args:
        category: File category.

    Returns:
        True if should go to source collection.
    """
    return category in (
        FileCategory.SOURCE_CODE,
        FileCategory.MUMPS_ROUTINE,
        FileCategory.MUMPS_GLOBAL,
        FileCategory.DATA,
    )


def is_indexable_category(category: FileCategory) -> bool:
    """Check if a category should be indexed at all.

    Args:
        category: File category.

    Returns:
        True if should be indexed.
    """
    return category not in (FileCategory.BINARY, FileCategory.UNKNOWN)
