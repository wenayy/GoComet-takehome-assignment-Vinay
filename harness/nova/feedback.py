"""feedback: keep a person's decisions, with the batch they came after, so later reports can apply them."""
from __future__ import annotations

import json
from pathlib import Path

from .store import Store


def take_feedback(feedback_file: str, state_dir: str) -> None:
    store = Store(state_dir)
    data = json.loads(Path(feedback_file).read_text())
    kept = store.feedback()
    seen = {i["item_id"] for i in kept}
    for item in data.get("items", []):
        if item.get("item_id") in seen:
            continue
        kept.append(item | {"_batch": data.get("after_batch") or store.last_batch(),
                            "_decided_at": data.get("decided_at"), "_decided_by": data.get("decided_by")})
    store.save_feedback(kept)
