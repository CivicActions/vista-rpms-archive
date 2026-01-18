"""Embedding generation using FastEmbed (ONNX-based local embeddings).

This module wraps FastEmbed to generate document embeddings compatible with
Qdrant and mcp-server-qdrant.
"""

import logging
from typing import Optional

from fastembed import TextEmbedding

logger = logging.getLogger(__name__)


class Embedder:
    """Generates document embeddings using FastEmbed.
    
    Uses ONNX-based local models (no external API calls).
    Default model: sentence-transformers/all-MiniLM-L6-v2 (384 dimensions)
    """
    
    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    ) -> None:
        """Initialize embedder with specified model.
        
        Args:
            model_name: Name of the sentence-transformer model to use.
                       Must be supported by FastEmbed.
        """
        self.model_name = model_name
        self._model: Optional[TextEmbedding] = None
        logger.debug(f"Embedder initialized with model: {model_name}")
    
    @property
    def model(self) -> TextEmbedding:
        """Lazy-load the embedding model on first use."""
        if self._model is None:
            logger.info(f"Loading embedding model: {self.model_name}")
            self._model = TextEmbedding(model_name=self.model_name, threads=1)
            logger.info("Embedding model loaded successfully")
        return self._model
    
    def preload(self) -> None:
        """Preload the embedding model.
        
        Call this before starting parallel workers to avoid race conditions
        with tqdm progress bars during model download.
        """
        _ = self.model
    
    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text.
        
        Args:
            text: Text content to embed.
        
        Returns:
            List of floats representing the embedding vector (384 dimensions
            for all-MiniLM-L6-v2).
        """
        # FastEmbed returns a generator, convert to list
        embeddings = list(self.model.embed([text]))
        if not embeddings:
            raise ValueError("Failed to generate embedding")
        
        # Return as plain list of floats
        return embeddings[0].tolist()
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts.
        
        Args:
            texts: List of text contents to embed.
        
        Returns:
            List of embedding vectors.
        """
        if not texts:
            return []
        
        embeddings = list(self.model.embed(texts))
        return [emb.tolist() for emb in embeddings]
    
    @property
    def dimension(self) -> int:
        """Return the embedding dimension for the loaded model.
        
        Returns:
            Number of dimensions in the embedding vectors.
            384 for all-MiniLM-L6-v2.
        """
        # all-MiniLM-L6-v2 produces 384-dimensional embeddings
        if "all-MiniLM-L6-v2" in self.model_name:
            return 384
        # For other models, generate a test embedding to get dimension
        test_embedding = self.embed("test")
        return len(test_embedding)
