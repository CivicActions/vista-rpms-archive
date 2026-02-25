#!/usr/bin/env python
"""Test Qdrant search timing - simulating mcp-server-qdrant behavior."""
import time
import os

os.environ['QDRANT_URL'] = 'https://qdrant.cicd.civicactions.net'
os.environ['QDRANT_API_KEY'] = 'sTepoxe5gh8v0tXDiLdIzUpl1MCqXSNu'

print("=== MCP Server Simulation ===\n")

# Time fastembed import
start = time.time()
from fastembed import TextEmbedding
print(f"1. Import fastembed: {time.time() - start:.2f}s")

# Time model loading
start = time.time()
model = TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')
print(f"2. Load embedding model: {time.time() - start:.2f}s")

# Time qdrant client import and init
start = time.time()
from qdrant_client import QdrantClient
client = QdrantClient(
    url='https://qdrant.cicd.civicactions.net',
    api_key='sTepoxe5gh8v0tXDiLdIzUpl1MCqXSNu',
    timeout=60
)
print(f"3. Init Qdrant client: {time.time() - start:.2f}s")

# Test connection
start = time.time()
collections = client.get_collections()
print(f"4. List collections: {time.time() - start:.2f}s ({len(collections.collections)} found)")

# Time a full search (what MCP does)
print("\n=== Search Test ===\n")
start_total = time.time()

start = time.time()
query = "VistA patient registration"
embedding = list(model.embed([query]))[0].tolist()
embed_time = time.time() - start

start = time.time()
results = client.search(
    collection_name='vista',
    query_vector=('fast-all-minilm-l6-v2', embedding),
    limit=5,
    with_payload=True
)
search_time = time.time() - start
total_time = time.time() - start_total

print(f"Query: '{query}'")
print(f"Embed time: {embed_time:.3f}s")
print(f"Search time: {search_time:.3f}s")
print(f"Total time: {total_time:.3f}s")
print(f"Results: {len(results)}")

# Show potential issues
print("\n=== Diagnosis ===\n")
if total_time > 5:
    print("WARNING: Total time > 5s - may cause MCP timeout")
elif total_time > 2:
    print("NOTICE: Total time > 2s - may feel slow")
else:
    print("OK: Response time is acceptable")
    
# Check if model name matches collection vector name
print(f"\nCollection vector name: fast-all-minilm-l6-v2")
print(f"Embedding model used: sentence-transformers/all-MiniLM-L6-v2")
print("These should be compatible (fastembed maps the name)")
