#!/usr/bin/env python3
"""Validation script for spec 004 — Docling chunking pipeline.

Creates a test collection, indexes synthetic MUMPS + document data,
then validates payload structure against qdrant-payload.md contract.
Cleans up the test collection afterward.

Usage:
    source .env && python scripts/validate_chunking.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chunker import chunk_source_code, chunk_text_fallback, _count_tokens
from src.url_resolver import resolve_source_url
from src.qdrant_client import get_content_hash, QdrantIndexer
from src.types import QdrantConfig

TEST_COLLECTION = "test-chunking-004"

# ── Sample Data ──────────────────────────────────────────────────────────

SAMPLE_MUMPS = """XUS ;ISC-SF/RAM - Kernel Signon/Security Utilities ;2024-01-15
 ;;8.0;KERNEL;;Jul 10, 2024;Build 11
 ;
EN ; Entry Point - Main signon
 N DUZ,XQY
 D ^XUS1
 Q
 ;
SETUP ; Setup Security Parameters
 S ^XTV(8989.3,1,"XUS")="SETUP"
 W !,"Security setup complete"
 Q
 ;
LOGOUT ; Logout current user
 K ^XUTL("XQ",$J)
 D CLEAN^XUSCLEAN
 Q
"""

SAMPLE_DOC = """# VistA Configuration Guide

## Chapter 1: System Setup

The VistA system requires proper configuration of the Kernel parameters.
FileMan must be initialized before other packages can function.
The system administrator should use the KIDS installer for all patches.

## Chapter 2: Security

KAAJEE (Kernel Authentication and Authorization) provides SSO capabilities.
Access/Verify codes are managed through the NEW PERSON file (#200).
Two-factor authentication can be configured via the SecurityKeys.

## Chapter 3: Integration

HL7 messaging enables communication between VistA instances.
RPCs (Remote Procedure Calls) allow client applications to interact with M routines.
The VistaLink protocol provides a Java-based connectivity layer.
"""

# ── Helpers ──────────────────────────────────────────────────────────────

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
errors = []


def check(description: str, condition: bool, detail: str = ""):
    """Assert a validation check and report."""
    if condition:
        print(f"  {PASS} {description}")
    else:
        msg = f"{description}: {detail}" if detail else description
        print(f"  {FAIL} {msg}")
        errors.append(msg)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Spec 004 — Chunking Pipeline Validation")
    print("=" * 60)

    # ── Step 1: Chunk MUMPS source code ──
    print("\n── Step 1: MUMPS Source Code Chunking ──")
    source_path = "source/WorldVistA/VistA-M/Packages/Kernel/Routines/XUS.m"
    source_url = resolve_source_url(source_path, content=SAMPLE_MUMPS)
    content_hash = get_content_hash(SAMPLE_MUMPS)

    mumps_chunks = chunk_source_code(SAMPLE_MUMPS, source_path, source_url, content_hash)
    check("MUMPS chunks produced", len(mumps_chunks) > 0, f"got {len(mumps_chunks)}")
    check("Source URL is GitHub format",
          source_url.startswith("https://github.com/WorldVistA/"),
          source_url)

    print(f"  Chunks: {len(mumps_chunks)}")
    for c in mumps_chunks:
        tokens = _count_tokens(c.text)
        print(f"    [{c.chunk_index}/{c.total_chunks}] routine={c.metadata.get('routine_name')}, "
              f"is_header={c.metadata.get('is_header')}, "
              f"lines={c.metadata.get('line_start')}-{c.metadata.get('line_end')}, "
              f"tokens={tokens}")
        check(f"Chunk {c.chunk_index} ≤ 512 tokens", tokens <= 512, f"got {tokens}")

    # Validate header chunk — header only exists if there are lines before the
    # first MUMPS label. In standard .m files, line 1 IS the routine label, so
    # there may be no header chunk. Either case is valid.
    headers = [c for c in mumps_chunks if c.metadata.get("is_header")]
    check("Header chunk count valid (0 or 1)", len(headers) <= 1, f"found {len(headers)}")
    if headers:
        h = headers[0]
        check("Header chunk_index is 0", h.chunk_index == 0)
        check("Header routine_name is _header", h.metadata.get("routine_name") == "_header")
        check("Header has line_start", h.metadata.get("line_start") is not None)
        check("Header has line_end", h.metadata.get("line_end") is not None)
    else:
        # First label IS line 1 — this is normal for MUMPS files
        check("First chunk starts at line 1 (no header needed)",
              mumps_chunks[0].metadata.get("line_start") == 1)

    # Validate routine chunks
    routines = [c for c in mumps_chunks if not c.metadata.get("is_header")]
    check("Routine chunks exist", len(routines) >= 3, f"found {len(routines)}")
    for r in routines:
        check(f"Routine {r.metadata.get('routine_name')} has language=MUMPS",
              r.metadata.get("language") == "MUMPS")
        check(f"Routine {r.metadata.get('routine_name')} has chunker=mumps-label",
              r.metadata.get("chunker") == "mumps-label")
        check(f"Routine {r.metadata.get('routine_name')} line_start ≤ line_end",
              r.metadata.get("line_start", 0) <= r.metadata.get("line_end", 0))

    # ── Step 2: Chunk document text ──
    print("\n── Step 2: Document Text Chunking (fallback) ──")
    doc_path = "source/www.va.gov/vdl/documents/infrastructure/vista_config_guide.html"
    doc_url = resolve_source_url(doc_path, content=SAMPLE_DOC)
    doc_hash = get_content_hash(SAMPLE_DOC)

    doc_chunks = chunk_text_fallback(SAMPLE_DOC, doc_path, doc_url, doc_hash)
    check("Document chunks produced", len(doc_chunks) > 0, f"got {len(doc_chunks)}")
    check("Document URL reconstructed",
          doc_url.startswith("https://www.va.gov/"),
          doc_url)

    print(f"  Chunks: {len(doc_chunks)}")
    for c in doc_chunks:
        tokens = _count_tokens(c.text)
        print(f"    [{c.chunk_index}/{c.total_chunks}] chunker={c.metadata.get('chunker')}, "
              f"tokens={tokens}, len={len(c.text)}")
        check(f"Doc chunk {c.chunk_index} ≤ 512 tokens", tokens <= 512, f"got {tokens}")

    check("Doc chunks have chunker=text-fallback",
          all(c.metadata.get("chunker") == "text-fallback" for c in doc_chunks))
    check("Doc chunks sequential indices",
          [c.chunk_index for c in doc_chunks] == list(range(len(doc_chunks))))

    # ── Step 3: Index to test collection ──
    print("\n── Step 3: Index to Qdrant Test Collection ──")
    config = QdrantConfig(
        url=os.environ["QDRANT_URL"],
        api_key=os.environ["QDRANT_API_KEY"],
        default_collection="vista",
    )
    indexer = QdrantIndexer(config=config)

    # Index MUMPS
    t0 = time.time()
    result1 = indexer.index_chunks(
        chunks=mumps_chunks,
        collection_name=TEST_COLLECTION,
        force=False,
        dry_run=False,
        file_size=len(SAMPLE_MUMPS),
        original_format=".m",
        cache_path="",
    )
    elapsed1 = time.time() - t0
    check(f"MUMPS indexing status=indexed ({elapsed1:.1f}s)",
          result1.status == "indexed", f"got {result1.status}: {result1.error}")

    # Index document
    t0 = time.time()
    result2 = indexer.index_chunks(
        chunks=doc_chunks,
        collection_name=TEST_COLLECTION,
        force=False,
        dry_run=False,
        file_size=len(SAMPLE_DOC),
        original_format=".html",
        cache_path="cache/www.va.gov/vdl/documents/infrastructure/vista_config_guide.html.md",
    )
    elapsed2 = time.time() - t0
    check(f"Document indexing status=indexed ({elapsed2:.1f}s)",
          result2.status == "indexed", f"got {result2.status}: {result2.error}")

    # ── Step 4: Verify stored payloads ──
    print("\n── Step 4: Verify Qdrant Payloads ──")
    import requests

    headers = {"api-key": os.environ["QDRANT_API_KEY"], "Content-Type": "application/json"}
    base_url = os.environ["QDRANT_URL"]

    r = requests.post(
        f"{base_url}/collections/{TEST_COLLECTION}/points/scroll",
        headers=headers,
        json={"limit": 50, "with_payload": True, "with_vector": False},
        timeout=30,
    )
    check("Scroll request succeeded", r.status_code == 200, f"HTTP {r.status_code}")

    if r.status_code == 200:
        points = r.json()["result"]["points"]
        total_expected = len(mumps_chunks) + len(doc_chunks)
        check(f"Point count = {total_expected}", len(points) == total_expected,
              f"got {len(points)}")

        # Validate common metadata fields for ALL points
        common_fields = [
            "source_path", "source_url", "content_hash", "chunk_index",
            "total_chunks", "original_format", "file_size", "cache_path",
            "chunker", "collection",
        ]

        for pt in points:
            payload = pt["payload"]
            meta = payload.get("metadata", {})

            check(f"Point {pt['id']}: has 'document' field",
                  "document" in payload and isinstance(payload["document"], str))
            check(f"Point {pt['id']}: has 'metadata' dict",
                  "metadata" in payload and isinstance(payload["metadata"], dict))
            check(f"Point {pt['id']}: has top-level 'content_hash'",
                  "content_hash" in payload)

            for field in common_fields:
                check(f"Point {pt['id']}: metadata.{field} present",
                      field in meta, f"missing from {list(meta.keys())}")

            # Check collection field
            check(f"Point {pt['id']}: collection = {TEST_COLLECTION}",
                  meta.get("collection") == TEST_COLLECTION,
                  f"got {meta.get('collection')}")

            # Source-code-specific checks
            if meta.get("chunker") == "mumps-label":
                for f in ["language", "routine_name", "is_header", "line_start", "line_end"]:
                    check(f"Point {pt['id']}: source-code field '{f}' present",
                          f in meta, f"missing from {list(meta.keys())}")
                check(f"Point {pt['id']}: language=MUMPS",
                      meta.get("language") == "MUMPS")

            # Document-specific checks
            if meta.get("chunker") == "text-fallback":
                check(f"Point {pt['id']}: text-fallback chunk has source_url",
                      meta.get("source_url", "").startswith("https://"))

        # Print sample payloads for inspection
        print("\n── Sample Payloads ──")
        for pt in points[:2]:
            meta = pt["payload"].get("metadata", {})
            print(f"  ID={pt['id']}")
            print(f"    document: {pt['payload'].get('document', '')[:80]}...")
            print(f"    metadata: {json.dumps(meta, indent=6, default=str)[:400]}")
            print()

    # ── Step 5: Re-indexing (force mode) validation ──
    print("\n── Step 5: Force Re-indexing Validation ──")
    # Re-index MUMPS with force=True
    t0 = time.time()
    force_result = indexer.index_chunks(
        chunks=mumps_chunks,
        collection_name=TEST_COLLECTION,
        force=True,
        dry_run=False,
        file_size=len(SAMPLE_MUMPS),
        original_format=".m",
        cache_path="",
    )
    elapsed_force = time.time() - t0
    check(f"Force re-index status=indexed ({elapsed_force:.1f}s)",
          force_result.status == "indexed", f"got {force_result.status}: {force_result.error}")

    # Verify same point count (no orphans)
    r2 = requests.post(
        f"{base_url}/collections/{TEST_COLLECTION}/points/scroll",
        headers=headers,
        json={"limit": 50, "with_payload": True, "with_vector": False},
        timeout=30,
    )
    if r2.status_code == 200:
        points_after = r2.json()["result"]["points"]
        check(f"Points after force re-index = {total_expected}",
              len(points_after) == total_expected,
              f"got {len(points_after)}")

    # ── Step 6: Cleanup ──
    print("\n── Step 6: Cleanup Test Collection ──")
    try:
        import requests as req
        del_resp = req.delete(
            f"{base_url}/collections/{TEST_COLLECTION}",
            headers=headers,
            timeout=30,
        )
        check(f"Deleted test collection (HTTP {del_resp.status_code})",
              del_resp.status_code == 200)
    except Exception as e:
        print(f"  Warning: cleanup failed: {e}")

    indexer.close()

    # ── Summary ──
    print("\n" + "=" * 60)
    if errors:
        print(f"RESULT: {len(errors)} FAILURES")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("RESULT: ALL CHECKS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
