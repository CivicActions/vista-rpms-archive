"""Document extractor using docling for office document conversion."""

import logging
from pathlib import Path
from typing import Optional

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types import DoclingDocument
from docling_core.types.doc import ImageRefMode

logger = logging.getLogger(__name__)

# Image placeholder used in markdown output
IMAGE_PLACEHOLDER = "<!-- image -->"


class Extractor:
    """Document extractor using docling."""
    
    def __init__(
        self,
        max_pages: int = 500,
        do_ocr: bool = False,
        image_mode: ImageRefMode = ImageRefMode.PLACEHOLDER,
    ):
        """Initialize extractor with docling configuration.
        
        Args:
            max_pages: Maximum pages to extract (0 = unlimited)
            do_ocr: Whether to perform OCR (default: False per spec)
            image_mode: How to handle images in output (default: PLACEHOLDER)
        """
        self._max_pages = max_pages
        self._do_ocr = do_ocr
        self._image_mode = image_mode
        self._converter: Optional[DocumentConverter] = None
    
    def _get_converter(self) -> DocumentConverter:
        """Get or create the document converter (lazy initialization)."""
        if self._converter is None:
            # Configure PDF pipeline options
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = self._do_ocr
            pipeline_options.do_table_structure = True  # Keep table extraction
            
            # Create converter with format-specific options
            self._converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
            logger.debug(f"Initialized docling converter (OCR={self._do_ocr})")
        
        return self._converter
    
    def extract_to_markdown(self, file_path: Path) -> str:
        """Extract document content to markdown.
        
        Args:
            file_path: Path to document file (PDF, DOCX, etc.)
        
        Returns:
            Extracted content as markdown string
        
        Raises:
            Exception: If extraction fails
        """
        logger.debug(f"Extracting {file_path}")
        
        converter = self._get_converter()
        
        # Convert document
        result = converter.convert(str(file_path))
        
        # Export to markdown with image placeholders
        markdown = result.document.export_to_markdown(
            image_mode=self._image_mode,
            image_placeholder=IMAGE_PLACEHOLDER,
        )
        
        # Add metadata header
        header = f"<!-- Source: {file_path.name} -->\n\n"
        
        return header + markdown
    
    def extract_file(self, file_path: Path) -> str:
        """Convenience method - alias for extract_to_markdown.
        
        Args:
            file_path: Path to document file
        
        Returns:
            Extracted content as markdown string
        """
        return self.extract_to_markdown(file_path)

    def extract_to_document(self, file_path: Path) -> DoclingDocument:
        """Extract document and return the full DoclingDocument object.
        
        Unlike extract_to_markdown(), this preserves the rich document
        structure needed by HybridChunker for structure-aware chunking.
        
        Args:
            file_path: Path to document file (PDF, DOCX, etc.)
        
        Returns:
            DoclingDocument with full structural information.
        
        Raises:
            Exception: If extraction fails.
        """
        logger.debug(f"Extracting to DoclingDocument: {file_path}")
        
        converter = self._get_converter()
        result = converter.convert(str(file_path))
        
        return result.document


def create_extractor(
    max_pages: int = 500,
    do_ocr: bool = False,
) -> Extractor:
    """Create an Extractor instance with standard configuration.
    
    Args:
        max_pages: Maximum pages to extract
        do_ocr: Whether to perform OCR
    
    Returns:
        Configured Extractor instance
    """
    return Extractor(
        max_pages=max_pages,
        do_ocr=do_ocr,
        image_mode=ImageRefMode.PLACEHOLDER,
    )
