"""Reconstruct original web URLs from GCS source paths.

The GCS archive uses a domain-first path convention:
  source/{domain}/{path} -> https://{domain}/{path}

Special cases:
  - httrack-mirrored HTML files have a canonical URL in an HTML comment
  - WorldVistA GitHub repos map to https://github.com/WorldVistA/{repo}/...
"""

import logging
import re

logger = logging.getLogger(__name__)

# Regex to extract httrack "Mirrored from" URL from HTML content.
# httrack embeds: <!-- Mirrored from {url} by HTTrack ... -->
_HTTRACK_MIRROR_RE = re.compile(
    r"<!--\s*Mirrored from\s+(\S+)",
    re.IGNORECASE,
)


def resolve_source_url(source_path: str, content: str | None = None) -> str:
    """Reconstruct the original web URL from a GCS source path.

    Resolution priority:
    1. If content contains an httrack ``<!-- Mirrored from {url} -->`` comment,
       return ``https://{url}``.
    2. If the path starts with ``WorldVistA/``, construct a GitHub URL.
    3. Otherwise, treat the first path element as a domain and reconstruct
       ``https://{domain}/{remaining_path}``.

    Args:
        source_path: GCS source path (e.g. ``source/www.va.gov/vdl/doc.pdf``).
        content: Optional file content for httrack comment extraction.

    Returns:
        Reconstructed original web URL.
    """
    # Strip the leading "source/" prefix if present
    relative = source_path
    if relative.startswith("source/"):
        relative = relative[len("source/"):]

    # 1. httrack comment extraction
    if content:
        match = _HTTRACK_MIRROR_RE.search(content[:4096])  # Only scan first 4KB
        if match:
            mirrored_url = match.group(1).rstrip("/")
            # Ensure https:// prefix
            if not mirrored_url.startswith("http"):
                mirrored_url = f"https://{mirrored_url}"
            elif mirrored_url.startswith("http://"):
                mirrored_url = "https://" + mirrored_url[len("http://"):]
            logger.debug(f"Resolved URL from httrack comment: {mirrored_url}")
            return mirrored_url

    # 2. WorldVistA GitHub repos
    if relative.startswith("WorldVistA/"):
        # Pattern: WorldVistA/{repo}/{remaining_path}
        parts = relative.split("/", 2)
        if len(parts) >= 3:
            org, repo, remaining = parts[0], parts[1], parts[2]
            url = f"https://github.com/{org}/{repo}/blob/HEAD/{remaining}"
        elif len(parts) == 2:
            org, repo = parts[0], parts[1]
            url = f"https://github.com/{org}/{repo}"
        else:
            url = f"https://github.com/{relative}"
        logger.debug(f"Resolved GitHub URL: {url}")
        return url

    # 3. Domain-first reconstruction
    parts = relative.split("/", 1)
    domain = parts[0]
    remaining = parts[1] if len(parts) > 1 else ""
    url = f"https://{domain}/{remaining}" if remaining else f"https://{domain}"
    return url
