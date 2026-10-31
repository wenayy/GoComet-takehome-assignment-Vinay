"""ingest: store every document of a batch, then read facts out of the ones we haven't read before."""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor

from .docs import read_batch
from .facts import ocr_pages, read_facts
from .store import Store

WORKERS = int(os.environ.get("NOVA_WORKERS", "8"))


def ingest_batch(batch_dir: str, state_dir: str) -> None:
    store = Store(state_dir)
    batch = read_batch(batch_dir)
    docs = {d.ref: d.to_json() for d, _ in batch}
    raws = {d.ref: raw for d, raw in batch}

    def read(ref: str):
        doc = docs[ref]
        if store.facts(doc["sha"]) is not None:
            return ref, None
        try:
            if doc["scan"]:
                doc["pages"], doc["ocr"] = ocr_pages(store, raws[ref])
            email = docs.get(doc["email_ref"])
            return ref, read_facts(store, doc, email)
        except Exception as e:  # one unreadable document must not stop the batch
            print(f"ingest: could not read {ref}: {e}", file=sys.stderr)
            return ref, {"kind": "unread", "error": str(e), "ref": ref}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = dict(pool.map(read, list(docs)))
    for ref, doc in docs.items():
        store.save_doc(doc)
        if results.get(ref) is not None and results[ref].get("kind") != "unread":
            store.save_facts(doc["sha"], results[ref])
