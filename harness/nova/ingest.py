"""ingest: store every document of a batch, then read facts out of the ones we haven't read before."""
from __future__ import annotations

from .docs import read_batch
from .store import Store


def ingest_batch(batch_dir: str, state_dir: str) -> None:
    store = Store(state_dir)
    for doc, raw in read_batch(batch_dir):
        store.save_doc(doc.to_json())
