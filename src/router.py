"""Document-to-collection routing based on path patterns.

Routes documents to Qdrant collections based on configurable pattern matching.
"""

import logging
import re
from typing import Optional

from .types import RoutingRule

logger = logging.getLogger(__name__)


class Router:
    """Routes documents to Qdrant collections based on path patterns.
    
    Routing rules are evaluated in order; first match wins.
    If no rules match, uses the default collection.
    """
    
    def __init__(
        self,
        rules: list[RoutingRule],
        default_collection: str = "vista",
    ) -> None:
        """Initialize router with routing rules.
        
        Args:
            rules: List of RoutingRule objects (evaluated in order).
            default_collection: Collection to use if no rules match.
        """
        self.rules = rules
        self.default_collection = default_collection
        logger.debug(
            f"Router initialized with {len(rules)} rules, "
            f"default collection: {default_collection}"
        )
    
    def route(self, source_path: str) -> str:
        """Determine the target collection for a document.
        
        Evaluates routing rules in order; returns collection from first match.
        If no rules match, returns the default collection.
        
        Args:
            source_path: GCS source path of the document.
        
        Returns:
            Name of the target Qdrant collection.
        """
        for rule in self.rules:
            if self._matches(source_path, rule.pattern):
                logger.debug(
                    f"Path '{source_path}' matches pattern '{rule.pattern}' "
                    f"-> collection '{rule.collection}'"
                )
                return rule.collection
        
        logger.debug(
            f"Path '{source_path}' matched no rules -> "
            f"default collection '{self.default_collection}'"
        )
        return self.default_collection
    
    def _matches(self, path: str, pattern: str) -> bool:
        """Check if a path matches a pattern.
        
        Supports both substring matching and regex patterns.
        Patterns starting with ^ or ending with $ are treated as regex.
        Otherwise, performs case-insensitive substring matching.
        
        Args:
            path: Path to check.
            pattern: Pattern to match against.
        
        Returns:
            True if path matches the pattern.
        """
        # Detect if pattern is a regex (starts with ^ or ends with $)
        if pattern.startswith("^") or pattern.endswith("$"):
            try:
                return bool(re.search(pattern, path, re.IGNORECASE))
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{pattern}': {e}")
                return False
        
        # Default: case-insensitive substring match
        return pattern.lower() in path.lower()
    
    def route_with_category(
        self,
        source_path: str,
        is_source_code: bool = False,
    ) -> str:
        """Route document with optional source code category handling.
        
        Source code files are routed to *-source collections instead of
        the main documentation collections.
        
        Args:
            source_path: GCS source path of the document.
            is_source_code: If True, route to source collection variant.
        
        Returns:
            Name of the target Qdrant collection.
        """
        # Get base collection from normal routing
        base_collection = self.route(source_path)
        
        # If source code, append "-source" suffix
        if is_source_code:
            source_collection = f"{base_collection}-source"
            logger.debug(
                f"Source code '{source_path}' -> '{source_collection}'"
            )
            return source_collection
        
        return base_collection
    
    @classmethod
    def from_config(
        cls,
        routing_config: list[RoutingRule],
        default_collection: str = "vista",
    ) -> "Router":
        """Create router from configuration.
        
        Args:
            routing_config: List of RoutingRule objects.
            default_collection: Default collection name.
        
        Returns:
            Configured Router instance.
        """
        return cls(rules=routing_config, default_collection=default_collection)
