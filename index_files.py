#!/usr/bin/env python3
"""
Standalone file indexer for /data directory.
Detects MIME types, file sizes, and lists archive contents.

Usage:
    python index_files.py /data --output index.json
    python index_files.py /data --output index.json --pretty
"""

import argparse
import json
import os
import sys
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

# python-magic for MIME detection (pip install python-magic)
try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    MAGIC_AVAILABLE = False
    print("WARNING: python-magic not installed. Install with: pip install python-magic", file=sys.stderr)
    print("         Falling back to extension-based detection only.", file=sys.stderr)


def detect_mime_type(file_path: Path) -> str:
    """Detect MIME type using libmagic."""
    if not MAGIC_AVAILABLE:
        return "application/octet-stream"
    try:
        return magic.from_file(str(file_path), mime=True)
    except Exception as e:
        return f"error: {e}"


def get_archive_contents(file_path: Path, mime_type: str) -> list[dict] | None:
    """List contents of archive files. Returns None if not an archive."""
    contents = []
    
    try:
        # ZIP files
        if mime_type == "application/zip" or file_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(file_path, 'r') as zf:
                for info in zf.infolist():
                    contents.append({
                        "name": info.filename,
                        "size": info.file_size,
                        "compressed_size": info.compress_size,
                        "is_dir": info.is_dir(),
                    })
            return contents
        
        # TAR files (including .tar.gz, .tar.bz2, etc.)
        if mime_type in ("application/x-tar", "application/gzip", "application/x-bzip2", "application/x-xz") \
           or file_path.suffix.lower() in (".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz"):
            with tarfile.open(file_path, 'r:*') as tf:
                for member in tf.getmembers():
                    contents.append({
                        "name": member.name,
                        "size": member.size,
                        "is_dir": member.isdir(),
                    })
            return contents
            
    except Exception as e:
        return [{"error": str(e)}]
    
    return None  # Not an archive


def index_file(file_path: Path, base_path: Path) -> dict:
    """Index a single file."""
    stat = file_path.stat()
    mime_type = detect_mime_type(file_path)
    
    entry = {
        "path": str(file_path.relative_to(base_path)),
        "absolute_path": str(file_path),
        "size_bytes": stat.st_size,
        "size_human": format_size(stat.st_size),
        "mime_type": mime_type,
        "extension": file_path.suffix.lower() if file_path.suffix else None,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }
    
    # Check for archive contents
    archive_contents = get_archive_contents(file_path, mime_type)
    if archive_contents is not None:
        entry["archive_contents"] = archive_contents
        entry["archive_file_count"] = len([c for c in archive_contents if not c.get("is_dir") and "error" not in c])
    
    return entry


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def index_directory(base_path: Path) -> dict:
    """Index all files in a directory recursively."""
    files = []
    errors = []
    total_size = 0
    
    all_files = list(base_path.rglob("*"))
    file_count = len([f for f in all_files if f.is_file()])
    
    print(f"Found {file_count} files to index in {base_path}", file=sys.stderr)
    
    processed = 0
    for item in all_files:
        if not item.is_file():
            continue
            
        processed += 1
        if processed % 100 == 0 or processed == file_count:
            print(f"Progress: {processed}/{file_count} files indexed", file=sys.stderr)
        
        try:
            entry = index_file(item, base_path)
            files.append(entry)
            total_size += entry["size_bytes"]
        except Exception as e:
            errors.append({
                "path": str(item.relative_to(base_path)),
                "error": str(e),
            })
            print(f"ERROR: {item}: {e}", file=sys.stderr)
    
    return {
        "index_version": "1.0",
        "indexed_at": datetime.now().isoformat(),
        "base_path": str(base_path),
        "summary": {
            "total_files": len(files),
            "total_size_bytes": total_size,
            "total_size_human": format_size(total_size),
            "error_count": len(errors),
            "archive_count": len([f for f in files if "archive_contents" in f]),
            "mime_types": count_by_key(files, "mime_type"),
            "extensions": count_by_key(files, "extension"),
        },
        "files": files,
        "errors": errors,
    }


def count_by_key(items: list[dict], key: str) -> dict[str, int]:
    """Count occurrences of each value for a given key."""
    counts = {}
    for item in items:
        value = item.get(key) or "(none)"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def main():
    parser = argparse.ArgumentParser(description="Index files in a directory")
    parser.add_argument("directory", type=Path, help="Directory to index")
    parser.add_argument("--output", "-o", type=Path, help="Output JSON file (default: stdout)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()
    
    if not args.directory.exists():
        print(f"ERROR: Directory does not exist: {args.directory}", file=sys.stderr)
        sys.exit(1)
    
    if not args.directory.is_dir():
        print(f"ERROR: Not a directory: {args.directory}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Indexing: {args.directory}", file=sys.stderr)
    index = index_directory(args.directory.resolve())
    
    # Output
    indent = 2 if args.pretty else None
    json_output = json.dumps(index, indent=indent, ensure_ascii=False)
    
    if args.output:
        args.output.write_text(json_output)
        print(f"Index written to: {args.output}", file=sys.stderr)
    else:
        print(json_output)
    
    # Summary to stderr
    print(f"\n=== Summary ===", file=sys.stderr)
    print(f"Total files: {index['summary']['total_files']}", file=sys.stderr)
    print(f"Total size: {index['summary']['total_size_human']}", file=sys.stderr)
    print(f"Archives: {index['summary']['archive_count']}", file=sys.stderr)
    print(f"Errors: {index['summary']['error_count']}", file=sys.stderr)


if __name__ == "__main__":
    main()
