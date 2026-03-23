#!/usr/bin/env python3
"""Download IHS RPMS FTP file browser content.

The IHS RPMS FTP page (https://www.ihs.gov/rpms/applications/ftp/) uses a
dynamic JavaScript file browser with query-parameter navigation instead of
a standard directory listing. This script crawls it by parsing the HTML to
find directory forms and file download links, then downloads all files
preserving the original directory structure.

Dependencies: Python 3.11+ standard library only (no pip packages).
"""

import argparse
import html
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://www.ihs.gov/rpms/applications/ftp/"
USER_AGENT = (
    "IHSFTPArchiver/1.0"
    " (vista-rpms-archive; github.com/CivicActions/vista-rpms-archive)"
)

log = logging.getLogger("ihs-ftp")


def fetch_page(url: str, params: dict | None = None,
               max_retries: int = 3) -> str:
    """Fetch a page and return the HTML content."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                return resp.read().decode("utf-8")
        except (
            urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
        ) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                log.warning(
                    "Fetch failed (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, max_retries, e, wait,
                )
                time.sleep(wait)
    raise last_error  # type: ignore[misc]


def download_file(url: str, dest: str, max_retries: int = 3) -> bool:
    """Download a file to disk. Returns True on success."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(request, timeout=120) as resp:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
            return True
        except (
            urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
        ) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                log.warning(
                    "Download failed (attempt %d/%d): %s"
                    " — retrying in %ds",
                    attempt + 1, max_retries, e, wait,
                )
                time.sleep(wait)
    log.error("Failed to download after %d attempts: %s", max_retries,
              last_error)
    return False


def parse_directory(page_html: str) -> tuple[list[dict], list[dict]]:
    """Parse a directory page to extract subdirectories and files.

    Returns (directories, files) where each is a list of dicts.
    Directories have 'parent' and 'fld' keys.
    Files have 'path', 'filename', and 'url' keys.
    """
    dirs = []
    files = []

    # Extract directory entries from forms
    forms = re.findall(r'<form[^>]*>(.*?)</form>', page_html, re.DOTALL)
    for form in forms:
        parent = re.search(r'name="parent"\s+value="([^"]*)"', form)
        fld = re.search(r'name="fld"\s+value="([^"]*)"', form)
        if parent is not None and fld is not None:
            parent_val = html.unescape(parent.group(1))
            fld_val = html.unescape(fld.group(1))
            dirs.append({"parent": parent_val, "fld": fld_val})

    # Extract file download links from tbody
    tbody_match = re.search(
        r'<tbody>(.*?)</tbody>', page_html, re.DOTALL,
    )
    if tbody_match:
        tbody = tbody_match.group(1)
        anchors = re.findall(
            r'<a\s+[^>]*href="(\?[^"]*download=1[^"]*)"[^>]*>'
            r'([^<]*)</a>',
            tbody,
        )
        for href, filename in anchors:
            # Parse the query string to get the path
            qs = urllib.parse.parse_qs(href.lstrip("?"))
            path = qs.get("p", [""])[0]
            fname = qs.get("flname", [filename.strip()])[0]
            # Re-encode URL to handle spaces and other special chars
            url = (BASE_URL + "?"
                   + urllib.parse.urlencode(
                       {"p": path, "flname": fname, "download": "1"}))
            files.append({
                "path": path,
                "filename": fname,
                "url": url,
            })

    return dirs, files


def crawl_directory(parent: str, fld: str, delay: float,
                    depth: int = 0) -> list[dict]:
    """Recursively crawl a directory and return all files found."""
    indent = "  " * depth
    path_display = (parent + "\\" + fld) if parent else fld
    log.info("%sCrawling: %s", indent, path_display)

    params = {}
    if parent:
        params["parent"] = parent
    if fld:
        params["fld"] = fld

    html_content = fetch_page(BASE_URL, params if params else None)
    time.sleep(delay)

    dirs, files = parse_directory(html_content)

    log.info(
        "%s  Found %d subdirectories, %d files",
        indent, len(dirs), len(files),
    )

    all_files = list(files)

    for d in dirs:
        sub_files = crawl_directory(d["parent"], d["fld"], delay, depth + 1)
        all_files.extend(sub_files)

    return all_files


def path_to_local(path: str) -> str:
    """Convert a backslash-separated FTP path to a local filesystem path.

    E.g. 'rpms\\dist\\2000cert\\00-INDEX.TXT' -> 'dist/2000cert/00-INDEX.TXT'
    The leading 'rpms\\' prefix is stripped since it's implied by the
    output directory.
    """
    # Normalize separators
    normalized = path.replace("\\", "/")
    # Strip leading rpms/ prefix
    if normalized.startswith("rpms/"):
        normalized = normalized[5:]
    return normalized


def main():
    parser = argparse.ArgumentParser(
        description="Download IHS RPMS FTP files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s --dry-run                  # List files without downloading
  %(prog)s --resume                   # Download, skipping existing files
  %(prog)s --retry-errors FILE        # Retry only files from error log
  %(prog)s --delay 1                  # Slower rate limiting
""",
    )
    parser.add_argument(
        "--output-dir",
        default="www.ihs.gov/rpms/applications/ftp",
        help="Output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.5,
        help="Delay between requests in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip files that already exist locally",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List files without downloading",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--retry-errors", metavar="FILE",
        help="Retry downloading only the files listed in an error log",
    )

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.retry_errors:
        # Retry mode: read paths from error log, reconstruct URLs
        log.info("=== Retry mode: reading %s ===", args.retry_errors)
        all_files = []
        with open(args.retry_errors, encoding="utf-8") as ef:
            for line in ef:
                local_rel = line.strip()
                if not local_rel:
                    continue
                # Reconstruct the backslash-separated FTP path
                ftp_path = "rpms\\" + local_rel.replace("/", "\\")
                fname = os.path.basename(local_rel)
                url = (BASE_URL + "?"
                       + urllib.parse.urlencode(
                           {"p": ftp_path, "flname": fname,
                            "download": "1"}))
                all_files.append({
                    "path": ftp_path,
                    "filename": fname,
                    "url": url,
                })
        log.info("Files to retry: %d", len(all_files))
    else:
        # Phase 1: Crawl the directory tree
        log.info("=== Phase 1: Crawling directory tree ===")
        all_files = crawl_directory("", "", args.delay)
        log.info("Total files found: %d", len(all_files))

    if args.dry_run:
        log.info("=== Dry-run: files that would be downloaded ===")
        for f in all_files:
            local_path = path_to_local(f["path"])
            print("%s" % local_path)

        print("\n--- Summary ---")
        print("Total files: %d" % len(all_files))
        return

    # Phase 2: Download files
    log.info("=== Phase 2: Downloading files ===")
    os.makedirs(args.output_dir, exist_ok=True)

    downloaded = 0
    skipped = 0
    errors = 0
    error_files = []

    for i, f in enumerate(all_files, 1):
        local_rel = path_to_local(f["path"])
        local_path = os.path.join(args.output_dir, local_rel)

        if args.resume and os.path.exists(local_path):
            log.debug("Skipping (exists): %s", local_rel)
            skipped += 1
            continue

        log.info("[%d/%d] %s", i, len(all_files), local_rel)

        try:
            if download_file(f["url"], local_path):
                downloaded += 1
            else:
                errors += 1
                error_files.append(local_rel)
        except Exception:
            log.exception("Error downloading %s", local_rel)
            errors += 1
            error_files.append(local_rel)

        if i < len(all_files):
            time.sleep(args.delay)

    # Phase 3: Report
    log.info("=== Summary ===")
    log.info("Total files found:  %d", len(all_files))
    log.info("Downloaded:         %d", downloaded)
    log.info("Skipped (resume):   %d", skipped)
    log.info("Errors:             %d", errors)

    if error_files:
        error_log = os.path.join(args.output_dir, "_errors.log")
        with open(error_log, "w", encoding="utf-8") as ef:
            for path in error_files:
                ef.write(path + "\n")
        log.info("Error files written to: %s", error_log)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
