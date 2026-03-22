#!/usr/bin/env python3
"""Download VistApedia MediaWiki content as HTML files.

Uses the MediaWiki API to enumerate all content pages, filter out spam,
and save rendered HTML for later processing by docling.

Dependencies: Python 3.11+ standard library only (no pip packages).
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://vistapedia.com/api.php"
USER_AGENT = (
    "VistApediaArchiver/1.0"
    " (vista-rpms-archive; github.com/CivicActions/vista-rpms-archive)"
)

# Default spam title patterns - catches common wiki spam regardless of author
DEFAULT_TITLE_EXCLUDE = (
    r"(?i)"
    r"Cheats?\b.*(?:Hack|Generator|Free|Unlimited|Android|Ios)"
    r"|(?:Hack|Generator).*(?:Cheats?|Free|Unlimited|Android|Ios)"
    r"|Free\s+(?:Gems|Gold|Coins|Diamonds|Credits|Cash|Crystals|CP)\s+Generator"
    r"|(?:Gems|Gold|Coins|Diamonds|Credits|Cash|Crystals)\s+Generator"
    r"|No\s+Human\s+Verification"
    r"|helpmecheat"
)

DEFAULT_BLOCKED_USERS = "Flydoc40"

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="generator" content="VistApedia MediaWiki Archive">
<meta name="source" content="https://vistapedia.com/index.php/{title_encoded}">
</head>
<body>
<h1>{title}</h1>
{content}
</body>
</html>
"""

log = logging.getLogger("vistapedia")


def api_request(params: dict, max_retries: int = 3) -> dict:
    """Make a GET request to the MediaWiki API with retry logic."""
    params["format"] = "json"
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT},
    )

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (
            urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
        ) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                log.warning(
                    "API request failed (attempt %d/%d): %s"
                    " — retrying in %ds",
                    attempt + 1, max_retries, e, wait,
                )
                time.sleep(wait)
    raise last_error  # type: ignore[misc]


def get_pages_created_by_user(username: str, delay: float) -> set[str]:
    """Get all page titles created by a specific user."""
    titles = set()
    params = {
        "action": "query",
        "list": "usercontribs",
        "ucuser": username,
        "ucshow": "new",
        "uclimit": "500",
        "ucprop": "title",
    }

    while True:
        data = api_request(params)
        for contrib in data.get("query", {}).get("usercontribs", []):
            titles.add(contrib["title"])

        cont = data.get("continue")
        if cont:
            params.update(cont)
            time.sleep(delay)
        else:
            break

    return titles


def get_all_pages(namespace: int, delay: float) -> list[dict]:
    """Enumerate all pages in a given namespace."""
    pages = []
    params = {
        "action": "query",
        "list": "allpages",
        "apnamespace": str(namespace),
        "aplimit": "500",
    }

    while True:
        data = api_request(params)
        for page in data.get("query", {}).get("allpages", []):
            pages.append(page)

        cont = data.get("continue")
        if cont:
            params.update(cont)
            time.sleep(delay)
        else:
            break

    return pages


def download_page_html(title: str) -> str | None:
    """Download the rendered HTML content of a page via the parse API."""
    data = api_request({
        "action": "parse",
        "page": title,
        "prop": "text",
        "disableeditsection": "true",
    })

    parse = data.get("parse")
    if not parse:
        error = data.get("error", {})
        log.error("Failed to parse page '%s': %s", title, error.get("info", "unknown error"))
        return None

    return parse["text"]["*"]


def sanitize_filename(title: str) -> str:
    """Convert a page title to a safe filename.

    Replaces / with ___ to preserve subpage hierarchy info.
    Strips characters that are illegal on common filesystems.
    """
    name = title.replace("/", "___")
    # Remove characters illegal on Windows/Mac/Linux filesystems
    name = re.sub(r'[<>:"|?*\\]', "_", name)
    # Collapse multiple underscores but preserve ___
    name = re.sub(r'_{4,}', "___", name)
    # Strip leading/trailing dots and spaces
    name = name.strip(". ")
    # Truncate to reasonable length (255 bytes minus .html extension)
    if len(name.encode("utf-8")) > 250:
        name = name[:250]
    return name


def build_exclusion_set(blocked_users: list[str], delay: float) -> set[str]:
    """Build set of page titles to exclude based on blocked user contributions."""
    excluded = set()
    for user in blocked_users:
        log.info("Fetching pages created by blocked user '%s'...", user)
        user_pages = get_pages_created_by_user(user, delay)
        log.info("  Found %d pages by '%s'", len(user_pages), user)
        excluded.update(user_pages)
    return excluded


def main():
    parser = argparse.ArgumentParser(
        description="Download VistApedia wiki content as HTML files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s --dry-run                  # List pages without downloading
  %(prog)s --resume                   # Download, skipping existing files
  %(prog)s --blocked-users Flydoc40,SpamBot  # Block multiple users
  %(prog)s --namespaces 0,14          # Include Category pages
""",
    )
    parser.add_argument(
        "--output-dir", default="vistapedia.com",
        help="Output directory (default: vistapedia.com)",
    )
    parser.add_argument(
        "--blocked-users", default=DEFAULT_BLOCKED_USERS,
        help="Comma-separated blocked usernames (default: %(default)s)",
    )
    parser.add_argument(
        "--title-exclude", default=DEFAULT_TITLE_EXCLUDE,
        help="Regex pattern to exclude spam page titles",
    )
    parser.add_argument(
        "--namespaces", default="0",
        help="Comma-separated namespace IDs to download (default: 0)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.5,
        help="Delay between API requests in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip pages whose output file already exists",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List pages without downloading",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Parse arguments
    blocked_users = [u.strip() for u in args.blocked_users.split(",") if u.strip()]
    namespaces = [int(ns.strip()) for ns in args.namespaces.split(",")]

    try:
        title_exclude_re = re.compile(args.title_exclude)
    except re.error as e:
        log.error("Invalid title-exclude regex: %s", e)
        sys.exit(1)

    # Phase 1: Build spam exclusion set from blocked users
    log.info("=== Phase 1: Building spam exclusion set ===")
    user_excluded_titles = build_exclusion_set(blocked_users, args.delay)
    log.info("Total pages excluded by blocked users: %d", len(user_excluded_titles))

    # Phase 2: Enumerate and filter pages
    log.info("=== Phase 2: Enumerating pages ===")
    all_pages = []
    for ns in namespaces:
        log.info("Fetching pages in namespace %d...", ns)
        pages = get_all_pages(ns, args.delay)
        log.info("  Found %d pages in namespace %d", len(pages), ns)
        all_pages.extend(pages)

    # Filter
    download_list = []
    excluded_by_user = 0
    excluded_by_pattern = 0

    for page in all_pages:
        title = page["title"]
        if title in user_excluded_titles:
            log.debug("Excluding (blocked user): %s", title)
            excluded_by_user += 1
            continue
        if title_exclude_re.search(title):
            log.debug("Excluding (title pattern): %s", title)
            excluded_by_pattern += 1
            continue
        download_list.append(page)

    log.info("Pages after filtering: %d (excluded: %d by user, %d by pattern)",
             len(download_list), excluded_by_user, excluded_by_pattern)

    # Dry-run: just list pages
    if args.dry_run:
        log.info("=== Dry-run: pages that would be downloaded ===")
        for page in download_list:
            filename = sanitize_filename(page["title"]) + ".html"
            print(f"{page['title']}  ->  {filename}")

        print("\n--- Summary ---")
        print(f"Total pages found:       {len(all_pages)}")
        print(f"Excluded by user:        {excluded_by_user}")
        print(f"Excluded by pattern:     {excluded_by_pattern}")
        print(f"Pages to download:       {len(download_list)}")
        return

    # Phase 3: Download HTML
    log.info("=== Phase 3: Downloading HTML ===")
    os.makedirs(args.output_dir, exist_ok=True)

    downloaded = 0
    skipped = 0
    errors = 0
    error_pages = []

    for i, page in enumerate(download_list, 1):
        title = page["title"]
        filename = sanitize_filename(title) + ".html"
        filepath = os.path.join(args.output_dir, filename)

        if args.resume and os.path.exists(filepath):
            log.debug("Skipping (exists): %s", title)
            skipped += 1
            continue

        log.info("[%d/%d] Downloading: %s", i, len(download_list), title)

        try:
            html_content = download_page_html(title)
            if html_content is None:
                errors += 1
                error_pages.append(title)
                continue

            title_encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
            full_html = HTML_TEMPLATE.format(
                title=title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
                title_encoded=title_encoded,
                content=html_content,
            )

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(full_html)

            downloaded += 1

        except Exception:
            log.exception("Error downloading '%s'", title)
            errors += 1
            error_pages.append(title)

        if i < len(download_list):
            time.sleep(args.delay)

    # Phase 4: Report
    log.info("=== Summary ===")
    log.info("Total pages found:       %d", len(all_pages))
    log.info("Excluded by user:        %d", excluded_by_user)
    log.info("Excluded by pattern:     %d", excluded_by_pattern)
    log.info("Downloaded:              %d", downloaded)
    log.info("Skipped (resume):        %d", skipped)
    log.info("Errors:                  %d", errors)

    if error_pages:
        error_log = os.path.join(args.output_dir, "_errors.log")
        with open(error_log, "w", encoding="utf-8") as f:
            for title in error_pages:
                f.write(title + "\n")
        log.info("Error pages written to: %s", error_log)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
